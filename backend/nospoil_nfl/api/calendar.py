"""The checked-in, source-verified season calendar used by the read API.

Calendar ownership intentionally lives here rather than in a provider or a
database table.  Updating a season is a reviewed code change: the source URL
and the date on which it was checked are kept beside the data being shipped.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from ..game.models import DomainValidationError, SeasonPhase, SeasonWeek


CALENDAR_VERSION = "espn-2020-2026-verified-2026-09-07"
CALENDAR_SOURCE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?dates={season}"
)
CALENDAR_VERIFIED_AT = "2026-09-07T00:00:00Z"

_PHASE_ORDER = {
    SeasonPhase.PRESEASON: 0,
    SeasonPhase.REGULAR_SEASON: 1,
    SeasonPhase.POSTSEASON: 2,
}


class CalendarError(ValueError):
    """Base class for a calendar/domain selection error."""


class UnknownSeasonError(CalendarError):
    """The requested season is not in the checked-in calendar."""


class UnknownSeasonWeekError(CalendarError):
    """The requested phase/week is not in the checked-in calendar."""


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """One known week and its UTC selection boundary."""

    season_week: SeasonWeek
    starts_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.season_week, SeasonWeek):
            raise DomainValidationError("calendar season_week must be a SeasonWeek")
        if (
            not isinstance(self.starts_at, datetime)
            or self.starts_at.tzinfo is None
            or self.starts_at.utcoffset().total_seconds() != 0
        ):
            raise DomainValidationError("calendar starts_at must be UTC")


def _utc(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _default_entries() -> tuple[CalendarEntry, ...]:
    """Return the exact 2026 boundaries observed in the ESPN calendar."""
    entries: list[CalendarEntry] = []
    for week, day in enumerate((6, 13, 20, 27), start=1):
        entries.append(
            CalendarEntry(
                SeasonWeek(2026, SeasonPhase.PRESEASON, week),
                _utc(2026, 8, day, 7),
            )
        )

    for week, day in enumerate((6, 16, 23, 30), start=1):
        entries.append(
            CalendarEntry(
                SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, week),
                _utc(2026, 9, day, 7),
            )
        )
    for week, day in enumerate((7, 14, 21, 28), start=5):
        entries.append(
            CalendarEntry(
                SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, week),
                _utc(2026, 10, day, 7),
            )
        )
    for week, day in enumerate((4, 11, 18, 25), start=9):
        entries.append(
            CalendarEntry(
                SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, week),
                _utc(2026, 11, day, 8),
            )
        )
    for week, day in enumerate((2, 9, 16, 23, 30), start=13):
        entries.append(
            CalendarEntry(
                SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, week),
                _utc(2026, 12, day, 8),
            )
        )
    entries.append(
        CalendarEntry(
            SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 18),
            _utc(2027, 1, 6, 8),
        )
    )
    for week, day in enumerate((13, 20, 27), start=1):
        entries.append(
            CalendarEntry(
                SeasonWeek(2026, SeasonPhase.POSTSEASON, week),
                _utc(2027, 1, day, 8),
            )
        )
    for week, day in enumerate((3, 10), start=4):
        entries.append(
            CalendarEntry(
                SeasonWeek(2026, SeasonPhase.POSTSEASON, week),
                _utc(2027, 2, day, 8),
            )
        )
    return tuple(entries)


def _default_catalogue() -> tuple[SeasonWeek, ...]:
    weeks: list[SeasonWeek] = []
    for season in range(2020, 2027):
        preseason_max = 5 if season == 2020 else 4
        regular_max = 17 if season == 2020 else 18
        for phase, maximum in (
            (SeasonPhase.PRESEASON, preseason_max),
            (SeasonPhase.REGULAR_SEASON, regular_max),
            (SeasonPhase.POSTSEASON, 5),
        ):
            weeks.extend(
                SeasonWeek(season, phase, week)
                for week in range(1, maximum + 1)
            )
    return tuple(weeks)


class SeasonCalendar:
    """Authoritative known-week list and current-week selection policy."""

    def __init__(
        self,
        entries: Iterable[CalendarEntry] | None = None,
        *,
        active_season: int = 2026,
        season_end: datetime | None = None,
        version: str = CALENDAR_VERSION,
        source_url: str = CALENDAR_SOURCE_URL,
        verified_at: str = CALENDAR_VERIFIED_AT,
        catalogue: Iterable[SeasonWeek] | None = None,
    ) -> None:
        self._entries = tuple(_default_entries() if entries is None else entries)
        if not self._entries:
            raise CalendarError("calendar must contain at least one known week")
        starts = [entry.starts_at for entry in self._entries]
        if starts != sorted(starts) or len(set(starts)) != len(starts):
            raise CalendarError("calendar boundaries must be strictly increasing")
        if len({entry.season_week.season for entry in self._entries}) != 1:
            raise CalendarError("calendar must contain one active season")
        if active_season != self._entries[0].season_week.season:
            raise CalendarError("active season must match calendar entries")
        if not isinstance(version, str) or not version:
            raise CalendarError("calendar version must be non-empty text")
        if not isinstance(source_url, str) or not source_url:
            raise CalendarError("calendar source URL must be non-empty text")
        if not isinstance(verified_at, str) or not verified_at.endswith("Z"):
            raise CalendarError("calendar verified_at must be a UTC timestamp")
        self.active_season = active_season
        self.calendar_version = version
        self.source_url = source_url
        self.verified_at = verified_at
        self.season_end = season_end or _utc(2027, 2, 16, 8)
        if self.season_end.tzinfo is None or self.season_end.utcoffset().total_seconds() != 0:
            raise CalendarError("season_end must be UTC")
        if self.season_end < self._entries[-1].starts_at:
            raise CalendarError("season_end cannot precede final week")
        self._starts = tuple(starts)
        self._catalogue = tuple(
            _default_catalogue() if catalogue is None else catalogue
        )
        if not self._catalogue or any(
            not isinstance(week, SeasonWeek) for week in self._catalogue
        ):
            raise CalendarError("catalogue must contain SeasonWeek values")
        if len(set(self._catalogue)) != len(self._catalogue):
            raise CalendarError("catalogue must contain unique known weeks")
        canonical = tuple(
            sorted(
                self._catalogue,
                key=lambda week: (week.season, _PHASE_ORDER[week.phase], week.week),
            )
        )
        if canonical != self._catalogue:
            raise CalendarError("catalogue must be in canonical season/phase/week order")
        active_weeks = frozenset(entry.season_week for entry in self._entries)
        catalogued_active_weeks = frozenset(
            week for week in self._catalogue if week.season == self.active_season
        )
        if catalogued_active_weeks != active_weeks:
            raise CalendarError(
                "catalogue active season must exactly match active calendar entries"
            )
        self._catalogue_set = frozenset(self._catalogue)
        self._readable_seasons = frozenset(
            week.season for week in self._catalogue
        )
        self._active_by_week = {entry.season_week: entry for entry in self._entries}
        self._active_index = {
            entry.season_week: index for index, entry in enumerate(self._entries)
        }

    @property
    def known_weeks(self) -> tuple[SeasonWeek, ...]:
        return self._catalogue

    @property
    def entries(self) -> tuple[CalendarEntry, ...]:
        return self._entries

    def validate_season(self, season: int) -> int:
        if isinstance(season, bool) or not isinstance(season, int):
            raise DomainValidationError("season must be an integer")
        if season not in self._readable_seasons:
            raise UnknownSeasonError(f"unknown season: {season}")
        return season

    def validate_week(
        self,
        season: int,
        phase: SeasonPhase | str,
        week: int,
    ) -> SeasonWeek:
        self.validate_season(season)
        if isinstance(phase, bool):
            raise DomainValidationError("phase must be preseason, regular_season, or postseason")
        try:
            season_phase = phase if isinstance(phase, SeasonPhase) else SeasonPhase(phase)
        except (TypeError, ValueError) as error:
            raise DomainValidationError(
                "phase must be preseason, regular_season, or postseason"
            ) from error
        if isinstance(week, bool) or not isinstance(week, int):
            raise DomainValidationError("week must be an integer")
        season_week = SeasonWeek(season, season_phase, week)
        if season_week not in self._catalogue_set:
            raise UnknownSeasonWeekError(
                f"unknown season week: {season}-{season_phase.value}-{week}"
            )
        return season_week

    def entry(self, season_week: SeasonWeek) -> CalendarEntry:
        try:
            return self._active_by_week[season_week]
        except KeyError as error:
            raise UnknownSeasonWeekError(
                f"unknown season week: {season_week.season}-{season_week.phase.value}-{season_week.week}"
            ) from error

    def current_week(self, now: datetime) -> SeasonWeek:
        if now.tzinfo is None or now.utcoffset().total_seconds() != 0:
            raise DomainValidationError("calendar clock must return UTC")
        index = bisect_right(self._starts, now) - 1
        if index < 0:
            index = 0
        if now >= self.season_end:
            index = len(self._entries) - 1
        return self._entries[index].season_week

    def is_current_or_future(self, season_week: SeasonWeek, now: datetime) -> bool:
        if season_week not in self._catalogue_set:
            raise UnknownSeasonWeekError("unknown season week")
        if season_week.season != self.active_season:
            return False
        current = self.current_week(now)
        return self._active_index[season_week] >= self._active_index[current]

    def bootstrap(self, now: datetime) -> dict[str, object]:
        current = self.current_week(now)
        return {
            "activeSeason": self.active_season,
            "currentWeek": _week_payload(current),
            "knownWeeks": [_week_payload(week) for week in self.known_weeks],
            "calendarVersion": self.calendar_version,
            "pollAfterSeconds": 86400 if now >= self.season_end else 300,
        }


def _week_payload(season_week: SeasonWeek) -> dict[str, object]:
    return {
        "season": season_week.season,
        "phase": season_week.phase.value,
        "week": season_week.week,
    }


__all__ = [
    "CALENDAR_SOURCE_URL",
    "CALENDAR_VERSION",
    "CALENDAR_VERIFIED_AT",
    "CalendarEntry",
    "CalendarError",
    "SeasonCalendar",
    "UnknownSeasonError",
    "UnknownSeasonWeekError",
]
