"""Injected contracts for external schedule and rating data."""

from __future__ import annotations

from typing import Protocol

from ..game.models import GameId, SeasonWeek
from .models import (
    GameSummary,
    NflversePlaySeason,
    NflverseScheduleSeason,
    ScoreboardBatch,
)


class ESPNScoreboardProvider(Protocol):
    """Provide one normalized ESPN scoreboard response."""

    def fetch_scoreboard(
        self,
        season_week: SeasonWeek | None = None,
    ) -> ScoreboardBatch:
        """Return all games in the requested or current scoreboard response."""
        ...


class ESPNGameSummaryProvider(Protocol):
    """Provide one normalized completed-game ESPN summary."""

    def fetch_summary(self, game_id: GameId) -> GameSummary:
        """Return summary data for one ESPN game ID."""
        ...


class NFLVerseScheduleProvider(Protocol):
    """Provide one complete normalized nflverse schedule season."""

    def load_schedule(self, season: int) -> NflverseScheduleSeason:
        """Load all schedule rows for one season."""
        ...


class NFLVersePlayProvider(Protocol):
    """Provide one complete normalized nflverse play season."""

    def load_plays(self, season: int) -> NflversePlaySeason:
        """Load all play observations for one season."""
        ...


__all__ = [
    "ESPNGameSummaryProvider",
    "ESPNScoreboardProvider",
    "NFLVersePlayProvider",
    "NFLVerseScheduleProvider",
]
