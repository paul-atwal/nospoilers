from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from backend.nospoil_nfl.game import DomainValidationError
from backend.nospoil_nfl.sync.handler import handle_event
from backend.nospoil_nfl.sync.models import SyncMode, SyncResult


NOW = datetime(2026, 9, 10, 17, 0, tzinfo=UTC)


def test_handler_validates_event_and_returns_deployment_friendly_result() -> None:
    service = Mock()
    service.run.return_value = SyncResult(
        mode=SyncMode.LIVE_TICK,
        scoreboard_requests=1,
        games_updated=2,
    )

    result = handle_event(
        {
            "mode": "live_tick",
            "scheduledTime": "2026-09-10T17:00:00Z",
            "season": 2026,
        },
        service,
        clock=lambda: NOW,
    )

    assert result["scoreboardRequests"] == 1
    assert result["gamesUpdated"] == 2
    parsed_event = service.run.call_args.args[0]
    assert parsed_event.mode is SyncMode.LIVE_TICK
    assert parsed_event.season == 2026


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
        handle_event(raw_event, Mock(), clock=lambda: NOW)
