"""Compatibility API for legacy excitement-calculator callers."""

from collections.abc import Sequence

if __package__:
    from .nospoil_nfl.rating import RatingInput, calculate_rating
    from .nospoil_nfl.rating.calculator import (
        COMEBACK_MEAN,
        COMEBACK_MULTIPLIER,
        K_FACTOR,
        VOLATILITY_MEAN,
        VOLATILITY_STD,
        calculate_comeback_factor as _calculate_comeback_factor,
        calculate_wp_volatility_normalized,
    )
else:
    from nospoil_nfl.rating import RatingInput, calculate_rating
    from nospoil_nfl.rating.calculator import (
        COMEBACK_MEAN,
        COMEBACK_MULTIPLIER,
        K_FACTOR,
        VOLATILITY_MEAN,
        VOLATILITY_STD,
        calculate_comeback_factor as _calculate_comeback_factor,
        calculate_wp_volatility_normalized,
    )


def calculate_comeback_factor(
    wp_history: Sequence[float],
    home_score: int,
    away_score: int,
) -> float:
    """Preserve the legacy final-score helper signature."""
    return _calculate_comeback_factor(
        wp_history,
        home_team_won=home_score > away_score,
    )


def calculate_excitement_score(
    wp_history: Sequence[float],
    home_score: int,
    away_score: int,
    is_overtime: bool,
    score_history: Sequence[tuple[int, int]] | None = None,
) -> float:
    """Delegate the legacy primitive-argument API to the primary calculator.

    The current formula does not use ``is_overtime`` or ``score_history``.
    They remain in this signature only for compatibility.
    """
    return calculate_rating(
        RatingInput(
            wp_history=tuple(wp_history),
            home_team_won=home_score > away_score,
        )
    )


__all__ = [
    "COMEBACK_MEAN",
    "COMEBACK_MULTIPLIER",
    "K_FACTOR",
    "VOLATILITY_MEAN",
    "VOLATILITY_STD",
    "calculate_comeback_factor",
    "calculate_excitement_score",
    "calculate_wp_volatility_normalized",
]
