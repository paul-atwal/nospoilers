"""Excitement-rating formula and helper functions."""

from collections.abc import Sequence

from .input import RatingInput


# Constants derived from 2,622 regular-season games from 2016 through 2025.
VOLATILITY_MEAN = 2.1804
VOLATILITY_STD = 0.8237
COMEBACK_MEAN = 1.2812
K_FACTOR = 1.8
COMEBACK_MULTIPLIER = 0.3


def calculate_wp_volatility_normalized(
    wp_history: Sequence[float],
) -> float:
    """Calculate the average absolute win-probability change.

    Args:
        wp_history: Home-team win probabilities in play order.

    Returns:
        The average absolute change as a percentage.
    """
    if len(wp_history) < 2:
        return 0.0

    total_volatility = sum(
        abs(wp_history[index] - wp_history[index - 1])
        for index in range(1, len(wp_history))
    )
    num_plays = len(wp_history) - 1

    return (total_volatility / num_plays) * 100


def calculate_comeback_factor(
    wp_history: Sequence[float],
    home_team_won: bool,
) -> float:
    """Calculate the largest win-probability deficit overcome.

    Args:
        wp_history: Home-team win probabilities in play order.
        home_team_won: Whether the home team won the game.

    Returns:
        The comeback contribution before its formula multiplier.
    """
    if len(wp_history) < 2:
        return 0.0

    if home_team_won:
        deficit_overcome = 0.5 - min(wp_history)
    else:
        deficit_overcome = max(wp_history) - 0.5

    comeback_score = deficit_overcome * 7.5

    return max(0.0, comeback_score)


def calculate_rating(rating_input: RatingInput) -> float:
    """Calculate an excitement rating from immutable formula input.

    Args:
        rating_input: Values used directly by the rating formula.

    Returns:
        The excitement rating from 0.0 through 10.0.
    """
    volatility = calculate_wp_volatility_normalized(rating_input.wp_history)
    comeback = calculate_comeback_factor(
        rating_input.wp_history,
        rating_input.home_team_won,
    )

    z_volatility = (volatility - VOLATILITY_MEAN) / VOLATILITY_STD
    center = 5.0 - (COMEBACK_MEAN * COMEBACK_MULTIPLIER)
    raw_score = (
        center
        + (z_volatility * K_FACTOR)
        + (comeback * COMEBACK_MULTIPLIER)
    )
    final_score = max(0.0, min(10.0, raw_score))

    return round(final_score, 1)
