"""Private DynamoDB item encoding for complete game records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError

from .models import (
    DomainValidationError,
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    OddsSnapshot,
    RatingRetry,
    RatingSource,
    RatingState,
    RecordScope,
    RecordSnapshot,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
    TeamRecord,
)
from .repository import GameRepository, GameRepositoryDataError, GameRepositoryError
from .rules import can_transition_game_state
from .updates import (
    LiveStatusUpdate,
    ScheduleUpdate,
    TeamScheduleUpdate,
    UNSET,
    WriteResult,
)


_SCHEMA_VERSION = 1
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_UNKNOWN_KICKOFF_SORT = "9999-12-31T23:59:59.999999Z"
_PHASE_RANKS = {
    SeasonPhase.PRESEASON: "0",
    SeasonPhase.REGULAR_SEASON: "1",
    SeasonPhase.POSTSEASON: "2",
}


class _StaleWrite(Exception):
    """Signal that a conditional write lost its write race."""


def _format_timestamp(value: datetime) -> str:
    """Encode one UTC datetime in a fixed sortable form."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise GameRepositoryDataError("DynamoDB timestamps must be UTC")
    return value.strftime(_TIMESTAMP_FORMAT)


def _parse_timestamp(value: object, field_name: str) -> datetime:
    """Decode one fixed UTC timestamp or raise a storage data error."""
    if not isinstance(value, str):
        raise GameRepositoryDataError(f"{field_name} must be a UTC timestamp string")
    try:
        parsed = datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise GameRepositoryDataError(
            f"{field_name} must use the fixed UTC timestamp format"
        ) from error
    if parsed.strftime(_TIMESTAMP_FORMAT) != value:
        raise GameRepositoryDataError(
            f"{field_name} must use the fixed UTC timestamp format"
        )
    return parsed


def _season_key(season: int) -> str:
    """Return the index partition key for one NFL season."""
    return f"SEASON#{season}"


def _schedule_key(game: Game) -> str:
    """Return the index sort key for a game in kickoff display order."""
    phase_rank = _PHASE_RANKS[game.season_week.phase]
    kickoff_sort = (
        _format_timestamp(game.kickoff_at)
        if game.kickoff_at is not None
        else _UNKNOWN_KICKOFF_SORT
    )
    return f"{phase_rank}#{game.season_week.week:02d}#{kickoff_sort}#{game.game_id}"


def _week_schedule_prefix(season_week: SeasonWeek) -> str:
    """Return the sort-key prefix for one season phase and week."""
    return f"{_PHASE_RANKS[season_week.phase]}#{season_week.week:02d}#"


def _required_text(item: Mapping[str, object], field_name: str) -> str:
    value = item.get(field_name)
    if not isinstance(value, str) or not value:
        raise GameRepositoryDataError(f"{field_name} must be non-empty text")
    return value


def _optional_text(item: Mapping[str, object], field_name: str) -> str | None:
    if field_name not in item:
        return None
    return _required_text(item, field_name)


