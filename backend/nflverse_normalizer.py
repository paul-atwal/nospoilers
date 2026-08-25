from __future__ import annotations

import math
from numbers import Number

import pandas as pd

try:
    from .rating_input import GameRatingInput
except ImportError:
    # The current deployment imports modules from inside the backend directory.
    from rating_input import GameRatingInput


PBP_REQUIRED_COLUMNS = frozenset({
    "home_wp",
    "total_home_score",
    "total_away_score",
    "qtr",
})


def _valid_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, Number):
        return False
    return pd.notna(value) and math.isfinite(float(value))


def _normalize_wp_history(
    game_plays: pd.DataFrame,
) -> tuple[pd.Series, list[float]] | None:
    wp_mask = game_plays["home_wp"].notna()
    wp_values = game_plays.loc[wp_mask, "home_wp"].tolist()
    if len(wp_values) < 2:
        return None

    wp_history: list[float] = []
    for value in wp_values:
        if not _valid_number(value):
            return None
        probability = float(value)
        if not 0.0 <= probability <= 1.0:
            return None
        wp_history.append(probability)

    return wp_mask, wp_history


def _normalize_scores(
    game_plays: pd.DataFrame,
    wp_mask: pd.Series,
) -> tuple[list[tuple[int, int]], int, int] | None:
    score_columns = ["total_home_score", "total_away_score"]
    raw_scores = game_plays[score_columns]
    if not raw_scores.notna().any().all():
        return None

    for value in raw_scores.to_numpy().flat:
        if pd.isna(value):
            continue
        if not _valid_number(value):
            return None
        score = float(value)
        if score < 0 or not score.is_integer():
            return None

    scores = raw_scores.ffill().fillna(0)
    score_history = [
        (int(home_score), int(away_score))
        for home_score, away_score in scores.loc[wp_mask].itertuples(
            index=False,
            name=None,
        )
    ]
    final_scores = scores.iloc[-1]
    home_score = int(final_scores["total_home_score"])
    away_score = int(final_scores["total_away_score"])

    return score_history, home_score, away_score


def _detect_overtime(game_plays: pd.DataFrame) -> bool | None:
    quarters = game_plays.loc[game_plays["qtr"].notna(), "qtr"].tolist()
    if not quarters:
        return None

    parsed_quarters: list[int] = []
    for value in quarters:
        if not _valid_number(value):
            return None
        quarter = float(value)
        if quarter < 1 or not quarter.is_integer():
            return None
        parsed_quarters.append(int(quarter))

    return max(parsed_quarters) > 4


def normalize_nflverse_game_data(
    game_id: str,
    game_plays: pd.DataFrame,
) -> GameRatingInput | None:
    if (
        not isinstance(game_id, str)
        or not game_id
        or game_plays.empty
        or not PBP_REQUIRED_COLUMNS.issubset(game_plays.columns)
    ):
        return None

    normalized_wp = _normalize_wp_history(game_plays)
    if normalized_wp is None:
        return None
    wp_mask, wp_history = normalized_wp

    normalized_scores = _normalize_scores(game_plays, wp_mask)
    if normalized_scores is None:
        return None
    score_history, home_score, away_score = normalized_scores

    is_overtime = _detect_overtime(game_plays)
    if is_overtime is None:
        return None

    return GameRatingInput(
        wp_history=wp_history,
        score_history=score_history,
        home_score=home_score,
        away_score=away_score,
        is_overtime=is_overtime,
        game_id=game_id,
        source="nflfastR",
    )
