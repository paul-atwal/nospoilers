"""Bounded ESPN provisional-rating orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Callable

from ..game.models import (
    Game,
    GameId,
    GameRating,
    GameState,
    RatingRetry,
    RatingSource,
    RatingState,
)
from ..game.repository import GameRepository
from ..game.rules import derive_final_outcome, rating_input_for_outcome
from ..game.updates import WriteResult
from ..providers.contracts import ESPNGameSummaryProvider
from ..providers.errors import (
    ProviderDataError,
    ProviderError,
    ProviderTransportError,
    ProviderUnavailableError,
)
from ..providers.models import GameSummary
from .calculator import calculate_rating
from .input import RatingInput, hash_rating_input


MAX_PROVISIONAL_GAMES = 2
MAX_REVALIDATION_GAMES = 4
MAX_ATTEMPTS = 4
MIN_REMAINING_TIME_MS = 12_000
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=3), timedelta(minutes=10))
MODEL_VERSION = "rating-v1"


class _SourceFailure(Exception):
    """A safe, stable code for an expected ESPN source failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProvisionalRatingService:
    """Calculate and persist a bounded number of ESPN provisional ratings."""

    def __init__(
        self,
        repository: GameRepository,
        provider: ESPNGameSummaryProvider,
        *,
        calculator: Callable[[RatingInput], float] = calculate_rating,
        logger: logging.Logger | None = None,
        model_version: str = MODEL_VERSION,
        remaining_time_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(model_version, str) or not model_version.strip():
            raise ValueError("model_version must be non-empty text")
        self._repository = repository
        self._provider = provider
        self._calculator = calculator
        self._logger = logger or logging.getLogger(__name__)
        self._model_version = model_version
        self._remaining_time_ms = remaining_time_ms or (lambda: 2**63 - 1)

    def rate_due(
        self,
        game_ids: Iterable[GameId],
        *,
        now: datetime,
    ) -> None:
        """Attempt at most two currently due ESPN provisional ratings."""
        _require_utc(now)
        if not self._has_time_budget(stage="revalidation"):
            return
        current_games = self._current_due_games(game_ids, now)
        for game in current_games[:MAX_PROVISIONAL_GAMES]:
            if not self._rate_one(game, now):
                return

    def _current_due_games(
        self,
        game_ids: Iterable[GameId],
        now: datetime,
    ) -> list[Game]:
        games: list[Game] = []
        seen: set[GameId] = set()
        inspected = 0
        for game_id in game_ids:
            if game_id in seen:
                continue
            if inspected >= MAX_REVALIDATION_GAMES:
                break
            seen.add(game_id)
            inspected += 1
            if not self._has_time_budget(
                stage="revalidation", game_id=str(game_id)
            ):
                break
            current = self._repository.get(game_id)
            if current is None or not _is_due(current, now):
                self._emit(
                    "info",
                    "provisional_rating_skipped",
                    game_id=str(game_id),
                )
                continue
            games.append(current)

        games.sort(key=_ordering_key)
        return games

    def _rate_one(self, current: Game, now: datetime) -> bool:
        attempt = current.rating.retry.attempt_count + 1
        if not self._has_time_budget(
            stage="summary", game_id=str(current.game_id)
        ):
            return False
        try:
            summary = self._provider.fetch_summary(GameId(current.espn_id))
            rating_input = self._validated_input(current, summary)
        except ProviderError as error:
            self._record_expected_failure(
                current,
                now,
                attempt,
                _provider_error_code(error),
            )
            return True
        except _SourceFailure as error:
            self._record_expected_failure(current, now, attempt, error.code)
            return True
        except Exception as error:
            self._emit(
                "error",
                "provisional_rating_failed",
                game_id=str(current.game_id),
                error_type=type(error).__name__,
            )
            raise

        rating = GameRating(
            state=RatingState.PROVISIONAL,
            retry=RatingRetry(),
            score=self._calculator(rating_input),
            source=RatingSource.ESPN,
            model_version=self._model_version,
            input_hash=hash_rating_input(rating_input),
            calculated_at=now,
        )
        result = self._repository.apply_rating(current, rating)
        if result is WriteResult.STALE:
            self._emit(
                "info",
                "provisional_rating_stale_write",
                game_id=str(current.game_id),
            )
            return True
        self._emit(
            "info",
            "provisional_rating_applied",
            game_id=str(current.game_id),
            attempt=attempt,
        )
        return True

    def _validated_input(self, current: Game, summary: GameSummary) -> RatingInput:
        if not isinstance(summary, GameSummary):
            raise _SourceFailure("espn_summary_invalid")
        if summary.game_id != GameId(current.espn_id):
            raise _SourceFailure("espn_summary_game_mismatch")
        if current.status.score != summary.final_score:
            raise _SourceFailure("espn_summary_score_mismatch")
        outcome = derive_final_outcome(current.status)
        return rating_input_for_outcome(summary.win_probability_history, outcome)

    def _record_expected_failure(
        self,
        current: Game,
        now: datetime,
        attempt: int,
        error_code: str,
    ) -> None:
        if attempt >= MAX_ATTEMPTS:
            rating = GameRating(
                state=RatingState.UNAVAILABLE,
                retry=RatingRetry(attempt_count=attempt, last_error=error_code),
            )
        else:
            rating = GameRating(
                state=RatingState.PENDING,
                retry=RatingRetry(
                    attempt_count=attempt,
                    next_attempt_at=now + RETRY_DELAYS[attempt - 1],
                    last_error=error_code,
                ),
            )

        result = self._repository.apply_rating(current, rating)
        if result is WriteResult.STALE:
            self._emit(
                "info",
                "provisional_rating_stale_retry_write",
                game_id=str(current.game_id),
                attempt=attempt,
            )
            return
        self._emit(
            "info",
            "provisional_rating_retry_scheduled"
            if rating.state is RatingState.PENDING
            else "provisional_rating_exhausted",
            game_id=str(current.game_id),
            attempt=attempt,
            error_code=error_code,
        )

    def _emit(self, level: str, event: str, **fields: object) -> None:
        payload = {"event": event, **fields}
        getattr(self._logger, level)(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )

    def _has_time_budget(self, *, stage: str, game_id: str | None = None) -> bool:
        if self._remaining_time_ms() >= MIN_REMAINING_TIME_MS:
            return True
        fields: dict[str, object] = {"stage": stage}
        if game_id is not None:
            fields["game_id"] = game_id
        self._emit("info", "provisional_rating_budget_skip", **fields)
        return False


def _is_due(game: Game, now: datetime) -> bool:
    return (
        game.status.state is GameState.FINAL
        and game.rating.state is RatingState.PENDING
        and (
            game.rating.retry.next_attempt_at is not None
            and game.rating.retry.next_attempt_at <= now
            or game.rating.retry.next_attempt_at is None
            and game.rating.retry.attempt_count == 0
        )
    )


def _ordering_key(game: Game) -> tuple[datetime, datetime, str]:
    due_at = game.rating.retry.next_attempt_at or (
        game.live_state_updated_at
        or game.schedule_updated_at
        or game.schedule_checked_at
    )
    kickoff_at = game.kickoff_at or datetime.max.replace(tzinfo=UTC)
    return due_at, kickoff_at, str(game.espn_id)


def _provider_error_code(error: ProviderError) -> str:
    if isinstance(error, ProviderTransportError):
        return "espn_transport_error"
    if isinstance(error, ProviderUnavailableError):
        return "espn_unavailable"
    if isinstance(error, ProviderDataError):
        return "espn_data_error"
    return "espn_provider_error"


def _require_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("now must be a timezone-aware UTC datetime")


__all__ = [
    "MAX_ATTEMPTS",
    "MAX_PROVISIONAL_GAMES",
    "MAX_REVALIDATION_GAMES",
    "MIN_REMAINING_TIME_MS",
    "MODEL_VERSION",
    "ProvisionalRatingService",
    "RETRY_DELAYS",
]
