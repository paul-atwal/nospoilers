from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from backend.nospoil_nfl.game.models import GameId, Score
from backend.nospoil_nfl.providers import (
    ESPN_GAME_SUMMARY_URL,
    ESPNGameSummaryProvider,
    EspnGameSummaryClient,
    GameSummary,
    ProviderDataError,
    ProviderTransportError,
    ProviderUnavailableError,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
REGULATION_ID = "401772840"
OVERTIME_ID = "401772888"


def load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open() as fixture_file:
        return json.load(fixture_file)


def make_client(monkeypatch: pytest.MonkeyPatch, payload: object) -> Mock:
    response = Mock(status_code=200)
    response.json.return_value = payload
    request = Mock(return_value=response)
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_summary.requests.get",
        request,
    )
    return request


def test_normalizes_regulation_summary_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_client(
        monkeypatch,
        load_fixture("espn_summary_regulation.json"),
    )

    result = EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))

    assert result == GameSummary(
        game_id=GameId(REGULATION_ID),
        win_probability_history=(0.708, 0.6884, 0.6791, 1.0, 1.0),
        final_score=Score(home=29, away=27),
        is_overtime=False,
    )
    request.assert_called_once_with(
        f"{ESPN_GAME_SUMMARY_URL}?event={REGULATION_ID}",
        timeout=10.0,
    )


def test_normalizes_overtime_from_nested_status_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_client(
        monkeypatch,
        load_fixture("espn_summary_overtime.json"),
    )

    result = EspnGameSummaryClient().fetch_summary(GameId(OVERTIME_ID))

    assert result.final_score == Score(home=34, away=27)
    assert result.is_overtime is True


def test_normalizes_multi_overtime_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_summary_overtime.json")
    status_type = payload["header"]["competitions"][0]["status"]["type"]
    status_type.update(
        {
            "detail": "Final/2OT",
            "shortDetail": "Final/2OT",
            "altDetail": "2OT",
        }
    )
    make_client(monkeypatch, payload)

    result = EspnGameSummaryClient().fetch_summary(GameId(OVERTIME_ID))

    assert result.is_overtime is True


def test_selects_the_single_competition_matching_game_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_summary_regulation.json")
    unrelated = deepcopy(payload["header"]["competitions"][0])
    unrelated["id"] = "unrelated-game"
    payload["header"]["competitions"].insert(0, unrelated)
    make_client(monkeypatch, payload)

    result = EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))

    assert result.game_id == REGULATION_ID


def test_rejects_non_final_summary_as_provider_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_summary_regulation.json")
    status_type = payload["header"]["competitions"][0]["status"]["type"]
    status_type["state"] = "in"
    status_type["completed"] = False
    make_client(monkeypatch, payload)

    with pytest.raises(ProviderDataError, match="completed final"):
        EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            lambda payload: payload["winprobability"][0].pop("homeWinPercentage"),
            id="missing-win-probability",
        ),
        pytest.param(
            lambda payload: payload["winprobability"][0].update(homeWinPercentage=1.1),
            id="out-of-range-win-probability",
        ),
        pytest.param(
            lambda payload: payload["header"]["competitions"][0]["competitors"].pop(),
            id="missing-competitor",
        ),
        pytest.param(
            lambda payload: payload["header"]["competitions"][0]["competitors"][
                0
            ].update(score="-1"),
            id="negative-score",
        ),
    ],
)
def test_rejects_invalid_summary_values(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
) -> None:
    payload = load_fixture("espn_summary_regulation.json")
    mutation(payload)
    make_client(monkeypatch, payload)

    with pytest.raises(ProviderDataError):
        EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))


def test_rejects_empty_game_id() -> None:
    with pytest.raises(ProviderDataError, match="game_id"):
        EspnGameSummaryClient().fetch_summary(GameId(""))


def test_rejects_missing_matching_competition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_summary_regulation.json")
    payload["header"]["competitions"][0]["id"] = "different-game"
    make_client(monkeypatch, payload)

    with pytest.raises(ProviderDataError, match="exactly one match"):
        EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_rate_limit_and_server_errors_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    response = Mock(status_code=status_code)
    request = Mock(return_value=response)
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_summary.requests.get",
        request,
    )

    with pytest.raises(ProviderUnavailableError):
        EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))

    response.json.assert_not_called()


def test_timeout_is_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Mock(side_effect=requests.Timeout)
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_summary.requests.get",
        request,
    )

    with pytest.raises(ProviderTransportError):
        EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))


def test_invalid_json_is_data_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock(status_code=200)
    response.json.side_effect = ValueError
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_summary.requests.get",
        Mock(return_value=response),
    )

    with pytest.raises(ProviderDataError):
        EspnGameSummaryClient().fetch_summary(GameId(REGULATION_ID))


def test_summary_contract_accepts_fixture_provider() -> None:
    class FixtureSummaryProvider:
        def __init__(self, summary: GameSummary) -> None:
            self.summary = summary

        def fetch_summary(self, game_id: GameId) -> GameSummary:
            assert game_id == REGULATION_ID
            return self.summary

    summary = GameSummary(
        game_id=GameId(REGULATION_ID),
        win_probability_history=(0.5, 0.75),
        final_score=Score(home=24, away=17),
        is_overtime=False,
    )
    provider: ESPNGameSummaryProvider = FixtureSummaryProvider(summary)

    assert provider.fetch_summary(GameId(REGULATION_ID)) is summary
