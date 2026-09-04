from __future__ import annotations

from datetime import UTC, datetime
import logging
from unittest.mock import Mock

import pytest

from backend.nospoil_nfl.game import DomainValidationError, GameId
from backend.nospoil_nfl.sync.handler import LOGGER, handle_event
from backend.nospoil_nfl.sync.models import SyncMode, SyncResult


NOW = datetime(2026, 9, 10, 17, 0, tzinfo=UTC)


def test_sync_logger_emits_required_info_events_by_default() -> None:
    assert LOGGER.isEnabledFor(logging.INFO)


def test_handler_validates_event_and_returns_deployment_friendly_result() -> None:
    service = Mock()
    provisional_ratings = Mock()
    service.run.return_value = SyncResult(
        mode=SyncMode.LIVE_TICK,
        scoreboard_requests=1,
        games_updated=2,
        provisional_rating_game_ids=(GameId("401000001"), GameId("401000002")),
    )

    result = handle_event(
        {
            "mode": "live_tick",
            "scheduledTime": "2026-09-10T17:00:00Z",
            "season": 2026,
        },
        service,
        provisional_ratings,
        clock=lambda: NOW,
    )

    assert result["scoreboardRequests"] == 1
    assert result["gamesUpdated"] == 2
    parsed_event = service.run.call_args.args[0]
    assert parsed_event.mode is SyncMode.LIVE_TICK
    assert parsed_event.season == 2026
    provisional_ratings.rate_due.assert_called_once_with(
        (GameId("401000001"), GameId("401000002")),
        now=NOW,
    )


def test_handler_does_not_add_rating_calls_to_schedule_modes() -> None:
    service = Mock()
    provisional_ratings = Mock()
    service.run.return_value = SyncResult(
        mode=SyncMode.DAILY_NEAR_TERM,
        provisional_rating_game_ids=(GameId("401000001"),),
    )

    handle_event(
        {
            "mode": "daily_near_term",
            "scheduledTime": "2026-09-10T17:00:00Z",
        },
        service,
        provisional_ratings,
        clock=lambda: NOW,
    )

    provisional_ratings.rate_due.assert_not_called()


@pytest.mark.parametrize(
    "raw_event",
    [
        {"mode": "live_tick", "scheduledTime": "2026-09-10T17:00:00Z"},
        {
            "mode": "daily_near_term",
            "scheduledTime": "2026-09-10T17:00:00Z",
            "season": 2026,
        },
        {"mode": "unknown", "scheduledTime": "2026-09-10T17:00:00Z"},
        {
            "mode": "weekly_remaining",
            "scheduledTime": "not-a-time",
        },
    ],
)
def test_handler_rejects_invalid_job_inputs(raw_event: object) -> None:
    with pytest.raises(DomainValidationError):
        handle_event(raw_event, Mock(), Mock(), clock=lambda: NOW)
