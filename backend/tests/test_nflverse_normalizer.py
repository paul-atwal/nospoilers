from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd
import pytest

from backend.nflverse_normalizer import normalize_nflverse_game_data
from backend.rating_input import GameRatingInput


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class NormalizationCase:
    name: str
    game_id: str
    fixture_name: str
    expected_game_data: GameRatingInput


REGULATION_CASE = NormalizationCase(
    name="regulation",
    game_id="2025_03_NYJ_TB",
    fixture_name="nflverse_pbp_regulation.json",
    expected_game_data={
        "game_id": "2025_03_NYJ_TB",
        "source": "nflfastR",
        "wp_history": [
            0.566792041063309,
            0.522391974925995,
            0.913995802385731,
            0.539870885515849,
            0.671166896820068,
            1.0,
        ],
        "score_history": [
            (0, 0),
            (0, 3),
            (20, 6),
            (26, 27),
            (29, 27),
            (29, 27),
        ],
        "home_score": 29,
        "away_score": 27,
        "is_overtime": False,
    },
)

OVERTIME_CASE = NormalizationCase(
    name="overtime",
    game_id="2025_12_NYG_DET",
    fixture_name="nflverse_pbp_overtime.json",
    expected_game_data={
        "game_id": "2025_12_NYG_DET",
        "source": "nflfastR",
        "wp_history": [
            0.566792041063309,
            0.514530062675476,
            0.153272468819463,
            0.221149250864983,
            0.829032859864836,
            1.0,
        ],
        "score_history": [
            (0, 0),
            (0, 6),
            (17, 27),
            (27, 27),
            (34, 27),
            (34, 27),
        ],
        "home_score": 34,
        "away_score": 27,
        "is_overtime": True,
    },
)

NORMALIZATION_CASES = [REGULATION_CASE, OVERTIME_CASE]


def load_plays(name: str) -> pd.DataFrame:
    with (FIXTURE_DIR / name).open() as fixture_file:
        return pd.DataFrame(json.load(fixture_file))


def expected_result(case: NormalizationCase) -> GameRatingInput:
    return deepcopy(case.expected_game_data)


@pytest.mark.parametrize(
    "case",
    NORMALIZATION_CASES,
    ids=lambda case: case.name,
)
def test_normalize_valid_game(case: NormalizationCase) -> None:
    game_plays = load_plays(case.fixture_name)

    result = normalize_nflverse_game_data(case.game_id, game_plays)

    assert result == expected_result(case)


def test_normalizer_skips_missing_win_probability_row() -> None:
    game_plays = load_plays(REGULATION_CASE.fixture_name)
    game_plays.loc[game_plays.index[-1], "home_wp"] = None
    expected = expected_result(REGULATION_CASE)
    expected["wp_history"] = expected["wp_history"][:-1]
    expected["score_history"] = expected["score_history"][:-1]

    result = normalize_nflverse_game_data(REGULATION_CASE.game_id, game_plays)

    assert result == expected


def test_normalizer_rejects_missing_required_column() -> None:
    game_plays = load_plays(REGULATION_CASE.fixture_name).drop(columns="qtr")

    result = normalize_nflverse_game_data(REGULATION_CASE.game_id, game_plays)

    assert result is None


def test_normalizer_rejects_out_of_range_win_probability() -> None:
    game_plays = load_plays(REGULATION_CASE.fixture_name)
    game_plays.loc[0, "home_wp"] = 1.1

    result = normalize_nflverse_game_data(REGULATION_CASE.game_id, game_plays)

    assert result is None


def test_normalizer_rejects_invalid_score() -> None:
    game_plays = load_plays(REGULATION_CASE.fixture_name)
    game_plays.loc[0, "total_home_score"] = -1

    result = normalize_nflverse_game_data(REGULATION_CASE.game_id, game_plays)

    assert result is None


def test_normalizer_requires_two_win_probabilities() -> None:
    game_plays = load_plays(REGULATION_CASE.fixture_name).head(1)

    result = normalize_nflverse_game_data(REGULATION_CASE.game_id, game_plays)

    assert result is None
