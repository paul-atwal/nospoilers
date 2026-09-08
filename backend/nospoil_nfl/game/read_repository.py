"""Minimal read-side repository contract and DynamoDB implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError

from .dynamodb_codec import _DynamoGameCodec, _season_key, _week_schedule_prefix
from .errors import GameRepositoryDataError, GameRepositoryError
from .models import Game, SeasonWeek


class GameReadRepository(Protocol):
    """Read complete game records without exposing write operations."""

    def list_week(self, season_week: SeasonWeek) -> list[Game]:
        """Return one complete season week in kickoff order."""
        ...

    def list_season(self, season: int) -> list[Game]:
        """Return one complete season in phase, week, and kickoff order."""
        ...


class _DynamoReadTable(Protocol):
    """Small DynamoDB table interface used by the read runtime."""

    def query(self, **kwargs: object) -> Mapping[str, object]:
        """Return one index query page."""
        ...


class DynamoReadRepository:
    """Read complete games from the schedule index without write dependencies."""

    def __init__(
        self,
        table: _DynamoReadTable,
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
                    raise GameRepositoryDataError(
                        "DynamoDB query returned a non-map item"
                    )
                games.append(self._codec.decode(item))

            last_evaluated_key = response.get("LastEvaluatedKey")
            if last_evaluated_key is None:
                return games
            if not isinstance(last_evaluated_key, Mapping):
                raise GameRepositoryDataError(
                    "DynamoDB query returned a non-map continuation key"
                )
            query_args["ExclusiveStartKey"] = dict(last_evaluated_key)


__all__ = [
    "DynamoReadRepository",
    "GameReadRepository",
    "GameRepositoryDataError",
    "GameRepositoryError",
]
