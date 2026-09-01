"""Typed ESPN-owned updates for existing game records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .models import (
    DomainValidationError,
    GameId,
    GameStatus,
    OddsSnapshot,
    RecordSnapshot,
    SeasonWeek,
)


class _Unset:
    """Mark an optional update field that the source did not supply."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


class WriteResult(StrEnum):
    """Result of one conditional write of source-owned game data."""

    APPLIED = "applied"
    STALE = "stale"


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be non-empty text")


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise DomainValidationError(f"{name} must be a timezone-aware UTC datetime")


@dataclass(frozen=True, slots=True)
class TeamScheduleUpdate:
    """Schedule-owned display and record values for one team in one game.

    ``UNSET`` keeps an optional stored field. ``None`` removes it. A concrete
    value replaces it.
    """

    team_id: str
    display_name: str
    abbreviation: str
    logo_key: str | None | _Unset = UNSET
    pregame_record: RecordSnapshot | None | _Unset = UNSET
    postgame_record: RecordSnapshot | None | _Unset = UNSET

    def __post_init__(self) -> None:
        _require_text("team_id", self.team_id)
        _require_text("display_name", self.display_name)
        _require_text("abbreviation", self.abbreviation)
        if self.logo_key is not UNSET and self.logo_key is not None:
            _require_text("logo_key", self.logo_key)
        if self.pregame_record is not UNSET and self.pregame_record is not None:
            if not isinstance(self.pregame_record, RecordSnapshot):
                raise DomainValidationError("pregame_record must be a RecordSnapshot")
        if self.postgame_record is not UNSET and self.postgame_record is not None:
            if not isinstance(self.postgame_record, RecordSnapshot):
                raise DomainValidationError("postgame_record must be a RecordSnapshot")


@dataclass(frozen=True, slots=True)
class ScheduleUpdate:
    """One normalized ESPN schedule observation for an existing game."""

    game_id: GameId
    observed_at: datetime
    season_week: SeasonWeek
    kickoff_at: datetime | None
    home: TeamScheduleUpdate
    away: TeamScheduleUpdate
    status: GameStatus | None = None
    broadcaster: str | None | _Unset = UNSET
    odds: OddsSnapshot | None | _Unset = UNSET

    def __post_init__(self) -> None:
        _require_text("game_id", self.game_id)
        _require_utc("observed_at", self.observed_at)
        if not isinstance(self.season_week, SeasonWeek):
            raise DomainValidationError("season_week must be a SeasonWeek")
        if self.kickoff_at is not None:
            _require_utc("kickoff_at", self.kickoff_at)
        if not isinstance(self.home, TeamScheduleUpdate):
            raise DomainValidationError("home must be a TeamScheduleUpdate")
        if not isinstance(self.away, TeamScheduleUpdate):
            raise DomainValidationError("away must be a TeamScheduleUpdate")
        if self.home.team_id == self.away.team_id:
            raise DomainValidationError("home and away teams must be different")
        if self.status is not None and not isinstance(self.status, GameStatus):
            raise DomainValidationError("status must be a GameStatus")
        if self.broadcaster is not UNSET and self.broadcaster is not None:
            _require_text("broadcaster", self.broadcaster)
        if self.odds is not UNSET and self.odds is not None:
            if not isinstance(self.odds, OddsSnapshot):
                raise DomainValidationError("odds must be an OddsSnapshot")


@dataclass(frozen=True, slots=True)
class LiveStatusUpdate:
    """One normalized ESPN live-status observation for an existing game."""

    game_id: GameId
    observed_at: datetime
    status: GameStatus

    def __post_init__(self) -> None:
        _require_text("game_id", self.game_id)
        _require_utc("observed_at", self.observed_at)
        if not isinstance(self.status, GameStatus):
            raise DomainValidationError("status must be a GameStatus")


__all__ = [
    "LiveStatusUpdate",
    "ScheduleUpdate",
    "TeamScheduleUpdate",
    "UNSET",
    "WriteResult",
]
