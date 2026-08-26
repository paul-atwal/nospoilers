"""Immutable input for the excitement-rating formula."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RatingInput:
    """Values used directly by the excitement-rating formula."""

    wp_history: tuple[float, ...]
    home_team_won: bool