def _required_map(item: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    value = item.get(field_name)
    if not isinstance(value, Mapping):
        raise GameRepositoryDataError(f"{field_name} must be a map")
    return value


def _optional_map(item: Mapping[str, object], field_name: str) -> Mapping[str, object] | None:
    if field_name not in item:
        return None
    return _required_map(item, field_name)


def _required_int(item: Mapping[str, object], field_name: str) -> int:
    value = item.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise GameRepositoryDataError(f"{field_name} must be an integer number")
    integer_value = int(value)
    if value != integer_value:
        raise GameRepositoryDataError(f"{field_name} must be an integer number")
    return integer_value


def _optional_int(item: Mapping[str, object], field_name: str) -> int | None:
    if field_name not in item:
        return None
    return _required_int(item, field_name)


def _required_number(item: Mapping[str, object], field_name: str) -> float:
    value = item.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise GameRepositoryDataError(f"{field_name} must be a number")
    float_value = float(value)
    if not math.isfinite(float_value):
        raise GameRepositoryDataError(f"{field_name} must be a finite number")
    return float_value


def _optional_number(item: Mapping[str, object], field_name: str) -> float | None:
    if field_name not in item:
        return None
    return _required_number(item, field_name)


def _encode_record(record: RecordSnapshot | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "wins": record.record.wins,
        "losses": record.record.losses,
        "ties": record.record.ties,
        "scope": record.scope.value,
        "snapshot_at": _format_timestamp(record.snapshot_at),
    }


def _decode_record(item: Mapping[str, object] | None) -> RecordSnapshot | None:
    if item is None:
        return None
    try:
        return RecordSnapshot(
            record=TeamRecord(
                wins=_required_int(item, "wins"),
                losses=_required_int(item, "losses"),
                ties=_required_int(item, "ties"),
            ),
            scope=RecordScope(_required_text(item, "scope")),
            snapshot_at=_parse_timestamp(item.get("snapshot_at"), "record snapshot_at"),
        )
    except (DomainValidationError, ValueError) as error:
        raise GameRepositoryDataError("invalid stored record snapshot") from error


def _encode_team(team: TeamGameSnapshot) -> dict[str, object]:
    item: dict[str, object] = {
        "team_id": team.team_id,
        "display_name": team.display_name,
        "abbreviation": team.abbreviation,
    }
    if team.logo_key is not None:
        item["logo_key"] = team.logo_key
    pregame_record = _encode_record(team.pregame_record)
    if pregame_record is not None:
        item["pregame_record"] = pregame_record
    postgame_record = _encode_record(team.postgame_record)
    if postgame_record is not None:
        item["postgame_record"] = postgame_record
    return item


def _decode_team(item: Mapping[str, object]) -> TeamGameSnapshot:
    try:
        return TeamGameSnapshot(
            team_id=_required_text(item, "team_id"),
            display_name=_required_text(item, "display_name"),
            abbreviation=_required_text(item, "abbreviation"),
            logo_key=_optional_text(item, "logo_key"),
            pregame_record=_decode_record(_optional_map(item, "pregame_record")),
            postgame_record=_decode_record(_optional_map(item, "postgame_record")),
        )
    except DomainValidationError as error:
        raise GameRepositoryDataError("invalid stored team snapshot") from error


def _encode_status(status: GameStatus) -> dict[str, object]:
    item: dict[str, object] = {"state": status.state.value}
    if status.detail is not None:
        item["detail"] = status.detail
    if status.period is not None:
        item["period"] = status.period
    if status.clock is not None:
        item["clock"] = status.clock
    if status.score is not None:
        item["score"] = {"home": status.score.home, "away": status.score.away}
    return item


def _decode_status(item: Mapping[str, object]) -> GameStatus:
    score_item = _optional_map(item, "score")
    try:
        return GameStatus(
            state=GameState(_required_text(item, "state")),
            detail=_optional_text(item, "detail"),
            period=_optional_int(item, "period"),
            clock=_optional_text(item, "clock"),
            score=(
                Score(
                    home=_required_int(score_item, "home"),
                    away=_required_int(score_item, "away"),
                )
                if score_item is not None
                else None
            ),
        )
    except (DomainValidationError, ValueError) as error:
        raise GameRepositoryDataError("invalid stored game status") from error


def _encode_rating(rating: GameRating) -> dict[str, object]:
    retry: dict[str, object] = {"attempt_count": rating.retry.attempt_count}
    if rating.retry.next_attempt_at is not None:
        retry["next_attempt_at"] = _format_timestamp(rating.retry.next_attempt_at)
    if rating.retry.last_error is not None:
        retry["last_error"] = rating.retry.last_error

    item: dict[str, object] = {"state": rating.state.value, "retry": retry}
    if rating.score is not None:
        item["score"] = Decimal(str(rating.score))
    if rating.source is not None:
        item["source"] = rating.source.value
    if rating.model_version is not None:
        item["model_version"] = rating.model_version
    if rating.input_hash is not None:
        item["input_hash"] = rating.input_hash
    if rating.calculated_at is not None:
        item["calculated_at"] = _format_timestamp(rating.calculated_at)
    if rating.confirmed_at is not None:
        item["confirmed_at"] = _format_timestamp(rating.confirmed_at)
    return item


def _decode_rating(item: Mapping[str, object]) -> GameRating:
    retry_item = _required_map(item, "retry")
    try:
        return GameRating(
            state=RatingState(_required_text(item, "state")),
            retry=RatingRetry(
                attempt_count=_required_int(retry_item, "attempt_count"),
                next_attempt_at=(
                    _parse_timestamp(
                        retry_item.get("next_attempt_at"), "rating next_attempt_at"
                    )
                    if "next_attempt_at" in retry_item
                    else None
                ),
                last_error=_optional_text(retry_item, "last_error"),
            ),
            score=_optional_number(item, "score"),
            source=(
                RatingSource(_required_text(item, "source"))
                if "source" in item
                else None
            ),
            model_version=_optional_text(item, "model_version"),
            input_hash=_optional_text(item, "input_hash"),
            calculated_at=(
                _parse_timestamp(item.get("calculated_at"), "rating calculated_at")
                if "calculated_at" in item
                else None
            ),
            confirmed_at=(
                _parse_timestamp(item.get("confirmed_at"), "rating confirmed_at")
                if "confirmed_at" in item
                else None
            ),
        )
    except (DomainValidationError, ValueError) as error:
        raise GameRepositoryDataError("invalid stored game rating") from error


class _DynamoGameCodec:
    """Translate domain games to the private DynamoDB item shape."""

    def encode(self, game: Game) -> dict[str, object]:
        """Encode one complete game item and its index keys."""
        if game.game_id != game.espn_id:
            raise GameRepositoryDataError(
                "game_id must match espn_id for DynamoDB game storage"
            )

        item: dict[str, object] = {
            "game_id": str(game.game_id),
            "schema_version": _SCHEMA_VERSION,
            "season_key": _season_key(game.season_week.season),
            "schedule_key": _schedule_key(game),
            "season_week": {
                "season": game.season_week.season,
                "phase": game.season_week.phase.value,
                "week": game.season_week.week,
            },
            "home": _encode_team(game.home),
            "away": _encode_team(game.away),
            "status": _encode_status(game.status),
            "rating": _encode_rating(game.rating),
            "schedule_checked_at": _format_timestamp(game.schedule_checked_at),
            "schedule_updated_at": _format_timestamp(game.schedule_updated_at),
        }
        if game.nflverse_id is not None:
            item["nflverse_id"] = game.nflverse_id
        if game.kickoff_at is not None:
            item["kickoff_at"] = _format_timestamp(game.kickoff_at)
        if game.live_source_checked_at is not None:
            item["live_source_checked_at"] = _format_timestamp(game.live_source_checked_at)
        if game.live_state_updated_at is not None:
            item["live_state_updated_at"] = _format_timestamp(game.live_state_updated_at)
        if game.broadcaster is not None:
            item["broadcaster"] = game.broadcaster
        if game.odds is not None:
            item["odds"] = {
                "details": game.odds.details,
                "updated_at": _format_timestamp(game.odds.updated_at),
            }
        return item

    def decode(self, item: Mapping[str, object]) -> Game:
        """Decode one stored game item into validated immutable domain values."""
        try:
            if _required_int(item, "schema_version") != _SCHEMA_VERSION:
                raise GameRepositoryDataError("unsupported game item schema version")

            season_week_item = _required_map(item, "season_week")
            season_week = SeasonWeek(
                season=_required_int(season_week_item, "season"),
                phase=SeasonPhase(_required_text(season_week_item, "phase")),
                week=_required_int(season_week_item, "week"),
            )
            odds_item = _optional_map(item, "odds")
            odds = (
                OddsSnapshot(
                    details=_required_text(odds_item, "details"),
                    updated_at=_parse_timestamp(
                        odds_item.get("updated_at"), "odds updated_at"
                    ),
                )
                if odds_item is not None
                else None
            )
            game_id = GameId(_required_text(item, "game_id"))
            game = Game(
                game_id=game_id,
                espn_id=str(game_id),
                nflverse_id=_optional_text(item, "nflverse_id"),
                season_week=season_week,
                kickoff_at=(
                    _parse_timestamp(item.get("kickoff_at"), "kickoff_at")
                    if "kickoff_at" in item
                    else None
                ),
                home=_decode_team(_required_map(item, "home")),
                away=_decode_team(_required_map(item, "away")),
                status=_decode_status(_required_map(item, "status")),
                rating=_decode_rating(_required_map(item, "rating")),
                schedule_checked_at=_parse_timestamp(
                    item.get("schedule_checked_at"), "schedule_checked_at"
                ),
                schedule_updated_at=_parse_timestamp(
                    item.get("schedule_updated_at"), "schedule_updated_at"
                ),
                live_source_checked_at=(
                    _parse_timestamp(
                        item.get("live_source_checked_at"), "live_source_checked_at"
                    )
                    if "live_source_checked_at" in item
                    else None
                ),
                live_state_updated_at=(
                    _parse_timestamp(
                        item.get("live_state_updated_at"), "live_state_updated_at"
                    )
                    if "live_state_updated_at" in item
                    else None
                ),
                broadcaster=_optional_text(item, "broadcaster"),
                odds=odds,
            )
            if _required_text(item, "season_key") != _season_key(game.season_week.season):
                raise GameRepositoryDataError("stored season_key does not match game")
            if _required_text(item, "schedule_key") != _schedule_key(game):
                raise GameRepositoryDataError("stored schedule_key does not match game")
            return game
        except (DomainValidationError, ValueError) as error:
            raise GameRepositoryDataError("invalid stored game item") from error


class _DynamoTable(Protocol):
    """Small DynamoDB table interface used by the repository."""

    def get_item(self, **kwargs: object) -> Mapping[str, object]:
        """Return one table item."""
        ...

    def put_item(self, **kwargs: object) -> Mapping[str, object]:
        """Store one table item."""
        ...

    def query(self, **kwargs: object) -> Mapping[str, object]:
        """Return one index query page."""
        ...

    def update_item(self, **kwargs: object) -> Mapping[str, object]:
        """Update selected values on one table item."""
        ...


class DynamoGameRepository(GameRepository):
    """DynamoDB implementation of the complete-game repository contract."""

    def __init__(
        self,
        table: _DynamoTable,
        *,
        index_name: str = "season-schedule-index",
        query_page_size: int | None = None,
    ) -> None:
        if not isinstance(index_name, str) or not index_name:
            raise ValueError("index_name must be non-empty text")
        if query_page_size is not None and (
            isinstance(query_page_size, bool)
            or not isinstance(query_page_size, int)
            or query_page_size < 1
        ):
            raise ValueError("query_page_size must be a positive integer")

        self._table = table
        self._index_name = index_name
        self._query_page_size = query_page_size
        self._codec = _DynamoGameCodec()

    def create_if_absent(self, game: Game) -> bool:
        """Create one complete game without replacing an existing game."""
        try:
            self._table.put_item(
                Item=self._codec.encode(game),
                ConditionExpression="attribute_not_exists(game_id)",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise GameRepositoryError("could not create game in DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not create game in DynamoDB") from error
        return True

    def get(self, game_id: GameId) -> Game | None:
        """Read one current game by its primary identity."""
        try:
            response = self._table.get_item(
                Key={"game_id": str(game_id)},
                ConsistentRead=True,
            )
        except ClientError as error:
            raise GameRepositoryError("could not read game from DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not read game from DynamoDB") from error

        item = response.get("Item")
        if item is None:
            return None
        if not isinstance(item, Mapping):
            raise GameRepositoryDataError("DynamoDB get returned a non-map item")
        return self._codec.decode(item)

    def list_week(self, season_week: SeasonWeek) -> list[Game]:
        """Read one complete week in kickoff order from the schedule index."""
        return self._query_all(
            "season_key = :season_key AND begins_with(schedule_key, :schedule_prefix)",
            {
                ":season_key": _season_key(season_week.season),
                ":schedule_prefix": _week_schedule_prefix(season_week),
            },
        )

    def list_season(self, season: int) -> list[Game]:
        """Read one complete season in phase, week, and kickoff order."""
        return self._query_all(
            "season_key = :season_key",
            {":season_key": _season_key(season)},
        )

    def apply_schedule(self, current: Game, update: ScheduleUpdate) -> WriteResult:
        """Apply a newer schedule observation without replacing other fields."""
        if not isinstance(current, Game):
            raise DomainValidationError("current must be a Game")
        if not isinstance(update, ScheduleUpdate):
            raise DomainValidationError("update must be a ScheduleUpdate")
        if current.game_id != update.game_id:
            raise DomainValidationError("schedule update game_id must match current game")
        if update.observed_at <= current.schedule_checked_at:
            return WriteResult.STALE

        proposed = self._scheduled_game(current, update)
        status_changed = proposed.status != current.status
        if status_changed:
            if not can_transition_game_state(current.status, proposed.status.state):
                raise DomainValidationError("schedule update has an invalid status transition")
            if (
                current.live_source_checked_at is not None
                and update.observed_at <= current.live_source_checked_at
            ):
                return WriteResult.STALE
            proposed = replace(
                proposed,
                live_source_checked_at=update.observed_at,
                live_state_updated_at=update.observed_at,
            )

        schedule_changed = any(
            getattr(proposed, field_name) != getattr(current, field_name)
            for field_name in (
                "season_week",
                "kickoff_at",
                "home",
                "away",
                "broadcaster",
                "odds",
                "status",
            )
        )
        proposed = replace(
            proposed,
            schedule_checked_at=update.observed_at,
            schedule_updated_at=(
                update.observed_at if schedule_changed else current.schedule_updated_at
            ),
        )
        try:
            self._update_schedule_item(current, proposed, status_changed)
        except _StaleWrite:
            return WriteResult.STALE
        return WriteResult.APPLIED

    def apply_live_status(
        self,
        current: Game,
        update: LiveStatusUpdate,
    ) -> WriteResult:
        """Apply a newer live-status observation without replacing other fields."""
        if not isinstance(current, Game):
            raise DomainValidationError("current must be a Game")
        if not isinstance(update, LiveStatusUpdate):
            raise DomainValidationError("update must be a LiveStatusUpdate")
        if current.game_id != update.game_id:
            raise DomainValidationError("live update game_id must match current game")
        if (
            current.live_source_checked_at is not None
            and update.observed_at <= current.live_source_checked_at
        ):
            return WriteResult.STALE

        status_changed = update.status != current.status
        if status_changed and not can_transition_game_state(
            current.status,
            update.status.state,
        ):
            raise DomainValidationError("live update has an invalid status transition")

        proposed = replace(
            current,
            status=update.status,
            live_source_checked_at=update.observed_at,
            live_state_updated_at=(
                update.observed_at
                if status_changed
                else current.live_state_updated_at
            ),
        )
        try:
            self._update_live_status_item(current, proposed, status_changed)
        except _StaleWrite:
            return WriteResult.STALE
        return WriteResult.APPLIED

    def _scheduled_game(self, current: Game, update: ScheduleUpdate) -> Game:
        """Build a complete game from a current game and schedule-owned fields."""
        return replace(
            current,
            season_week=update.season_week,
            kickoff_at=update.kickoff_at,
            home=self._scheduled_team(current.home, update.home),
            away=self._scheduled_team(current.away, update.away),
            status=current.status if update.status is None else update.status,
            broadcaster=(
                current.broadcaster
                if update.broadcaster is UNSET
                else update.broadcaster
            ),
            odds=self._scheduled_odds(current.odds, update.odds),
        )

    @staticmethod
    def _scheduled_team(
        current: TeamGameSnapshot,
        update: TeamScheduleUpdate,
    ) -> TeamGameSnapshot:
        """Apply one team's explicitly supplied schedule-owned values."""
        identity_changed = current.team_id != update.team_id
        return TeamGameSnapshot(
            team_id=update.team_id,
            display_name=update.display_name,
            abbreviation=update.abbreviation,
            logo_key=(
                None
                if identity_changed and update.logo_key is UNSET
                else current.logo_key if update.logo_key is UNSET else update.logo_key
            ),
            pregame_record=DynamoGameRepository._scheduled_record(
                current.pregame_record,
                update.pregame_record,
                identity_changed=identity_changed,
            ),
            postgame_record=DynamoGameRepository._scheduled_record(
                current.postgame_record,
                update.postgame_record,
                identity_changed=identity_changed,
            ),
        )

    @staticmethod
    def _scheduled_record(
        current: RecordSnapshot | None,
        update: object,
        *,
        identity_changed: bool,
    ) -> RecordSnapshot | None:
        """Keep a newer stored record unless the team identity changed."""
        if update is UNSET:
            return None if identity_changed else current
        if update is None:
            return None
        if not isinstance(update, RecordSnapshot):
            raise DomainValidationError("schedule record must be a RecordSnapshot")
        if (
            not identity_changed
            and current is not None
            and update.snapshot_at < current.snapshot_at
        ):
            return current
        return update

    @staticmethod
    def _scheduled_odds(
        current: OddsSnapshot | None,
        update: object,
    ) -> OddsSnapshot | None:
        """Keep a newer stored odds value when a schedule poll is older."""
        if update is UNSET:
            return current
        if update is None:
            return None
        if not isinstance(update, OddsSnapshot):
            raise DomainValidationError("schedule odds must be an OddsSnapshot")
        if current is not None and update.updated_at < current.updated_at:
            return current
        return update

    def _update_schedule_item(
        self,
        current: Game,
        proposed: Game,
        status_changed: bool,
    ) -> None:
        """Conditionally update schedule-owned fields and their index keys."""
        current_item = self._codec.encode(current)
        proposed_item = self._codec.encode(proposed)
        set_fields = {
            field_name: proposed_item[field_name]
            for field_name in (
                "season_key",
                "schedule_key",
                "season_week",
                "home",
                "away",
                "status",
                "schedule_checked_at",
                "schedule_updated_at",
                "live_source_checked_at",
                "live_state_updated_at",
                "kickoff_at",
                "broadcaster",
                "odds",
            )
            if field_name in proposed_item
            and proposed_item.get(field_name) != current_item.get(field_name)
        }
        remove_fields = [
            field_name
            for field_name in ("kickoff_at", "broadcaster", "odds")
            if field_name in current_item and field_name not in proposed_item
        ]

        names = {f"#{field_name}": field_name for field_name in set_fields}
        names.update({f"#{field_name}": field_name for field_name in remove_fields})
        names.update(
            {
                "#game_id": "game_id",
                "#schedule_checked_at": "schedule_checked_at",
            }
        )
        values: dict[str, object] = {
            ":expected_schedule_checked_at": current_item["schedule_checked_at"],
        }
        set_terms: list[str] = []
        for index, (field_name, value) in enumerate(set_fields.items()):
            value_name = f":set_{index}"
            values[value_name] = value
            set_terms.append(f"#{field_name} = {value_name}")

        condition_terms = [
            "attribute_exists(#game_id)",
            "#schedule_checked_at = :expected_schedule_checked_at",
        ]
        if status_changed:
            names["#status"] = "status"
            values[":expected_status"] = current_item["status"]
            condition_terms.append("#status = :expected_status")
            if current.live_source_checked_at is None:
                names["#live_source_checked_at"] = "live_source_checked_at"
                condition_terms.append("attribute_not_exists(#live_source_checked_at)")
            else:
                names["#live_source_checked_at"] = "live_source_checked_at"
                values[":expected_live_source_checked_at"] = current_item[
                    "live_source_checked_at"
                ]
                condition_terms.append(
                    "#live_source_checked_at = :expected_live_source_checked_at"
                )

        expression_parts = [f"SET {', '.join(set_terms)}"]
        if remove_fields:
            expression_parts.append(
                f"REMOVE {', '.join(f'#{field_name}' for field_name in remove_fields)}"
            )
        try:
            self._table.update_item(
                Key={"game_id": str(current.game_id)},
                UpdateExpression=" ".join(expression_parts),
                ConditionExpression=" AND ".join(condition_terms),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise _StaleWrite from error
            raise GameRepositoryError("could not update game schedule in DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not update game schedule in DynamoDB") from error

    def _update_live_status_item(
        self,
        current: Game,
        proposed: Game,
        status_changed: bool,
    ) -> None:
        """Conditionally update live-owned fields without replacing other fields."""
        current_item = self._codec.encode(current)
        proposed_item = self._codec.encode(proposed)
        set_fields = {
            "live_source_checked_at": proposed_item["live_source_checked_at"],
        }
        if status_changed:
            set_fields["status"] = proposed_item["status"]
            set_fields["live_state_updated_at"] = proposed_item[
                "live_state_updated_at"
            ]

        names = {f"#{field_name}": field_name for field_name in set_fields}
        names["#game_id"] = "game_id"
        names["#live_source_checked_at"] = "live_source_checked_at"
        values: dict[str, object] = {}
        set_terms: list[str] = []
        for index, (field_name, value) in enumerate(set_fields.items()):
            value_name = f":set_{index}"
            values[value_name] = value
            set_terms.append(f"#{field_name} = {value_name}")

        condition_terms = ["attribute_exists(#game_id)"]
        if current.live_source_checked_at is None:
            condition_terms.append("attribute_not_exists(#live_source_checked_at)")
        else:
            values[":expected_live_source_checked_at"] = current_item[
                "live_source_checked_at"
            ]
            condition_terms.append(
                "#live_source_checked_at = :expected_live_source_checked_at"
            )
        if status_changed:
            names["#status"] = "status"
            values[":expected_status"] = current_item["status"]
            condition_terms.append("#status = :expected_status")

        try:
            self._table.update_item(
                Key={"game_id": str(current.game_id)},
                UpdateExpression=f"SET {', '.join(set_terms)}",
                ConditionExpression=" AND ".join(condition_terms),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise _StaleWrite from error
            raise GameRepositoryError("could not update game status in DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not update game status in DynamoDB") from error

    def _query_all(
        self,
        key_condition_expression: str,
        expression_attribute_values: Mapping[str, str],
    ) -> list[Game]:
        query_args: dict[str, object] = {
            "IndexName": self._index_name,
            "KeyConditionExpression": key_condition_expression,
            "ExpressionAttributeValues": dict(expression_attribute_values),
            "ScanIndexForward": True,
        }
        if self._query_page_size is not None:
            query_args["Limit"] = self._query_page_size

        games: list[Game] = []
        while True:
            try:
                response = self._table.query(**query_args)
            except ClientError as error:
                raise GameRepositoryError("could not query games from DynamoDB") from error
            except BotoCoreError as error:
                raise GameRepositoryError("could not query games from DynamoDB") from error

            items = response.get("Items")
            if not isinstance(items, list):
                raise GameRepositoryDataError("DynamoDB query returned non-list items")
            for item in items:
                if not isinstance(item, Mapping):
                    raise GameRepositoryDataError("DynamoDB query returned a non-map item")
                games.append(self._codec.decode(item))

            last_evaluated_key = response.get("LastEvaluatedKey")
            if last_evaluated_key is None:
                return games
            if not isinstance(last_evaluated_key, Mapping):
                raise GameRepositoryDataError(
                    "DynamoDB query returned a non-map continuation key"
                )
            query_args["ExclusiveStartKey"] = dict(last_evaluated_key)


__all__ = ["DynamoGameRepository"]
