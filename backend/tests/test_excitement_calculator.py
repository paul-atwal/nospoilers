from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import pytest

from backend.excitement_calculator import calculate_excitement_score


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
    actual_score = calculate_excitement_score(
        case["wp_history"],
        case["home_score"],
        case["away_score"],
        case["is_overtime"],
    )

    assert actual_score == pytest.approx(case["expected_score"], abs=0.05)
