"""AWS Lambda entry point for ESPN sync and provisional-rating work."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
from typing import Callable

from ..rating.provisional import ProvisionalRatingService
from .models import SyncEvent, SyncMode, SyncResult
from .service import ScheduleSyncService


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
MAX_ESPN_TIMEOUT_SECONDS = 8.0
MAX_ESPN_SUMMARY_TIMEOUT_SECONDS = 5.0


def handle_event(
    raw_event: object,
    service: ScheduleSyncService,
    provisional_ratings: ProvisionalRatingService,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    """Validate and run one event with injected dependencies."""
    parsed_event: SyncEvent | None = None
    try:
        parsed_event = SyncEvent.parse(raw_event)
        now = clock()
        result = service.run(parsed_event, now=now)
        if parsed_event.mode is SyncMode.LIVE_TICK:
            provisional_ratings.rate_due(
                result.provisional_rating_game_ids,
                now=now,
            )
    except Exception as exc:
        mode = parsed_event.mode.value if parsed_event is not None else "invalid"
        scheduled_time = (
            parsed_event.scheduled_at.isoformat() if parsed_event is not None else None
        )
        LOGGER.error(
            json.dumps(
                {
                    "event": "sync_failed",
                    "mode": mode,
                    "scheduled_time": scheduled_time,
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise
    return _result_payload(result)


def lambda_handler(event: object, context: object) -> dict[str, object]:
    """Run the ESPN sync Lambda from EventBridge or manual invocation."""
    import boto3

    from ..game.dynamodb_repository import DynamoGameRepository
    from ..providers import EspnGameSummaryClient, EspnScoreboardClient

    del context
    table_name = _required_environment("NOSPOIL_GAMES_TABLE")
    index_name = os.environ.get("NOSPOIL_SCHEDULE_INDEX", "season-schedule-index")
    timeout = _bounded_positive_float_environment(
        "NOSPOIL_ESPN_TIMEOUT_SECONDS",
        MAX_ESPN_TIMEOUT_SECONDS,
        maximum=MAX_ESPN_TIMEOUT_SECONDS,
    )
    summary_timeout = _bounded_positive_float_environment(
        "NOSPOIL_ESPN_SUMMARY_TIMEOUT_SECONDS",
        MAX_ESPN_SUMMARY_TIMEOUT_SECONDS,
        maximum=MAX_ESPN_SUMMARY_TIMEOUT_SECONDS,
    )
    table = boto3.resource("dynamodb").Table(table_name)
    repository = DynamoGameRepository(table, index_name=index_name)
    service = ScheduleSyncService(
        repository,
        EspnScoreboardClient(timeout_seconds=timeout),
        logger=LOGGER,
    )
    provisional_ratings = ProvisionalRatingService(
        repository,
        EspnGameSummaryClient(timeout_seconds=summary_timeout),
        logger=LOGGER,
    )
    return handle_event(event, service, provisional_ratings)


def _result_payload(result: SyncResult) -> dict[str, object]:
    return {
        "mode": result.mode.value,
        "ignoredOldEvent": result.ignored_old_event,
        "scoreboardRequests": result.scoreboard_requests,
        "gamesCreated": result.games_created,
        "gamesUpdated": result.games_updated,
        "staleWrites": result.stale_writes,
        "rejectedTransitions": result.rejected_transitions,
        "provisionalRatingGameIds": [
            str(game_id) for game_id in result.provisional_rating_game_ids
        ],
    }


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} must be configured")
    return value.strip()


def _bounded_positive_float_environment(
    name: str,
    default: float,
    *,
    maximum: float,
) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    if value > maximum:
        raise RuntimeError(f"{name} must be no greater than {maximum:g}")
    return value


__all__ = ["handle_event", "lambda_handler"]
