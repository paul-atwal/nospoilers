from __future__ import annotations

import pytest

from backend.nospoil_nfl.game import (
    DomainValidationError,
    GameOutcome,
    GameState,
    GameStatus,
    RatingState,
    Score,
    can_transition_game_state,
    can_transition_rating_state,
    derive_final_outcome,
    rating_input_for_outcome,
)


def make_game_status(state: GameState) -> GameStatus:
    if state is GameState.IN_PROGRESS:
        return GameStatus(
            state=state,
            period=1,
            clock="15:00",
            score=Score(home=0, away=0),
        )
    if state is GameState.FINAL:
        return GameStatus(
            state=state,
            detail="Final",
            score=Score(home=24, away=17),
        )
    return GameStatus(state=state)


@pytest.mark.parametrize(
    ("current", "allowed_targets"),
    [
        (
            GameState.SCHEDULED,
            {
                GameState.SCHEDULED,
                GameState.POSTPONED,
                GameState.IN_PROGRESS,
                GameState.DELAYED,
                GameState.FINAL,
                GameState.CANCELLED,
            },
        ),
        (
            GameState.POSTPONED,
            {
                GameState.POSTPONED,
                GameState.SCHEDULED,
                GameState.IN_PROGRESS,
                GameState.DELAYED,
                GameState.FINAL,
                GameState.CANCELLED,
            },
        ),
        (
            GameState.IN_PROGRESS,
            {
                GameState.IN_PROGRESS,
                GameState.DELAYED,
                GameState.FINAL,
                GameState.CANCELLED,
            },
        ),
        (
            GameState.DELAYED,
            {
                GameState.DELAYED,
                GameState.POSTPONED,
                GameState.IN_PROGRESS,
                GameState.FINAL,
                GameState.CANCELLED,
            },
        ),
        (GameState.FINAL, {GameState.FINAL}),
        (GameState.CANCELLED, {GameState.CANCELLED}),
    ],
)
def test_game_state_transition_matrix(
    current: GameState,
    allowed_targets: set[GameState],
) -> None:
    actual_targets = {
        target
        for target in GameState
        if can_transition_game_state(make_game_status(current), target)
    }

    assert actual_targets == allowed_targets


def test_game_state_transition_requires_canonical_status_and_target() -> None:
    with pytest.raises(
        DomainValidationError,
        match="transitions require a canonical GameStatus",
    ):
        can_transition_game_state(
            GameState.SCHEDULED,
            GameState.FINAL,
        )

    with pytest.raises(
        DomainValidationError,
        match="transitions require a canonical target GameState",
    ):
        can_transition_game_state(
            GameStatus(state=GameState.SCHEDULED),
            "final",  # type: ignore[arg-type]
        )


def test_prekickoff_delay_can_be_postponed_and_rescheduled() -> None:
    delayed = GameStatus(state=GameState.DELAYED)

    assert not delayed.has_started
    assert can_transition_game_state(delayed, GameState.POSTPONED)
    assert can_transition_game_state(
        GameStatus(state=GameState.POSTPONED),
        GameState.SCHEDULED,
    )


def test_started_delay_cannot_regress_to_postponed() -> None:
    delayed = GameStatus(
        state=GameState.DELAYED,
        period=1,
        score=Score(home=0, away=0),
    )

    assert delayed.has_started
    assert not can_transition_game_state(delayed, GameState.POSTPONED)
    assert not can_transition_game_state(delayed, GameState.SCHEDULED)


@pytest.mark.parametrize(
    ("current", "allowed_targets"),
    [
        (
            RatingState.PENDING,
            {
                RatingState.PENDING,
                RatingState.PROVISIONAL,
                RatingState.CONFIRMED,
                RatingState.UNAVAILABLE,
            },
        ),
        (
            RatingState.PROVISIONAL,
            {
                RatingState.PROVISIONAL,
                RatingState.CONFIRMED,
            },
        ),
        (RatingState.CONFIRMED, {RatingState.CONFIRMED}),
        (RatingState.UNAVAILABLE, {RatingState.UNAVAILABLE}),
    ],
)
def test_rating_state_transition_matrix(
    current: RatingState,
    allowed_targets: set[RatingState],
) -> None:
    actual_targets = {
        target
        for target in RatingState
        if can_transition_rating_state(current, target)
    }

    assert actual_targets == allowed_targets


@pytest.mark.parametrize(
    ("home", "away", "expected"),
    [
        (24, 17, GameOutcome.HOME_WIN),
        (17, 24, GameOutcome.AWAY_WIN),
        (20, 20, GameOutcome.TIE),
    ],
)
def test_final_outcome_is_derived_from_score(
    home: int,
    away: int,
    expected: GameOutcome,
) -> None:
    status = GameStatus(
        state=GameState.FINAL,
        score=Score(home=home, away=away),
    )

    assert derive_final_outcome(status) is expected


def test_non_final_game_has_no_outcome() -> None:
    status = GameStatus(
        state=GameState.IN_PROGRESS,
        period=2,
        clock="03:14",
        score=Score(home=10, away=7),
    )

    with pytest.raises(
        DomainValidationError,
        match="outcome requires a final game status",
    ):
        derive_final_outcome(status)


@pytest.mark.parametrize(
    ("outcome", "home_team_won"),
    [
        (GameOutcome.HOME_WIN, True),
        (GameOutcome.AWAY_WIN, False),
        (GameOutcome.TIE, False),
    ],
)
def test_outcome_adapter_preserves_current_rating_formula_behavior(
    outcome: GameOutcome,
    home_team_won: bool,
) -> None:
    history = [0.25, 0.75]

    rating_input = rating_input_for_outcome(history, outcome)

    assert rating_input.wp_history == (0.25, 0.75)
    assert rating_input.home_team_won is home_team_won


def test_rating_input_adapter_requires_canonical_outcome() -> None:
    with pytest.raises(
        DomainValidationError,
        match="requires a canonical GameOutcome",
    ):
        rating_input_for_outcome(
            [0.25, 0.75],
            "tie",  # type: ignore[arg-type]
        )
