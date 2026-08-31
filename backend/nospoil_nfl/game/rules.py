"""Pure game lifecycle, outcome, and rating-adapter rules."""

from __future__ import annotations

from collections.abc import Sequence

from ..rating import RatingInput
from .models import (
    DomainValidationError,
    GameOutcome,
    GameState,
    GameStatus,
    RatingState,
)


_GAME_STATE_TRANSITIONS: dict[GameState, frozenset[GameState]] = {
    GameState.SCHEDULED: frozenset(
        {
            GameState.SCHEDULED,
            GameState.POSTPONED,
            GameState.IN_PROGRESS,
            GameState.DELAYED,
            GameState.FINAL,
            GameState.CANCELLED,
        }
    ),
    GameState.POSTPONED: frozenset(
        {
            GameState.POSTPONED,
            GameState.SCHEDULED,
            GameState.IN_PROGRESS,
            GameState.DELAYED,
            GameState.FINAL,
            GameState.CANCELLED,
        }
    ),
    GameState.IN_PROGRESS: frozenset(
        {
            GameState.IN_PROGRESS,
            GameState.DELAYED,
            GameState.FINAL,
            GameState.CANCELLED,
        }
    ),
    GameState.DELAYED: frozenset(
        {
            GameState.DELAYED,
            GameState.IN_PROGRESS,
            GameState.FINAL,
            GameState.CANCELLED,
        }
    ),
    GameState.FINAL: frozenset({GameState.FINAL}),
    GameState.CANCELLED: frozenset({GameState.CANCELLED}),
}


_RATING_STATE_TRANSITIONS: dict[RatingState, frozenset[RatingState]] = {
    RatingState.PENDING: frozenset(
        {
            RatingState.PENDING,
            RatingState.PROVISIONAL,
            RatingState.CONFIRMED,
            RatingState.UNAVAILABLE,
        }
    ),
    RatingState.PROVISIONAL: frozenset(
        {
            RatingState.PROVISIONAL,
            RatingState.CONFIRMED,
        }
    ),
    RatingState.CONFIRMED: frozenset({RatingState.CONFIRMED}),
    RatingState.UNAVAILABLE: frozenset({RatingState.UNAVAILABLE}),
}


def can_transition_game_state(
    current: GameStatus,
    target: GameState,
) -> bool:
    """Return whether normal sync work may apply a game-state observation."""
    if not isinstance(current, GameStatus):
        raise DomainValidationError(
            "game-state transitions require a canonical GameStatus"
        )
    if not isinstance(target, GameState):
        raise DomainValidationError(
            "game-state transitions require a canonical target GameState"
        )

    if (
        current.state is GameState.DELAYED
        and not current.has_started
        and target is GameState.POSTPONED
    ):
        return True

    return target in _GAME_STATE_TRANSITIONS[current.state]


def can_transition_rating_state(
    current: RatingState,
    target: RatingState,
) -> bool:
    """Return whether normal rating work may apply a rating-state update."""
    if not isinstance(current, RatingState) or not isinstance(target, RatingState):
        raise DomainValidationError(
            "rating-state transitions require canonical RatingState values"
        )

    return target in _RATING_STATE_TRANSITIONS[current]


def derive_final_outcome(status: GameStatus) -> GameOutcome:
    """Derive a final game's outcome from its canonical score."""
    if not isinstance(status, GameStatus):
        raise DomainValidationError("outcome derivation requires a GameStatus")
    if status.state is not GameState.FINAL:
        raise DomainValidationError("outcome requires a final game status")

    score = status.score
    if score is None:
        raise DomainValidationError("outcome requires a final score")
    if score.home > score.away:
        return GameOutcome.HOME_WIN
    if score.away > score.home:
        return GameOutcome.AWAY_WIN
    return GameOutcome.TIE


def rating_input_for_outcome(
    wp_history: Sequence[float],
    outcome: GameOutcome,
) -> RatingInput:
    """Adapt an explicit game outcome to the current Boolean rating formula."""
    if not isinstance(outcome, GameOutcome):
        raise DomainValidationError(
            "rating input adaptation requires a canonical GameOutcome"
        )

    return RatingInput(
        wp_history=tuple(wp_history),
        home_team_won=outcome is GameOutcome.HOME_WIN,
    )


__all__ = [
    "can_transition_game_state",
    "can_transition_rating_state",
    "derive_final_outcome",
    "rating_input_for_outcome",
]
