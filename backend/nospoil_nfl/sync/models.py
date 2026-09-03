"""Validated inputs and results for ESPN sync jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Mapping

from ..game import DomainValidationError, GameId


class SyncMode(StrEnum):
    """Supported schedule and live job modes."""

    MANUAL_PRESEASON = "manual_preseason"
    DAILY_NEAR_TERM = "daily_near_term"
    WEEKLY_REMAINING = "weekly_remaining"
    LIVE_TICK = "live_tick"


_MAX_EVENT_AGES = {
    SyncMode.MANUAL_PRESEASON: timedelta(hours=1),
    SyncMode.DAILY_NEAR_TERM: timedelta(minutes=15),
    SyncMode.WEEKLY_REMAINING: timedelta(minutes=15),
    SyncMode.LIVE_TICK: timedelta(minutes=2),
}


@dataclass(frozen=True, slots=True)
class SyncEvent:
    """One explicit, validated invocation of a sync mode."""

    mode: SyncMode
    scheduled_at: datetime
    season: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SyncMode):
            raise DomainValidationError("sync mode must be a SyncMode")
        if not isinstance(
            self.scheduled_at, datetime
        ) or self.scheduled_at.utcoffset() != timedelta(0):
            raise DomainValidationError("scheduled_at must be a UTC datetime")
        if self.season is not None and (
            isinstance(self.season, bool)
            or not isinstance(self.season, int)
            or self.season < 1
        ):
            raise DomainValidationError("season must be an integer >= 1")

        needs_season = self.mode in {
            SyncMode.MANUAL_PRESEASON,
            SyncMode.LIVE_TICK,
        }
        if needs_season and self.season is None:
            raise DomainValidationError(f"{self.mode.value} requires season")
        if not needs_season and self.season is not None:
            raise DomainValidationError(f"{self.mode.value} derives season from ESPN")

    @classmethod
    def parse(cls, value: object) -> SyncEvent:
        """Parse the small EventBridge/manual event contract."""
        if not isinstance(value, Mapping):
            raise DomainValidationError("sync event must be an object")
        allowed = {"mode", "scheduledTime", "season"}
        unknown = set(value) - allowed
        if unknown:
            raise DomainValidationError(
                f"sync event has unsupported fields: {', '.join(sorted(unknown))}"
            )
        try:
            mode = SyncMode(value.get("mode"))
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("sync event mode is unsupported") from exc
        scheduled_at = _parse_timestamp(value.get("scheduledTime"))
        return cls(mode=mode, scheduled_at=scheduled_at, season=value.get("season"))

    def is_old(self, now: datetime) -> bool:
        """Return whether this event is too old to remain useful."""
        _require_utc_now(now)
        return now - self.scheduled_at > _MAX_EVENT_AGES[self.mode]

    def is_from_future(self, now: datetime) -> bool:
        """Return whether the event timestamp is implausibly ahead of the clock."""
        _require_utc_now(now)
        return self.scheduled_at - now > timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Operational summary of one sync invocation."""

    mode: SyncMode
    ignored_old_event: bool = False
    scoreboard_requests: int = 0
    games_created: int = 0
    games_updated: int = 0
    stale_writes: int = 0
    rejected_transitions: int = 0
    provisional_rating_game_ids: tuple[GameId, ...] = ()


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError("scheduledTime must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainValidationError("scheduledTime must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DomainValidationError("scheduledTime must include a timezone")
    return parsed.astimezone(UTC)


def _require_utc_now(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise DomainValidationError("now must be a UTC datetime")


__all__ = ["SyncEvent", "SyncMode", "SyncResult"]
