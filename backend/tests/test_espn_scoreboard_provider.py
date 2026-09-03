from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from backend.nospoil_nfl.game.models import (
    GameState,
    RecordScope,
    SeasonPhase,
    SeasonWeek,
    Score,
)
from backend.nospoil_nfl.providers import (
    EspnScoreboardClient,
    ProviderDataError,
    ProviderTransportError,
    ProviderUnavailableError,
    ScoreboardBatch,
)
from backend.nospoil_nfl.providers.espn_scoreboard import ESPN_SCOREBOARD_URL


FIXTURE_DIR = Path(__file__).parent / "fixtures"
OBSERVED_AT = datetime(2026, 9, 2, 16, 30, tzinfo=UTC)
REGULAR_WEEK = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1)


def load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open() as fixture_file:
        return json.load(fixture_file)


def make_client(monkeypatch: pytest.MonkeyPatch, payload: object) -> Mock:
    response = Mock(status_code=200)
    response.json.return_value = payload
    request = Mock(return_value=response)
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_scoreboard.requests.get",
        request,
    )
    return request


def test_fetches_one_response_and_normalizes_all_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_client(
        monkeypatch,
        load_fixture("espn_scoreboard_scheduled.json"),
    )
    client = EspnScoreboardClient(clock=lambda: OBSERVED_AT)

    result = client.fetch_scoreboard(REGULAR_WEEK)

    assert isinstance(result, ScoreboardBatch)
    assert result.season_week == REGULAR_WEEK
    assert len(result.games) == 2
    assert [game.game_id for game in result.games] == [
        "401872656",
        "401872657",
    ]
    assert result.games[0].home.abbreviation == "SEA"
    assert result.games[0].home.logo_key == "26"
    assert result.games[0].away.abbreviation == "NE"
    assert result.games[0].broadcaster == "NBC"
    assert result.games[0].odds is not None
    assert result.games[0].odds.details == "SEA -3.5"
    assert result.known_weeks[0] == SeasonWeek(2026, SeasonPhase.PRESEASON, 1)
    assert result.known_weeks[-1] == SeasonWeek(2026, SeasonPhase.POSTSEASON, 5)
    assert len(result.known_weeks) == 27
    request.assert_called_once_with(
        f"{ESPN_SCOREBOARD_URL}?dates=2026&week=1&seasontype=2&limit=100",
        timeout=10.0,
    )


def test_scheduled_zero_scores_are_not_live_score_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_client(
        monkeypatch,
        load_fixture("espn_scoreboard_scheduled.json"),
    )

    game = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard().games[0]

    assert game.status.state is GameState.SCHEDULED
    assert game.status.score is None
    assert game.status.period is None
    assert game.status.clock is None


def test_cancelled_event_discards_placeholder_live_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_client(
        monkeypatch,
        load_fixture("espn_scoreboard_cancelled.json"),
    )

    game = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard().games[0]

    assert game.game_id == "401437947"
    assert game.status.state is GameState.CANCELLED
    assert game.status.detail == "Canceled"
    assert game.status.score is None
    assert game.status.period is None
    assert game.status.clock is None


def test_reads_status_detail_from_nested_status_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_client(
        monkeypatch,
        load_fixture("espn_scoreboard_scheduled.json"),
    )

    game = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard().games[0]

    assert game.status.detail == "Thu, September 10th at 8:20 PM EDT"


def test_accepts_post_state_postponement_before_completion_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_scoreboard_scheduled.json")
    payload["events"][0]["status"].update(
        {
            "period": 0,
            "displayClock": "0:00",
            "type": {
                "name": "STATUS_POSTPONED",
                "state": "post",
                "completed": False,
                "description": "Postponed",
                "detail": "Postponed",
                "shortDetail": "Postponed",
            },
        }
    )
    make_client(monkeypatch, payload)

    game = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard().games[0]

    assert game.status.state is GameState.POSTPONED
    assert game.status.detail == "Postponed"
    assert game.status.score is None
    assert game.status.period is None
    assert game.status.clock is None


def test_postseason_final_preserves_record_snapshot_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_client(
        monkeypatch,
        load_fixture("espn_scoreboard_postseason_final.json"),
    )

    result = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard()
    game = result.games[0]

    assert result.season_week == SeasonWeek(2025, SeasonPhase.POSTSEASON, 1)
    assert game.status.state is GameState.FINAL
    assert game.status.score == Score(home=31, away=34)
    assert game.home.record is not None
    assert game.home.record.record.wins == 8
    assert game.home.record.record.losses == 9
    assert game.home.record.scope is RecordScope.REGULAR_SEASON
    assert game.home.record.snapshot_at == OBSERVED_AT


