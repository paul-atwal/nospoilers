"""Private DynamoDB item encoding for complete game records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

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
from .repository import GameRepositoryDataError


_SCHEMA_VERSION = 1
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_UNKNOWN_KICKOFF_SORT = "9999-12-31T23:59:59.999999Z"
_PHASE_RANKS = {
    SeasonPhase.PRESEASON: "0",
    SeasonPhase.REGULAR_SEASON: "1",
    SeasonPhase.POSTSEASON: "2",
}


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


__all__ = ["_DynamoGameCodec"]
