"""Concrete nflverse schedule provider."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import math
from numbers import Integral, Real
from typing import Any

from ..game.models import (
    DomainValidationError,
    Score,
    SeasonPhase,
    SeasonWeek,
)
from .errors import (
    ProviderDataError,
    ProviderError,
    ProviderTransportError,
)
from ._nflreadpy import load_nflreadpy
from .models import (
    NflverseGameId,
    NflverseScheduleGame,
    NflverseScheduleSeason,
)


NflverseScheduleLoader = Callable[[list[int]], object]
REQUIRED_SCHEDULE_COLUMNS = frozenset(
    {
        "season",
        "game_type",
        "week",
        "game_id",
        "espn",
        "home_score",
        "away_score",
    }
)
_GAME_PHASES = {
    "REG": SeasonPhase.REGULAR_SEASON,
    "WC": SeasonPhase.POSTSEASON,
    "DIV": SeasonPhase.POSTSEASON,
    "CON": SeasonPhase.POSTSEASON,
    "SB": SeasonPhase.POSTSEASON,
}
_POSTSEASON_WEEKS = {
    "WC": (19, 1),
    "DIV": (20, 2),
    "CON": (21, 3),
    "SB": (22, 5),
}


class NflverseScheduleClient:
    """Load and normalize one complete nflverse schedule season."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        loader: NflverseScheduleLoader | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite number greater than 0")
        self._timeout_seconds = timeout_seconds
        self._loader = loader or self._default_loader

    def load_schedule(self, season: int) -> NflverseScheduleSeason:
        """Load every normalized schedule row for one season."""
        requested_season = _positive_int(season, "season")
        try:
            table = self._loader([requested_season])
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderTransportError(
                "nflverse schedule load failed",
                provider="nflverse",
                operation="schedule",
            ) from exc

        rows = _rows(table)
        games = tuple(_normalize_row(row, requested_season) for row in rows)
        try:
            return NflverseScheduleSeason(
                season=requested_season,
                games=games,
            )
        except DomainValidationError as exc:
            raise _data_error(str(exc)) from exc

    def _default_loader(self, seasons: list[int]) -> object:
        return load_nflreadpy(
            "load_schedules",
            seasons,
            timeout_seconds=self._timeout_seconds,
            operation="schedule",
        )


def _rows(table: object) -> list[Mapping[str, Any]]:
    columns = _column_names(table)

    if isinstance(table, list):
        raw_rows = table
    elif hasattr(table, "to_dicts") and callable(table.to_dicts):
        try:
            raw_rows = table.to_dicts()
        except Exception as exc:
            raise _data_error("nflverse schedule table could not be converted") from exc
    elif hasattr(table, "to_dict") and callable(table.to_dict):
        try:
            raw_rows = table.to_dict(orient="records")
        except Exception as exc:
            raise _data_error("nflverse schedule table could not be converted") from exc
    else:
        raise _data_error("nflverse schedule table is not a supported table")

    if not isinstance(raw_rows, list) or any(
        not isinstance(row, Mapping) for row in raw_rows
    ):
        raise _data_error("nflverse schedule rows must be objects")

    table_columns = set(columns) if columns is not None else set()
    row_columns = {column for row in raw_rows for column in row}
    available_columns = table_columns | row_columns
    missing_columns = REQUIRED_SCHEDULE_COLUMNS - available_columns
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise _data_error(f"nflverse schedule is missing columns: {missing}")
    if not raw_rows and columns is None:
        raise _data_error("empty nflverse schedule has no validated schema")
    return raw_rows


def _column_names(table: object) -> tuple[str, ...] | None:
    columns = getattr(table, "columns", None)
    if columns is None:
        return None
    if isinstance(columns, str) or not isinstance(columns, Iterable):
        raise _data_error("nflverse schedule table has invalid columns")
    try:
        normalized_columns = tuple(columns)
    except TypeError as exc:
        raise _data_error("nflverse schedule table has invalid columns") from exc
    if any(not isinstance(column, str) for column in normalized_columns):
        raise _data_error("nflverse schedule table has invalid columns")
    return normalized_columns


def _normalize_row(
    row: Mapping[str, Any], requested_season: int
) -> NflverseScheduleGame:
    source_season = _positive_int(row.get("season"), "schedule.season")
    if source_season != requested_season:
        raise _data_error("schedule row season does not match requested season")

    game_type = _text(row.get("game_type"), "schedule.game_type").upper()
    try:
        phase = _GAME_PHASES[game_type]
    except KeyError as exc:
        raise _data_error(f"unsupported nflverse game_type: {game_type}") from exc

    source_week = _positive_int(row.get("week"), "schedule.week")
    if game_type in _POSTSEASON_WEEKS:
        expected_source_week, week = _POSTSEASON_WEEKS[game_type]
        if source_week != expected_source_week:
            raise _data_error(
                f"nflverse {game_type} week must be {expected_source_week}"
            )
    else:
        week = source_week
    game_id = _text(row.get("game_id"), "schedule.game_id")
    espn_id = _optional_identifier(row.get("espn"), "schedule.espn")
    home_score = _optional_score(row.get("home_score"), "schedule.home_score")
    away_score = _optional_score(row.get("away_score"), "schedule.away_score")
    if (home_score is None) != (away_score is None):
        raise _data_error("schedule scores must be both present or both missing")
    final_score = (
        Score(home=home_score, away=away_score)
        if home_score is not None and away_score is not None
        else None
    )

    try:
        return NflverseScheduleGame(
            season_week=SeasonWeek(
                season=requested_season,
                phase=phase,
                week=week,
            ),
            nflverse_game_id=NflverseGameId(game_id),
            espn_id=espn_id,
            final_score=final_score,
        )
    except DomainValidationError as exc:
        raise _data_error(f"schedule row {game_id} was invalid") from exc


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _data_error(f"{name} must be an integer >= 1")
    result = int(value)
    if result < 1:
        raise _data_error(f"{name} must be an integer >= 1")
    return result


def _optional_score(value: object, name: str) -> int | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _data_error(f"{name} must be a non-negative integer or null")
    numeric_value = float(value)
    if (
        not math.isfinite(numeric_value)
        or numeric_value < 0
        or not numeric_value.is_integer()
    ):
        raise _data_error(f"{name} must be a non-negative integer or null")
    return int(numeric_value)


def _optional_identifier(value: object, name: str) -> str | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, Integral)):
        raise _data_error(f"{name} must be a string identifier or null")
    return _text(str(value), name)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _data_error(f"{name} must be non-empty text")
    return value.strip()


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isnan(float(value))
    )


def _data_error(message: str) -> ProviderDataError:
    return ProviderDataError(message, provider="nflverse", operation="schedule")


__all__ = [
    "NflverseScheduleClient",
    "NflverseScheduleLoader",
    "REQUIRED_SCHEDULE_COLUMNS",
]
