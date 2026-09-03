"""Concrete nflverse play-by-play provider."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import math
from numbers import Integral, Real
from typing import Any

from ..game.models import DomainValidationError, Score
from ._nflreadpy import load_nflreadpy
from .errors import ProviderDataError, ProviderError, ProviderTransportError
from .models import NflverseGameId, NflversePlay, NflversePlaySeason


NflversePlayLoader = Callable[[list[int]], object]
REQUIRED_PBP_COLUMNS = frozenset(
    {
        "season",
        "game_id",
        "play_id",
        "qtr",
        "home_wp",
        "total_home_score",
        "total_away_score",
    }
)


class NflversePlayClient:
    """Load and normalize one complete nflverse play-by-play season."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        loader: NflversePlayLoader | None = None,
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

    def load_plays(self, season: int) -> NflversePlaySeason:
        """Load every normalized play observation for one season."""
        requested_season = _positive_int(season, "season")
        try:
            table = self._loader([requested_season])
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderTransportError(
                "nflverse play-by-play load failed",
                provider="nflverse",
                operation="plays",
            ) from exc

        rows = _rows(table)
        plays = tuple(_normalize_row(row, requested_season) for row in rows)
        try:
            return NflversePlaySeason(
                season=requested_season,
                plays=plays,
            )
        except DomainValidationError as exc:
            raise _data_error(str(exc)) from exc

    def _default_loader(self, seasons: list[int]) -> object:
        return load_nflreadpy(
            "load_pbp",
            seasons,
            timeout_seconds=self._timeout_seconds,
            operation="plays",
        )


def _rows(table: object) -> list[Mapping[str, Any]]:
    columns = _column_names(table)

    if isinstance(table, list):
        raw_rows = table
    elif hasattr(table, "to_dicts") and callable(table.to_dicts):
        try:
            raw_rows = table.to_dicts()
        except Exception as exc:
            raise _data_error("nflverse PBP table could not be converted") from exc
    elif hasattr(table, "to_dict") and callable(table.to_dict):
        try:
            raw_rows = table.to_dict(orient="records")
        except Exception as exc:
            raise _data_error("nflverse PBP table could not be converted") from exc
    else:
        raise _data_error("nflverse PBP table is not a supported table")

    if not isinstance(raw_rows, list) or any(
        not isinstance(row, Mapping) for row in raw_rows
    ):
        raise _data_error("nflverse PBP rows must be objects")

    table_columns = set(columns) if columns is not None else set()
    row_columns = {column for row in raw_rows for column in row}
    missing_columns = REQUIRED_PBP_COLUMNS - (table_columns | row_columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise _data_error(f"nflverse PBP is missing columns: {missing}")
    if not raw_rows and columns is None:
        raise _data_error("empty nflverse PBP table has no validated schema")
    return raw_rows


def _column_names(table: object) -> tuple[str, ...] | None:
    columns = getattr(table, "columns", None)
    if columns is None:
        return None
    if isinstance(columns, str) or not isinstance(columns, Iterable):
        raise _data_error("nflverse PBP table has invalid columns")
    try:
        normalized_columns = tuple(columns)
    except TypeError as exc:
        raise _data_error("nflverse PBP table has invalid columns") from exc
    if any(not isinstance(column, str) for column in normalized_columns):
        raise _data_error("nflverse PBP table has invalid columns")
    return normalized_columns


def _normalize_row(row: Mapping[str, Any], requested_season: int) -> NflversePlay:
    source_season = _positive_int(row.get("season"), "play.season")
    if source_season != requested_season:
        raise _data_error("play row season does not match requested season")

    game_id = _text(row.get("game_id"), "play.game_id")
    play_number = _nonnegative_int(row.get("play_id"), "play.play_id")
    period = _optional_positive_int(row.get("qtr"), "play.qtr")
    home_win_probability = _optional_probability(
        row.get("home_wp"),
        "play.home_wp",
    )
    home_score = _optional_score(
        row.get("total_home_score"),
        "play.total_home_score",
    )
    away_score = _optional_score(
        row.get("total_away_score"),
        "play.total_away_score",
    )
    if (home_score is None) != (away_score is None):
        raise _data_error("play scores must be both present or both missing")
    score = (
        Score(home=home_score, away=away_score)
        if home_score is not None and away_score is not None
        else None
    )

    try:
        return NflversePlay(
            nflverse_game_id=NflverseGameId(game_id),
            play_number=play_number,
            period=period,
            home_win_probability=home_win_probability,
            score=score,
        )
    except DomainValidationError as exc:
        raise _data_error(f"play row {game_id}/{play_number} was invalid") from exc


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _data_error(f"{name} must be an integer >= 1")
    result = int(value)
    if result < 1:
        raise _data_error(f"{name} must be an integer >= 1")
    return result


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _data_error(f"{name} must be an integer >= 0")
    numeric_value = float(value)
    if (
        not math.isfinite(numeric_value)
        or numeric_value < 0
        or not numeric_value.is_integer()
    ):
        raise _data_error(f"{name} must be an integer >= 0")
    return int(numeric_value)


def _optional_positive_int(value: object, name: str) -> int | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _data_error(f"{name} must be an integer >= 1")
    numeric_value = float(value)
    if (
        not math.isfinite(numeric_value)
        or numeric_value < 1
        or not numeric_value.is_integer()
    ):
        raise _data_error(f"{name} must be an integer >= 1")
    return int(numeric_value)


def _optional_probability(value: object, name: str) -> float | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _data_error(f"{name} must be a finite number from 0.0 through 1.0")
    probability = float(value)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise _data_error(f"{name} must be a finite number from 0.0 through 1.0")
    return probability


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
    return ProviderDataError(message, provider="nflverse", operation="plays")


__all__ = [
    "NflversePlayClient",
    "NflversePlayLoader",
    "REQUIRED_PBP_COLUMNS",
]
