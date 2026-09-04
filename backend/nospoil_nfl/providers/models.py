"""Immutable values exchanged across external data provider boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from numbers import Real
from typing import NewType

from ..game.models import (
    DomainValidationError,
    GameId,
    GameState,
    GameStatus,
    OddsSnapshot,
    RecordSnapshot,
    Score,
    SeasonWeek,
)


NflverseGameId = NewType("NflverseGameId", str)


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be non-empty text")


def _require_optional_text(name: str, value: str | None) -> None:
    if value is not None:
        _require_text(name, value)


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise DomainValidationError(f"{name} must be a timezone-aware UTC datetime")


def _require_season(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DomainValidationError(f"{name} must be an integer >= 1")


def _require_tuple(name: str, value: object) -> None:
    if not isinstance(value, tuple):
        raise DomainValidationError(f"{name} must be a tuple")


def _require_probability(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise DomainValidationError(
            f"{name} must be a finite number from 0.0 through 1.0"
        )
    probability = float(value)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise DomainValidationError(
            f"{name} must be a finite number from 0.0 through 1.0"
        )


@dataclass(frozen=True, slots=True)
class ScheduleTeam:
    """Normalized team fields and an optional scoped source record."""

    team_id: str
    display_name: str
    abbreviation: str
    logo_key: str | None = None
    record: RecordSnapshot | None = None

    def __post_init__(self) -> None:
        _require_text("team_id", self.team_id)
        _require_text("display_name", self.display_name)
        _require_text("abbreviation", self.abbreviation)
        _require_optional_text("logo_key", self.logo_key)
        if self.record is not None and not isinstance(self.record, RecordSnapshot):
            raise DomainValidationError("record must be a RecordSnapshot")


@dataclass(frozen=True, slots=True)
class ScheduleGame:
    """One normalized ESPN schedule or scoreboard game observation."""

    game_id: GameId
    kickoff_at: datetime | None
    home: ScheduleTeam
    away: ScheduleTeam
    status: GameStatus
    broadcaster: str | None = None
    odds: OddsSnapshot | None = None

    def __post_init__(self) -> None:
        _require_text("game_id", self.game_id)
        if self.kickoff_at is not None:
            _require_utc("kickoff_at", self.kickoff_at)
        if not isinstance(self.home, ScheduleTeam):
            raise DomainValidationError("home must be a ScheduleTeam")
        if not isinstance(self.away, ScheduleTeam):
            raise DomainValidationError("away must be a ScheduleTeam")
        if self.home.team_id == self.away.team_id:
            raise DomainValidationError("home and away teams must be different")
        if not isinstance(self.status, GameStatus):
            raise DomainValidationError("status must be a GameStatus")
        if self.kickoff_at is None and self.status.state not in {
            GameState.POSTPONED,
            GameState.CANCELLED,
        }:
            raise DomainValidationError(
                f"{self.status.state.value} game requires kickoff_at"
            )
        _require_optional_text("broadcaster", self.broadcaster)
        if self.odds is not None and not isinstance(self.odds, OddsSnapshot):
            raise DomainValidationError("odds must be an OddsSnapshot")


@dataclass(frozen=True, slots=True)
class ScoreboardBatch:
    """One normalized ESPN response containing all returned schedule games."""

    observed_at: datetime
    season_week: SeasonWeek
    games: tuple[ScheduleGame, ...]
    known_weeks: tuple[SeasonWeek, ...] = ()

    def __post_init__(self) -> None:
        _require_utc("scoreboard observed_at", self.observed_at)
        if not isinstance(self.season_week, SeasonWeek):
            raise DomainValidationError("season_week must be a SeasonWeek")
        _require_tuple("scoreboard games", self.games)
        if any(not isinstance(game, ScheduleGame) for game in self.games):
            raise DomainValidationError(
                "scoreboard games must contain ScheduleGame values"
            )
        game_ids = [game.game_id for game in self.games]
        if len(game_ids) != len(set(game_ids)):
            raise DomainValidationError("scoreboard game IDs must be unique")
        _require_tuple("scoreboard known_weeks", self.known_weeks)
        if any(not isinstance(week, SeasonWeek) for week in self.known_weeks):
            raise DomainValidationError(
                "scoreboard known_weeks must contain SeasonWeek values"
            )
        if any(week.season != self.season_week.season for week in self.known_weeks):
            raise DomainValidationError(
                "scoreboard known weeks must match the scoreboard season"
            )
        if len(self.known_weeks) != len(set(self.known_weeks)):
            raise DomainValidationError("scoreboard known weeks must be unique")


@dataclass(frozen=True, slots=True)
class GameSummary:
    """Normalized ESPN summary data available to a later rating service."""

    game_id: GameId
    win_probability_history: tuple[float, ...]
    final_score: Score
    is_overtime: bool

    def __post_init__(self) -> None:
        _require_text("game_id", self.game_id)
        _require_tuple("win_probability_history", self.win_probability_history)
        if len(self.win_probability_history) < 2:
            raise DomainValidationError(
                "win_probability_history must contain at least two values"
            )
        for value in self.win_probability_history:
            _require_probability("win probability", value)
        if not isinstance(self.final_score, Score):
            raise DomainValidationError("final_score must be a Score")
        if not isinstance(self.is_overtime, bool):
            raise DomainValidationError("is_overtime must be a bool")


@dataclass(frozen=True, slots=True)
class NflverseScheduleGame:
    """One normalized nflverse schedule row used by later mapping work."""

    season_week: SeasonWeek
    nflverse_game_id: NflverseGameId
    espn_id: str | None
    final_score: Score | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.season_week, SeasonWeek):
            raise DomainValidationError("season_week must be a SeasonWeek")
        _require_text("nflverse_game_id", self.nflverse_game_id)
        _require_optional_text("espn_id", self.espn_id)
        if self.final_score is not None and not isinstance(self.final_score, Score):
            raise DomainValidationError("final_score must be a Score")


@dataclass(frozen=True, slots=True)
class NflverseScheduleSeason:
    """One complete normalized nflverse schedule for a season."""

    season: int
    games: tuple[NflverseScheduleGame, ...]

    def __post_init__(self) -> None:
        _require_season("season", self.season)
        _require_tuple("nflverse schedule games", self.games)
        if any(
            not isinstance(game, NflverseScheduleGame)
            for game in self.games
        ):
            raise DomainValidationError(
                "nflverse schedule games must contain "
                "NflverseScheduleGame values"
            )
        if any(game.season_week.season != self.season for game in self.games):
            raise DomainValidationError(
                "nflverse schedule game seasons must match the containing season"
            )

        nflverse_ids = [game.nflverse_game_id for game in self.games]
        if len(nflverse_ids) != len(set(nflverse_ids)):
            raise DomainValidationError("nflverse schedule game IDs must be unique")

        espn_ids = [game.espn_id for game in self.games if game.espn_id is not None]
        if len(espn_ids) != len(set(espn_ids)):
            raise DomainValidationError("nflverse schedule ESPN IDs must be unique")


@dataclass(frozen=True, slots=True)
class NflversePlay:
    """One normalized nflverse play observation."""

    nflverse_game_id: NflverseGameId
    play_number: int
    period: int | None
    home_win_probability: float | None
    score: Score | None

    def __post_init__(self) -> None:
        _require_text("nflverse_game_id", self.nflverse_game_id)
        if (
            isinstance(self.play_number, bool)
            or not isinstance(self.play_number, int)
            or self.play_number < 0
        ):
            raise DomainValidationError("play_number must be an integer >= 0")
        if self.period is not None and (
            isinstance(self.period, bool)
            or not isinstance(self.period, int)
            or self.period < 1
        ):
            raise DomainValidationError("period must be an integer >= 1")
        if self.home_win_probability is not None:
            _require_probability(
                "home_win_probability",
                self.home_win_probability,
            )
        if self.score is not None and not isinstance(self.score, Score):
            raise DomainValidationError("score must be a Score")


@dataclass(frozen=True, slots=True)
class NflversePlaySeason:
    """One complete normalized nflverse play collection for a season."""

    season: int
    plays: tuple[NflversePlay, ...]

    def __post_init__(self) -> None:
        _require_season("season", self.season)
        _require_tuple("nflverse plays", self.plays)
        if any(not isinstance(play, NflversePlay) for play in self.plays):
            raise DomainValidationError(
                "nflverse plays must contain NflversePlay values"
            )
        play_keys = [
            (play.nflverse_game_id, play.play_number)
            for play in self.plays
        ]
        if len(play_keys) != len(set(play_keys)):
            raise DomainValidationError(
                "nflverse play game IDs and play numbers must be unique"
            )


__all__ = [
    "GameSummary",
    "NflverseGameId",
    "NflversePlay",
    "NflversePlaySeason",
    "NflverseScheduleGame",
    "NflverseScheduleSeason",
    "ScheduleGame",
    "ScheduleTeam",
    "ScoreboardBatch",
]
