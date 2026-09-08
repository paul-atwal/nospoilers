"""Provider-free read API domain services.

This package is deliberately small: the HTTP adapter can depend on these
services without importing the sync, provider, or legacy application runtime.
"""

from .calendar import (
    CALENDAR_SOURCE_URL,
    CALENDAR_VERSION,
    CALENDAR_VERIFIED_AT,
    CalendarEntry,
    CalendarError,
    SeasonCalendar,
    UnknownSeasonError,
    UnknownSeasonWeekError,
)
from .snapshots import (
    ReadSnapshotService,
    SnapshotResult,
    canonical_etag,
)

__all__ = [
    "CALENDAR_SOURCE_URL",
    "CALENDAR_VERSION",
    "CALENDAR_VERIFIED_AT",
    "CalendarEntry",
    "CalendarError",
    "ReadSnapshotService",
    "SeasonCalendar",
    "SnapshotResult",
    "UnknownSeasonError",
    "UnknownSeasonWeekError",
    "canonical_etag",
]
