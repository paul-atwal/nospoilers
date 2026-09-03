"""Concrete ESPN game-summary provider."""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any

import requests

from ..game.models import (
    DomainValidationError,
    GameId,
    Score,
)
from .errors import (
    ProviderDataError,
    ProviderTransportError,
    ProviderUnavailableError,
)
from .models import GameSummary


ESPN_GAME_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
)
_PROVIDER = "espn"
_OPERATION = "summary"
_OVERTIME_PATTERN = re.compile(r"\b(?:\d+)?(?:OT|OVERTIME)\b", re.IGNORECASE)


class EspnGameSummaryClient:
    """Fetch and normalize one completed ESPN game summary."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite number greater than 0")
        self._timeout_seconds = timeout_seconds

    def fetch_summary(self, game_id: GameId) -> GameSummary:
        """Return a normalized summary for one completed ESPN game."""
        requested_game_id = _text(game_id, "game_id")
        url = f"{ESPN_GAME_SUMMARY_URL}?event={requested_game_id}"

        try:
            response = requests.get(url, timeout=self._timeout_seconds)
        except requests.RequestException as exc:
            raise ProviderTransportError(
                "ESPN game summary request failed",
                provider=_PROVIDER,
                operation=_OPERATION,
            ) from exc

        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            raise _data_error("ESPN game summary response has no valid status code")
        if status_code == 429 or status_code >= 500:
            raise ProviderUnavailableError(
                f"ESPN game summary returned HTTP {status_code}",
                provider=_PROVIDER,
                operation=_OPERATION,
            )
        if status_code != 200:
            raise _data_error(f"ESPN game summary returned HTTP {status_code}")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise _data_error("ESPN game summary response was not valid JSON") from exc

        try:
            return _normalize_summary(payload, requested_game_id)
        except ProviderDataError:
            raise
        except (DomainValidationError, KeyError, TypeError, ValueError) as exc:
            raise _data_error("ESPN game summary response was malformed") from exc


def _normalize_summary(payload: object, game_id: str) -> GameSummary:
    root = _mapping(payload, "summary response")
    header = _mapping(root.get("header"), "header")
    competitions = header.get("competitions")
    if not isinstance(competitions, list):
        raise _data_error("header.competitions must be a list")

    matches = [
        competition
        for competition in competitions
        if isinstance(competition, Mapping)
        and _source_id(competition.get("id")) == game_id
    ]
    if len(matches) != 1:
        raise _data_error(
            f"header.competitions must contain exactly one match for game {game_id}"
        )
    competition = matches[0]

    status_type = _final_status_type(competition.get("status"))
    win_probability_history = _win_probability_history(root.get("winprobability"))
    final_score = _final_score(competition.get("competitors"))
    is_overtime = _is_overtime(status_type)

    try:
        return GameSummary(
            game_id=GameId(game_id),
            win_probability_history=tuple(win_probability_history),
            final_score=final_score,
            is_overtime=is_overtime,
        )
    except DomainValidationError as exc:
        raise _data_error("normalized ESPN game summary was invalid") from exc


def _final_status_type(status: object) -> Mapping[str, Any]:
    source_status = _mapping(status, "competition.status")
    status_type = _mapping(source_status.get("type"), "competition.status.type")
    state = _text(status_type.get("state"), "status.type.state")
    completed = status_type.get("completed")
    if state != "post" or completed is not True:
        raise _data_error("ESPN game summary is not a completed final game")
    return status_type


def _win_probability_history(raw: object) -> list[float]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise _data_error("winprobability must contain at least two observations")

    history: list[float] = []
    for index, entry in enumerate(raw):
        source_entry = _mapping(entry, f"winprobability[{index}]")
        value = source_entry.get("homeWinPercentage")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _data_error(
                f"winprobability[{index}].homeWinPercentage must be numeric"
            )
        probability = float(value)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise _data_error(
                f"winprobability[{index}].homeWinPercentage must be from 0.0 through 1.0"
            )
        history.append(probability)
    return history


def _final_score(raw: object) -> Score:
    if not isinstance(raw, list) or len(raw) != 2:
        raise _data_error("competition.competitors must contain exactly two teams")

    scores: dict[str, int] = {}
    for index, competitor in enumerate(raw):
        source_competitor = _mapping(competitor, f"competitors[{index}]")
        side = _text(source_competitor.get("homeAway"), "competitor.homeAway")
        if side not in {"home", "away"} or side in scores:
            raise _data_error("competition must contain one home and one away team")
        scores[side] = _nonnegative_int(
            source_competitor.get("score"),
            f"{side} final score",
        )
    if set(scores) != {"home", "away"}:
        raise _data_error("competition must contain one home and one away team")

    try:
        return Score(home=scores["home"], away=scores["away"])
    except DomainValidationError as exc:
        raise _data_error("normalized ESPN final score was invalid") from exc


def _is_overtime(status_type: Mapping[str, Any]) -> bool:
    text_fields: list[str] = []
    for field in ("description", "detail", "shortDetail", "altDetail"):
        value = status_type.get(field)
        if value is not None and not isinstance(value, str):
            raise _data_error(f"status.type.{field} must be text")
        if isinstance(value, str):
            text_fields.append(value)
    return _OVERTIME_PATTERN.search(" ".join(text_fields)) is not None


def _source_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _data_error(f"{name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _data_error(f"{name} must be non-empty text")
    return value.strip()


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise _data_error(f"{name} must be an integer >= 0")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        result = int(value.strip())
    else:
        raise _data_error(f"{name} must be an integer >= 0")
    if result < 0:
        raise _data_error(f"{name} must be an integer >= 0")
    return result


def _data_error(message: str) -> ProviderDataError:
    return ProviderDataError(message, provider=_PROVIDER, operation=_OPERATION)


__all__ = ["ESPN_GAME_SUMMARY_URL", "EspnGameSummaryClient"]
