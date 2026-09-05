"""Immutable game, season, record, and rating domain values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
import math
from typing import NewType


GameId = NewType("GameId", str)


class DomainValidationError(ValueError):
    """Raised when values cannot form a valid domain object."""


class SeasonPhase(StrEnum):
    """Source-defined phase within an NFL season."""

    PRESEASON = "preseason"
    REGULAR_SEASON = "regular_season"
    POSTSEASON = "postseason"


class GameState(StrEnum):
    """Canonical game lifecycle states."""

    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    IN_PROGRESS = "in_progress"
    DELAYED = "delayed"
    FINAL = "final"
    CANCELLED = "cancelled"


class GameOutcome(StrEnum):
    """Outcome derived from a final score."""

    HOME_WIN = "home_win"
    AWAY_WIN = "away_win"
    TIE = "tie"


class RecordScope(StrEnum):
    """Games included in a team record snapshot."""

    PRESEASON = "preseason"
    REGULAR_SEASON = "regular_season"


class RatingState(StrEnum):
    """Canonical excitement-rating lifecycle states."""

    PENDING = "pending"
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    UNAVAILABLE = "unavailable"


class RatingSource(StrEnum):
    """Normalized source used to calculate a rating."""

    ESPN = "espn"
    NFLVERSE = "nflverse"


def _require_int(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DomainValidationError(f"{name} must be an integer >= {minimum}")


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be non-empty text")


def _require_optional_text(name: str, value: str | None) -> None:
    if value is not None:
        _require_text(name, value)


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise DomainValidationError(f"{name} must be a timezone-aware UTC datetime")


@dataclass(frozen=True, slots=True)
class SeasonWeek:
    """One source week in one phase of one NFL season."""

    season: int
    phase: SeasonPhase
    week: int

    def __post_init__(self) -> None:
        _require_int("season", self.season, minimum=1)
        if not isinstance(self.phase, SeasonPhase):
            raise DomainValidationError("phase must be a SeasonPhase")
        _require_int("week", self.week, minimum=1)


@dataclass(frozen=True, slots=True)
class Score:
    """Home and away points observed for a game."""

    home: int
    away: int

    def __post_init__(self) -> None:
        _require_int("home score", self.home, minimum=0)
        _require_int("away score", self.away, minimum=0)


@dataclass(frozen=True, slots=True)
class GameStatus:
    """Canonical state plus live and final status details."""

    state: GameState
    detail: str | None = None
    period: int | None = None
    clock: str | None = None
    score: Score | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, GameState):
            raise DomainValidationError("state must be a GameState")
        _require_optional_text("status detail", self.detail)
        _require_optional_text("clock", self.clock)

        if self.period is not None:
            _require_int("period", self.period, minimum=0)
        if self.score is not None and not isinstance(self.score, Score):
            raise DomainValidationError("score must be a Score")
        if self.clock is not None and self.period is None:
            raise DomainValidationError("clock requires a period")

        if self.state in {GameState.SCHEDULED, GameState.POSTPONED}:
            if any(
                value is not None for value in (self.period, self.clock, self.score)
            ):
                raise DomainValidationError(
                    f"{self.state.value} status cannot contain live game data"
                )
        elif self.state is GameState.IN_PROGRESS:
            if self.period is None or self.period < 1 or self.score is None:
                raise DomainValidationError(
                    "in-progress status requires a positive period and score"
                )
        elif self.state is GameState.FINAL and self.score is None:
            raise DomainValidationError("final status requires a score")
        elif self.state in {GameState.DELAYED, GameState.CANCELLED}:
            if self.period is not None and self.score is None:
                raise DomainValidationError(
                    f"{self.state.value} status with a period requires a score"
                )

    @property
    def has_started(self) -> bool:
        """Return whether the canonical status shows that play began."""
        return self.score is not None or self.state is GameState.IN_PROGRESS


@dataclass(frozen=True, slots=True)
class TeamRecord:
    """Structured team record."""

    wins: int
    losses: int
    ties: int = 0

    def __post_init__(self) -> None:
        _require_int("wins", self.wins, minimum=0)
        _require_int("losses", self.losses, minimum=0)
        _require_int("ties", self.ties, minimum=0)


@dataclass(frozen=True, slots=True)
class RecordSnapshot:
    """A scoped team record prepared at one point in time."""

    record: TeamRecord
    scope: RecordScope
    snapshot_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.record, TeamRecord):
            raise DomainValidationError("record must be a TeamRecord")
        if not isinstance(self.scope, RecordScope):
            raise DomainValidationError("scope must be a RecordScope")
        _require_utc("record snapshot_at", self.snapshot_at)


@dataclass(frozen=True, slots=True)
class TeamGameSnapshot:
    """Historical team display fields and game-specific records."""

    team_id: str
    display_name: str
    abbreviation: str
    logo_key: str | None
    pregame_record: RecordSnapshot | None
    postgame_record: RecordSnapshot | None = None

    def __post_init__(self) -> None:
        _require_text("team_id", self.team_id)
        _require_text("display_name", self.display_name)
        _require_text("abbreviation", self.abbreviation)
        _require_optional_text("logo_key", self.logo_key)

        if (
            self.pregame_record is not None
            and not isinstance(self.pregame_record, RecordSnapshot)
        ):
            raise DomainValidationError("pregame_record must be a RecordSnapshot")
        if self.postgame_record is None:
            return
        if not isinstance(self.postgame_record, RecordSnapshot):
            raise DomainValidationError("postgame_record must be a RecordSnapshot")
        if self.pregame_record is None:
            return
        if self.postgame_record.scope is not self.pregame_record.scope:
            raise DomainValidationError("pregame and postgame record scopes must match")
        if self.postgame_record.snapshot_at < self.pregame_record.snapshot_at:
            raise DomainValidationError("postgame record cannot predate pregame record")


@dataclass(frozen=True, slots=True)
class OddsSnapshot:
    """Last accepted pregame odds display value."""

    details: str
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_text("odds details", self.details)
        _require_utc("odds updated_at", self.updated_at)


@dataclass(frozen=True, slots=True)
class RatingRetry:
    """Retry metadata for the next rating-state improvement."""

    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        _require_int("rating attempt_count", self.attempt_count, minimum=0)
        if self.next_attempt_at is not None:
            _require_utc("rating next_attempt_at", self.next_attempt_at)
        _require_optional_text("rating last_error", self.last_error)

        if self.attempt_count == 0 and self.last_error is not None:
            raise DomainValidationError("a rating error requires a failed attempt")
        if self.attempt_count > 0 and self.last_error is None:
            raise DomainValidationError("a failed rating attempt requires last_error")


@dataclass(frozen=True, slots=True)
class GameRating:
    """Current rating value, provenance, lifecycle state, and retry data."""

    state: RatingState
    retry: RatingRetry
    score: float | None = None
    source: RatingSource | None = None
    model_version: str | None = None
    input_hash: str | None = None
    calculated_at: datetime | None = None
    confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, RatingState):
            raise DomainValidationError("rating state must be a RatingState")
        if not isinstance(self.retry, RatingRetry):
            raise DomainValidationError("rating retry must be RatingRetry")
        if self.source is not None and not isinstance(self.source, RatingSource):
            raise DomainValidationError("rating source must be a RatingSource")
        _require_optional_text("rating model_version", self.model_version)
        _require_optional_text("rating input_hash", self.input_hash)

        if self.score is not None:
            if (
                isinstance(self.score, bool)
                or not isinstance(self.score, (int, float))
                or not math.isfinite(self.score)
                or not 0.0 <= self.score <= 10.0
            ):
                raise DomainValidationError(
                    "rating score must be from 0.0 through 10.0"
                )
        if self.calculated_at is not None:
            _require_utc("rating calculated_at", self.calculated_at)
        if self.confirmed_at is not None:
            _require_utc("rating confirmed_at", self.confirmed_at)

        if self.state is RatingState.PENDING:
            self._require_no_rating_value("pending")
        elif self.state is RatingState.PROVISIONAL:
            self._require_complete_rating(RatingSource.ESPN)
            if self.confirmed_at is not None:
                raise DomainValidationError("provisional rating cannot be confirmed")
        elif self.state is RatingState.CONFIRMED:
            self._require_complete_rating(RatingSource.NFLVERSE)
            if self.confirmed_at is None:
                raise DomainValidationError("confirmed rating requires confirmed_at")
            if self.confirmed_at < self.calculated_at:
                raise DomainValidationError("confirmed_at cannot predate calculated_at")
            if self.retry != RatingRetry():
                raise DomainValidationError("confirmed rating cannot have retry work")
        else:
            self._require_no_rating_value("unavailable")
            if self.retry.attempt_count < 1 or self.retry.last_error is None:
                raise DomainValidationError(
                    "unavailable rating requires at least one failed attempt"
                )
            if self.retry.next_attempt_at is not None:
                raise DomainValidationError(
                    "unavailable rating cannot have an automatic next attempt"
                )

    def _require_no_rating_value(self, state_name: str) -> None:
        if any(
            value is not None
            for value in (
                self.score,
                self.source,
                self.model_version,
                self.input_hash,
                self.calculated_at,
                self.confirmed_at,
            )
        ):
            raise DomainValidationError(
                f"{state_name} rating cannot contain calculated rating data"
            )

    def _require_complete_rating(self, expected_source: RatingSource) -> None:
        if self.score is None:
            raise DomainValidationError(f"{self.state.value} rating requires a score")
        if self.source is not expected_source:
            raise DomainValidationError(
                f"{self.state.value} rating requires source {expected_source.value}"
            )
        if self.model_version is None or self.input_hash is None:
            raise DomainValidationError(
                f"{self.state.value} rating requires model version and input hash"
            )
        if self.calculated_at is None:
            raise DomainValidationError(
                f"{self.state.value} rating requires calculated_at"
            )


@dataclass(frozen=True, slots=True)
class Game:
    """Complete domain record for one scheduled NFL game."""

    game_id: GameId
    espn_id: str
    nflverse_id: str | None
    season_week: SeasonWeek
    kickoff_at: datetime | None
    home: TeamGameSnapshot
    away: TeamGameSnapshot
    status: GameStatus
    rating: GameRating
    schedule_checked_at: datetime
    schedule_updated_at: datetime
    live_source_checked_at: datetime | None = None
    live_state_updated_at: datetime | None = None
    broadcaster: str | None = None
    odds: OddsSnapshot | None = None
    confirmation_retry: RatingRetry = field(default_factory=RatingRetry)

    def __post_init__(self) -> None:
        _require_text("game_id", self.game_id)
        _require_text("espn_id", self.espn_id)
        _require_optional_text("nflverse_id", self.nflverse_id)
        if not isinstance(self.season_week, SeasonWeek):
            raise DomainValidationError("season_week must be a SeasonWeek")
        if not isinstance(self.home, TeamGameSnapshot):
            raise DomainValidationError("home must be a TeamGameSnapshot")
        if not isinstance(self.away, TeamGameSnapshot):
            raise DomainValidationError("away must be a TeamGameSnapshot")
        if not isinstance(self.status, GameStatus):
            raise DomainValidationError("status must be a GameStatus")
        if not isinstance(self.rating, GameRating):
            raise DomainValidationError("rating must be a GameRating")
        if not isinstance(self.confirmation_retry, RatingRetry):
            raise DomainValidationError("confirmation_retry must be a RatingRetry")

        if self.kickoff_at is not None:
            _require_utc("kickoff_at", self.kickoff_at)
        if self.status.state not in {GameState.POSTPONED, GameState.CANCELLED}:
            if self.kickoff_at is None:
                raise DomainValidationError(
                    f"{self.status.state.value} game requires kickoff_at"
                )

        if self.home.team_id == self.away.team_id:
            raise DomainValidationError("home and away teams must be different")

        self._validate_record_scope()
        if self.status.state is not GameState.FINAL and any(
            team.postgame_record is not None for team in (self.home, self.away)
        ):
            raise DomainValidationError(
                "only a final game can have postgame record snapshots"
            )
        if self.status.state is not GameState.FINAL:
            if self.rating.state is not RatingState.PENDING:
                raise DomainValidationError(
                    "only a final game can have a completed rating state"
                )
            if self.confirmation_retry != RatingRetry():
                raise DomainValidationError(
                    "only a final game can have confirmation retry work"
                )
        if self.rating.state is RatingState.CONFIRMED:
            if self.confirmation_retry != RatingRetry():
                raise DomainValidationError(
                    "confirmed game rating cannot have confirmation retry work"
                )

        _require_utc("schedule_checked_at", self.schedule_checked_at)
        _require_utc("schedule_updated_at", self.schedule_updated_at)
        if self.schedule_updated_at > self.schedule_checked_at:
            raise DomainValidationError(
                "schedule_updated_at cannot follow schedule_checked_at"
            )

        if self.live_source_checked_at is not None:
            _require_utc("live_source_checked_at", self.live_source_checked_at)
        if self.live_state_updated_at is not None:
            _require_utc("live_state_updated_at", self.live_state_updated_at)
            if self.live_source_checked_at is None:
                raise DomainValidationError(
                    "live_state_updated_at requires live_source_checked_at"
                )
            if self.live_state_updated_at > self.live_source_checked_at:
                raise DomainValidationError(
                    "live_state_updated_at cannot follow live_source_checked_at"
                )

        _require_optional_text("broadcaster", self.broadcaster)
        if self.odds is not None and not isinstance(self.odds, OddsSnapshot):
            raise DomainValidationError("odds must be an OddsSnapshot")

    def _validate_record_scope(self) -> None:
        expected_scope = {
            SeasonPhase.PRESEASON: RecordScope.PRESEASON,
            SeasonPhase.REGULAR_SEASON: RecordScope.REGULAR_SEASON,
            SeasonPhase.POSTSEASON: RecordScope.REGULAR_SEASON,
        }[self.season_week.phase]

        for side, team in (("home", self.home), ("away", self.away)):
            if (
                team.pregame_record is not None
                and team.pregame_record.scope is not expected_scope
            ):
                raise DomainValidationError(
                    f"{side} pregame record must use scope {expected_scope.value}"
                )
            if (
                team.postgame_record is not None
                and team.postgame_record.scope is not expected_scope
            ):
                raise DomainValidationError(
                    f"{side} postgame record must use scope {expected_scope.value}"
                )


__all__ = [
    "DomainValidationError",
    "Game",
    "GameId",
    "GameOutcome",
    "GameRating",
    "GameState",
    "GameStatus",
    "OddsSnapshot",
    "RatingRetry",
    "RatingSource",
    "RatingState",
    "RecordScope",
    "RecordSnapshot",
    "Score",
    "SeasonPhase",
    "SeasonWeek",
    "TeamGameSnapshot",
    "TeamRecord",
]
