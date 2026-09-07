"""Pure policy for competitions supported by nflverse confirmation."""

from __future__ import annotations

from dataclasses import dataclass

from ..game.models import Game, GameState, RatingState, SeasonPhase, SeasonWeek


@dataclass(frozen=True, slots=True)
class ConfirmationSupport:
    """Support decision and stable explanation for operator feedback."""

    supported: bool
    reason: str


def confirmation_support(season_week: SeasonWeek) -> ConfirmationSupport:
    """Return whether nflverse confirmation is defined for a season week."""
    if season_week.phase is SeasonPhase.REGULAR_SEASON:
        return ConfirmationSupport(True, "regular_season")
    if season_week.phase is SeasonPhase.PRESEASON:
        return ConfirmationSupport(False, "preseason_unsupported")
    if season_week.phase is SeasonPhase.POSTSEASON and season_week.week in {1, 2, 3, 5}:
        return ConfirmationSupport(True, f"postseason_week_{season_week.week}")
    if season_week.phase is SeasonPhase.POSTSEASON and season_week.week == 4:
        return ConfirmationSupport(False, "pro_bowl_unsupported")
    return ConfirmationSupport(False, "unsupported_competition")


def is_confirmation_supported(season_week: SeasonWeek) -> bool:
    """Return whether nflverse confirmation is supported for a week."""
    return confirmation_support(season_week).supported


def confirmation_work_remains(game: Game) -> bool:
    """Return whether a final supported game can still be confirmed."""
    return (
        game.status.state is GameState.FINAL
        and is_confirmation_supported(game.season_week)
        and game.rating.state is not RatingState.CONFIRMED
    )


__all__ = [
    "ConfirmationSupport",
    "confirmation_support",
    "confirmation_work_remains",
    "is_confirmation_supported",
]
