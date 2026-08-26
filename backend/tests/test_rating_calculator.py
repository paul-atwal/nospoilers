from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import pytest

from backend.nospoil_nfl.rating import RatingInput, calculate_rating


class RatingCase(TypedDict):
    id: str
    wp_history: list[float]
    home_score: int
    away_score: int
    is_overtime: bool
    expected_score: float


def load_rating_cases() -> list[RatingCase]:
    fixture_path = Path(__file__).parent / "fixtures" / "rating_cases.json"
    with fixture_path.open() as fixture_file:
        return json.load(fixture_file)


@pytest.mark.parametrize("case", load_rating_cases(), ids=lambda case: case["id"])
def test_representative_rating(case: RatingCase) -> None:
    rating_input = RatingInput(
        wp_history=tuple(case["wp_history"]),
        home_team_won=case["home_score"] > case["away_score"],
    )

    actual_score = calculate_rating(rating_input)

    assert actual_score == pytest.approx(case["expected_score"], abs=0.05)
