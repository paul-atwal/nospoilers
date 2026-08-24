"""
ESPN API fallback for when nflfastR doesn't have data yet.
Fetches win probability and scores from ESPN's summary endpoint.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any

import requests

try:
    from .rating_input import GameRatingInput
except ImportError:
    # The current deployment imports modules from inside the backend directory.
    from rating_input import GameRatingInput


def _select_competition(
    payload: Mapping[str, Any],
    game_id: str,
) -> Mapping[str, Any] | None:
    header = payload.get("header")
    if not isinstance(header, Mapping):
        return None

    competitions = header.get("competitions")
    if not isinstance(competitions, list):
        return None

    matches = [
        competition
        for competition in competitions
        if isinstance(competition, Mapping)
        and str(competition.get("id")) == game_id
    ]
    return matches[0] if len(matches) == 1 else None


def _parse_wp_history(raw: object) -> list[float] | None:
    if not isinstance(raw, list) or len(raw) < 2:
        return None

    wp_history: list[float] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            return None

        value = entry.get("homeWinPercentage")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None

        probability = float(value)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            return None

        wp_history.append(probability)

    return wp_history


def _parse_final_scores(raw: object) -> tuple[int, int] | None:
    if not isinstance(raw, list):
        return None

    scores: dict[str, int] = {}
    for competitor in raw:
        if not isinstance(competitor, Mapping):
            return None

        side = competitor.get("homeAway")
        if side not in {"home", "away"} or side in scores:
            return None

        raw_score = competitor.get("score")
        if isinstance(raw_score, bool):
            return None

        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            return None

        if score < 0 or (isinstance(raw_score, float) and raw_score != score):
            return None

        scores[side] = score

    if set(scores) != {"home", "away"}:
        return None

    return scores["home"], scores["away"]


def _is_final(competition: Mapping[str, Any]) -> bool:
    status = competition.get("status")
    if not isinstance(status, Mapping):
        return False

    status_type = status.get("type")
    if not isinstance(status_type, Mapping):
        return False

    return status_type.get("state") == "post" and status_type.get("completed") is True


def _is_overtime(competition: Mapping[str, Any]) -> bool:
    status = competition.get("status")
    if not isinstance(status, Mapping):
        return False

    status_type = status.get("type")
    if not isinstance(status_type, Mapping):
        return False

    status_parts = []
    for key in ("description", "detail", "shortDetail", "altDetail"):
        value = status_type.get(key)
        if isinstance(value, str):
            status_parts.append(value)

    status_text = " ".join(status_parts)
    return re.search(r"\b(?:OT|OVERTIME)\b", status_text, re.IGNORECASE) is not None


def normalize_espn_game_data(
    game_id: str,
    payload: Mapping[str, Any],
) -> GameRatingInput | None:
    competition = _select_competition(payload, game_id)
    if competition is None or not _is_final(competition):
        return None

    wp_history = _parse_wp_history(payload.get("winprobability"))
    final_scores = _parse_final_scores(competition.get("competitors"))
    if wp_history is None or final_scores is None:
        return None

    home_score, away_score = final_scores
    return GameRatingInput(
        wp_history=wp_history,
        score_history=[],
        home_score=home_score,
        away_score=away_score,
        is_overtime=_is_overtime(competition),
        game_id=game_id,
        source="ESPN",
    )


def fetch_espn_game_data(game_id: str) -> GameRatingInput | None:
    """
    Fetch game data from ESPN API as fallback when nflfastR doesn't have it yet.

    Args:
        game_id: ESPN game ID (e.g., "401772783")

    Returns:
        Normalized rating input, or None when ESPN data is unavailable or unusable.
    """
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={game_id}"
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            return None

        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(payload, Mapping):
        return None

    return normalize_espn_game_data(game_id, payload)
