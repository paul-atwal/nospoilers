"""Application-facing storage contract for complete game records."""

from __future__ import annotations

from typing import Protocol

from .models import (
    DomainValidationError,
    Game,
    GameId,
    GameRating,
    GameState,
    SeasonWeek,
)
from .rules import can_transition_rating_state
from .updates import LiveStatusUpdate, ScheduleUpdate, WriteResult


class GameRepositoryError(RuntimeError):
    """Raised when durable game storage cannot complete an operation."""


class GameRepositoryDataError(GameRepositoryError):
    """Raised when a stored item cannot become a valid game."""


def validate_rating_update(current: Game, rating: GameRating) -> None:
    """Validate one proposed rating replacement for its current game."""
    if not isinstance(current, Game):
        raise DomainValidationError("current must be a Game")
    if not isinstance(rating, GameRating):
        raise DomainValidationError("rating must be a GameRating")
    if current.status.state is not GameState.FINAL:
        raise DomainValidationError("rating updates require a final game")
    if not can_transition_rating_state(current.rating.state, rating.state):
        raise DomainValidationError("rating update has an invalid rating transition")


class GameRepository(Protocol):
    """Store and retrieve complete game records without storage details."""

    def create_if_absent(self, game: Game) -> bool:
        """Create one game and return whether the item was newly stored."""
        ...

    def get(self, game_id: GameId) -> Game | None:
        """Return one game by its primary identity, if it exists."""
        ...

    def list_week(self, season_week: SeasonWeek) -> list[Game]:
        """Return one complete season week in kickoff order."""
        ...

    def list_season(self, season: int) -> list[Game]:
        """Return one complete season in phase, week, and kickoff order."""
        ...

    def apply_schedule(
        self,
        current: Game,
        update: ScheduleUpdate,
    ) -> WriteResult:
        """Apply one schedule observation when the current item still matches."""
        ...

    def apply_live_status(
        self,
        current: Game,
        update: LiveStatusUpdate,
    ) -> WriteResult:
        """Apply one live-status observation when the current item still matches."""
        ...

    def apply_rating(self, current: Game, rating: GameRating) -> WriteResult:
        """Apply one complete rating when the current item still matches."""
        ...


__all__ = [
    "GameRepository",
    "GameRepositoryDataError",
    "GameRepositoryError",
    "validate_rating_update",
]
