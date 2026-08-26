from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from backend import espn_fetcher
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
    game_id="401772840",
    fixture_name="espn_summary_regulation.json",
    expected_game_data={
        "game_id": "401772840",
        "source": "ESPN",
        "wp_history": [0.708, 0.6884, 0.6791, 1.0, 1.0],
        "score_history": [],
        "home_score": 29,
        "away_score": 27,
        "is_overtime": False,
    },
)

OVERTIME_CASE = NormalizationCase(
    name="overtime",
    game_id="401772888",
    fixture_name="espn_summary_overtime.json",
    expected_game_data={
        "game_id": "401772888",
        "source": "ESPN",
        "wp_history": [0.7227, 0.7026, 0.6752, 0.8934, 1.0],
        "score_history": [],
        "home_score": 34,
        "away_score": 27,
        "is_overtime": True,
    },
)

NORMALIZATION_CASES = [REGULATION_CASE, OVERTIME_CASE]


def load_summary(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open() as fixture_file:
        return json.load(fixture_file)


def expected_result(case: NormalizationCase) -> GameRatingInput:
    return deepcopy(case.expected_game_data)


@pytest.mark.parametrize(
    "case",
    NORMALIZATION_CASES,
    ids=lambda case: case.name,
)
def test_normalize_valid_final_game(case: NormalizationCase) -> None:
    payload = load_summary(case.fixture_name)

    result = espn_fetcher.normalize_espn_game_data(case.game_id, payload)

    assert result == expected_result(case)


def test_normalizer_selects_matching_competition() -> None:
    case = REGULATION_CASE
    payload = load_summary(case.fixture_name)
    unrelated = deepcopy(payload["header"]["competitions"][0])
    unrelated["id"] = "unrelated-game"
    payload["header"]["competitions"].insert(0, unrelated)

    result = espn_fetcher.normalize_espn_game_data(case.game_id, payload)

    assert result == expected_result(case)


def test_normalizer_rejects_nonmatching_competition() -> None:
    case = REGULATION_CASE
    payload = load_summary(case.fixture_name)

    result = espn_fetcher.normalize_espn_game_data("different-game", payload)

    assert result is None


def test_normalizer_rejects_missing_win_probability() -> None:
    case = REGULATION_CASE
    payload = load_summary(case.fixture_name)
    del payload["winprobability"][0]["homeWinPercentage"]

    result = espn_fetcher.normalize_espn_game_data(case.game_id, payload)

    assert result is None


def test_normalizer_rejects_out_of_range_win_probability() -> None:
    case = REGULATION_CASE
    payload = load_summary(case.fixture_name)
    payload["winprobability"][0]["homeWinPercentage"] = 1.1

    result = espn_fetcher.normalize_espn_game_data(case.game_id, payload)

    assert result is None


def test_normalizer_rejects_incomplete_game() -> None:
    case = REGULATION_CASE
    payload = load_summary(case.fixture_name)
    status = payload["header"]["competitions"][0]["status"]["type"]
    status["state"] = "in"
    status["completed"] = False

    result = espn_fetcher.normalize_espn_game_data(case.game_id, payload)

    assert result is None


def test_normalizer_rejects_missing_competitor() -> None:
    case = REGULATION_CASE
    payload = load_summary(case.fixture_name)
    payload["header"]["competitions"][0]["competitors"].pop()

    result = espn_fetcher.normalize_espn_game_data(case.game_id, payload)

    assert result is None


def test_fetch_normalizes_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = REGULATION_CASE
    response = Mock(status_code=200)
    response.json.return_value = load_summary(case.fixture_name)
    request = Mock(return_value=response)
    monkeypatch.setattr(espn_fetcher.requests, "get", request)

    result = espn_fetcher.fetch_espn_game_data(case.game_id)

    assert result == expected_result(case)
    request.assert_called_once_with(
        f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={case.game_id}",
        timeout=10,
    )


def test_fetch_rejects_unsuccessful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Mock(status_code=503)
    request = Mock(return_value=response)
    monkeypatch.setattr(espn_fetcher.requests, "get", request)

    result = espn_fetcher.fetch_espn_game_data("game-id")

    assert result is None
    response.json.assert_not_called()


def test_fetch_handles_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Mock(side_effect=espn_fetcher.requests.Timeout)
    monkeypatch.setattr(espn_fetcher.requests, "get", request)

    result = espn_fetcher.fetch_espn_game_data("game-id")

    assert result is None


def test_fetch_handles_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock(status_code=200)
    response.json.side_effect = ValueError
    monkeypatch.setattr(espn_fetcher.requests, "get", Mock(return_value=response))

    result = espn_fetcher.fetch_espn_game_data("game-id")

    assert result is None
