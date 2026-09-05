"""DynamoDB implementation of the complete-game repository."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError

from .dynamodb_codec import (
    _DynamoGameCodec,
    _encode_retry,
    _encode_rating,
    _season_key,
    _week_schedule_prefix,
)
from .models import (
    DomainValidationError,
    Game,
    GameId,
    GameRating,
    GameState,
    OddsSnapshot,
    RecordSnapshot,
    RatingRetry,
    RatingState,
    SeasonWeek,
    TeamGameSnapshot,
)
from .repository import (
    GameRepository,
    GameRepositoryDataError,
    GameRepositoryError,
    NflverseIdConflictError,
    validate_rating_update,
)
from .rules import can_transition_game_state
from .updates import (
    LiveStatusUpdate,
    ScheduleUpdate,
    TeamScheduleUpdate,
    UNSET,
    WriteResult,
)


class _StaleWrite(Exception):
    """Signal that a conditional write lost its write race."""


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

    def apply_rating(self, current: Game, rating: GameRating) -> WriteResult:
        """Apply a complete rating without replacing non-rating game data."""
        validate_rating_update(current, rating)
        if rating.state is RatingState.CONFIRMED:
            return self.apply_confirmed_rating(current, rating)
        if rating == current.rating:
            return WriteResult.STALE

        try:
            self._update_rating_item(current, rating)
        except _StaleWrite:
            return WriteResult.STALE
        return WriteResult.APPLIED

    def apply_nflverse_mapping(
        self,
        current: Game,
        nflverse_id: str,
    ) -> WriteResult:
        """Persist one immutable ESPN-to-nflverse mapping."""
        if not isinstance(current, Game):
            raise DomainValidationError("current must be a Game")
        if not isinstance(nflverse_id, str) or not nflverse_id.strip():
            raise DomainValidationError("nflverse_id must be non-empty text")
        if current.nflverse_id is not None:
            if current.nflverse_id == nflverse_id:
                return WriteResult.STALE
            raise NflverseIdConflictError(
                f"game {current.game_id} is already mapped to another nflverse game"
            )

        try:
            self._update_nflverse_mapping_item(current, nflverse_id)
        except _StaleWrite:
            latest = self.get(current.game_id)
            if latest is None or latest.nflverse_id is None:
                return WriteResult.STALE
            if latest.nflverse_id != nflverse_id:
                raise NflverseIdConflictError(
                    f"game {current.game_id} is already mapped to another nflverse game"
                )
            return WriteResult.STALE
        return WriteResult.APPLIED

    def apply_confirmation_retry(
        self,
        current: Game,
        retry: RatingRetry,
    ) -> WriteResult:
        """Persist nflverse confirmation retry metadata only."""
        if not isinstance(current, Game):
            raise DomainValidationError("current must be a Game")
        if not isinstance(retry, RatingRetry):
            raise DomainValidationError("retry must be a RatingRetry")
        if current.status.state is not GameState.FINAL:
            raise DomainValidationError("confirmation retries require a final game")
        if current.rating.state is RatingState.CONFIRMED:
            raise DomainValidationError("confirmed ratings cannot have retry work")
        if retry == current.confirmation_retry:
            return WriteResult.STALE

        try:
            self._update_confirmation_retry_item(current, retry)
        except _StaleWrite:
            return WriteResult.STALE
        return WriteResult.APPLIED

    def apply_confirmed_rating(
        self,
        current: Game,
        rating: GameRating,
    ) -> WriteResult:
        """Atomically persist a confirmed rating and clear retry metadata."""
        validate_rating_update(current, rating)
        if rating.state is not RatingState.CONFIRMED:
            raise DomainValidationError("confirmed rating update requires confirmed state")
        if current.nflverse_id is None:
            raise DomainValidationError(
                "confirmed rating update requires an nflverse mapping"
            )
        if rating == current.rating and current.confirmation_retry == RatingRetry():
            return WriteResult.STALE

        try:
            self._update_confirmed_rating_item(current, rating)
        except _StaleWrite:
            return WriteResult.STALE
        return WriteResult.APPLIED

    def _update_nflverse_mapping_item(self, current: Game, nflverse_id: str) -> None:
        """Conditionally set a missing mapping without changing other fields."""
        try:
            self._table.update_item(
                Key={"game_id": str(current.game_id)},
                UpdateExpression="SET #nflverse_id = :nflverse_id",
                ConditionExpression=(
                    "attribute_exists(#game_id) AND "
                    "(attribute_not_exists(#nflverse_id) OR "
                    "#nflverse_id = :nflverse_id)"
                ),
                ExpressionAttributeNames={
                    "#game_id": "game_id",
                    "#nflverse_id": "nflverse_id",
                },
                ExpressionAttributeValues={":nflverse_id": nflverse_id},
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise _StaleWrite from error
            raise GameRepositoryError("could not update nflverse mapping in DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not update nflverse mapping in DynamoDB") from error

    def _update_confirmation_retry_item(
        self,
        current: Game,
        retry: RatingRetry,
    ) -> None:
        """Conditionally replace only the nflverse confirmation retry map."""
        current_item = self._codec.encode(current)
        try:
            self._table.update_item(
                Key={"game_id": str(current.game_id)},
                UpdateExpression="SET #confirmation_retry = :confirmation_retry",
                ConditionExpression=(
                    "attribute_exists(#game_id) AND #rating = :expected_rating "
                    "AND #status = :expected_status AND "
                    f"{self._confirmation_retry_condition(current)}"
                ),
                ExpressionAttributeNames={
                    "#game_id": "game_id",
                    "#rating": "rating",
                    "#status": "status",
                    "#confirmation_retry": "confirmation_retry",
                },
                ExpressionAttributeValues={
                    ":confirmation_retry": _encode_retry(retry),
                    ":expected_rating": current_item["rating"],
                    ":expected_status": current_item["status"],
                    ":expected_confirmation_retry": _encode_retry(
                        current.confirmation_retry
                    ),
                },
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise _StaleWrite from error
            raise GameRepositoryError("could not update confirmation retry in DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not update confirmation retry in DynamoDB") from error

    def _update_confirmed_rating_item(
        self,
        current: Game,
        rating: GameRating,
    ) -> None:
        """Conditionally replace rating and clear retry as one write."""
        current_item = self._codec.encode(current)
        try:
            self._table.update_item(
                Key={"game_id": str(current.game_id)},
                UpdateExpression=(
                    "SET #rating = :rating, #confirmation_retry = :confirmation_retry"
                ),
                ConditionExpression=(
                    "attribute_exists(#game_id) AND #rating = :expected_rating "
                    "AND #status = :expected_status "
                    "AND #nflverse_id = :expected_nflverse_id AND "
                    f"{self._confirmation_retry_condition(current)}"
                ),
                ExpressionAttributeNames={
                    "#game_id": "game_id",
                    "#rating": "rating",
                    "#status": "status",
                    "#nflverse_id": "nflverse_id",
                    "#confirmation_retry": "confirmation_retry",
                },
                ExpressionAttributeValues={
                    ":rating": _encode_rating(rating),
                    ":confirmation_retry": _encode_retry(RatingRetry()),
                    ":expected_rating": current_item["rating"],
                    ":expected_status": current_item["status"],
                    ":expected_nflverse_id": current_item["nflverse_id"],
                    ":expected_confirmation_retry": _encode_retry(
                        current.confirmation_retry
                    ),
                },
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise _StaleWrite from error
            raise GameRepositoryError("could not update confirmed rating in DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not update confirmed rating in DynamoDB") from error

    @staticmethod
    def _confirmation_retry_condition(current: Game) -> str:
        """Match a missing retry field only for the legacy default value."""
        if current.confirmation_retry == RatingRetry():
            return (
                "(attribute_not_exists(#confirmation_retry) OR "
                "#confirmation_retry = :expected_confirmation_retry)"
            )
        return "#confirmation_retry = :expected_confirmation_retry"

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

    def _update_rating_item(self, current: Game, rating: GameRating) -> None:
        """Conditionally replace one rating map for an unchanged final game."""
        current_item = self._codec.encode(current)
        try:
            self._table.update_item(
                Key={"game_id": str(current.game_id)},
                UpdateExpression="SET #rating = :rating",
                ConditionExpression=(
                    "attribute_exists(#game_id) AND #rating = :expected_rating "
                    "AND #status = :expected_status"
                ),
                ExpressionAttributeNames={
                    "#game_id": "game_id",
                    "#rating": "rating",
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":rating": _encode_rating(rating),
                    ":expected_rating": current_item["rating"],
                    ":expected_status": current_item["status"],
                },
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise _StaleWrite from error
            raise GameRepositoryError("could not update game rating in DynamoDB") from error
        except BotoCoreError as error:
            raise GameRepositoryError("could not update game rating in DynamoDB") from error

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
