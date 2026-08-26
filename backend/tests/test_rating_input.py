from dataclasses import FrozenInstanceError

import pytest

from backend.nospoil_nfl.rating import RatingInput


def test_rating_input_stores_formula_values() -> None:
    rating_input = RatingInput(
        wp_history=(0.25, 0.75),
        home_team_won=True,
    )

    assert rating_input.wp_history == (0.25, 0.75)
    assert rating_input.home_team_won is True


def test_rating_input_fields_cannot_be_reassigned() -> None:
    rating_input = RatingInput(
        wp_history=(0.25, 0.75),
        home_team_won=True,
    )

    with pytest.raises(FrozenInstanceError):
        rating_input.home_team_won = False  # type: ignore[misc]


def test_wp_history_cannot_be_changed_in_place() -> None:
    rating_input = RatingInput(
        wp_history=(0.25, 0.75),
        home_team_won=True,
    )

    with pytest.raises(TypeError):
        rating_input.wp_history[0] = 0.5  # type: ignore[index]