@pytest.mark.parametrize(
    ("state", "expected_state", "expected_score"),
    [
        ("pre", GameState.DELAYED, None),
        ("in", GameState.DELAYED, Score(home=0, away=0)),
    ],
)
def test_maps_pregame_and_in_game_delays_without_fabricating_scores(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    expected_state: GameState,
    expected_score: Score | None,
) -> None:
    payload = load_fixture("espn_scoreboard_scheduled.json")
    event = payload["events"][0]
    event["status"].update(
        {
            "period": 2 if state == "in" else 0,
            "displayClock": "7:11" if state == "in" else "0:00",
            "type": {
                "name": "STATUS_DELAYED",
                "state": state,
                "completed": False,
                "description": "Weather delay",
                "detail": "Weather delay",
                "shortDetail": "Delayed",
            },
        }
    )
    make_client(monkeypatch, payload)

    game = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard().games[0]

    assert game.status.state is expected_state
    assert game.status.score == expected_score
    if state == "pre":
        assert game.status.period is None
        assert game.status.clock is None
    else:
        assert game.status.period == 2
        assert game.status.clock == "7:11"


def test_changed_event_date_is_returned_as_the_latest_kickoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_scoreboard_scheduled.json")
    payload["events"][0]["date"] = "2026-09-11T00:20Z"
    make_client(monkeypatch, payload)

    game = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard().games[0]

    assert game.kickoff_at == datetime(2026, 9, 11, 0, 20, tzinfo=UTC)


def test_postponed_event_can_have_unknown_kickoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_scoreboard_scheduled.json")
    event = payload["events"][0]
    event["date"] = None
    event["status"]["type"] = {
        "name": "STATUS_POSTPONED",
        "state": "pre",
        "completed": False,
        "description": "Postponed",
        "detail": "Postponed",
        "shortDetail": "Postponed",
    }
    make_client(monkeypatch, payload)

    game = EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard().games[0]

    assert game.status.state is GameState.POSTPONED
    assert game.kickoff_at is None


def test_rejects_calendar_metadata_for_a_different_season(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_scoreboard_scheduled.json")
    payload["leagues"][0]["season"]["year"] = 2025
    make_client(monkeypatch, payload)

    with pytest.raises(ProviderDataError, match="league season does not match"):
        EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard()


def test_invalid_required_event_field_is_provider_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_scoreboard_scheduled.json")
    del payload["events"][0]["id"]
    make_client(monkeypatch, payload)

    with pytest.raises(ProviderDataError) as error:
        EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard()

    assert error.value.provider == "espn"
    assert error.value.operation == "scoreboard"


def test_explicit_request_rejects_mismatched_source_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_client(
        monkeypatch,
        load_fixture("espn_scoreboard_scheduled.json"),
    )

    with pytest.raises(ProviderDataError, match="does not match"):
        EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard(
            SeasonWeek(2026, SeasonPhase.POSTSEASON, 1)
        )


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_rate_limit_and_server_errors_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    response = Mock(status_code=status_code)
    request = Mock(return_value=response)
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_scoreboard.requests.get",
        request,
    )

    with pytest.raises(ProviderUnavailableError):
        EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard()

    response.json.assert_not_called()


def test_timeout_is_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Mock(side_effect=requests.Timeout)
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_scoreboard.requests.get",
        request,
    )

    with pytest.raises(ProviderTransportError):
        EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard()


def test_invalid_json_is_data_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock(status_code=200)
    response.json.side_effect = ValueError
    monkeypatch.setattr(
        "backend.nospoil_nfl.providers.espn_scoreboard.requests.get",
        Mock(return_value=response),
    )

    with pytest.raises(ProviderDataError):
        EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard()


def test_duplicate_valid_overall_records_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture("espn_scoreboard_scheduled.json")
    payload["events"][0]["competitions"][0]["competitors"][0]["records"].append(
        {"name": "overall", "type": "total", "summary": "0-0"}
    )
    make_client(monkeypatch, payload)

    with pytest.raises(ProviderDataError, match="multiple valid overall records"):
        EspnScoreboardClient(clock=lambda: OBSERVED_AT).fetch_scoreboard()
