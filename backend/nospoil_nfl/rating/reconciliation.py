"""Scheduled nflverse reconciliation for durable game ratings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
import logging
from typing import Callable, Literal

from ..game.models import (
    Game,
    GameId,
    GameState,
    GameRating,
    RatingRetry,
    RatingSource,
    RatingState,
)
from ..game.repository import GameRepository, NflverseIdConflictError
from ..game.rules import derive_final_outcome, rating_input_for_outcome
from ..game.updates import WriteResult
from ..nflverse import NflverseGameMappingError, NflverseSeason, load_nflverse_season
from ..providers.contracts import NFLVersePlayProvider, NFLVerseScheduleProvider
from ..providers.errors import ProviderError
from .calculator import calculate_rating
from .input import RatingInput, hash_rating_input


CONFIRMATION_DELAY = timedelta(hours=6)
RETRY_DELAYS = (timedelta(hours=6), timedelta(hours=12), timedelta(hours=24))
OVERDUE_AFTER = timedelta(hours=18)
MODEL_VERSION = "rating-v1"
ReconciliationMode = Literal["due", "correction"]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Bounded counters and attention signals from one reconciliation run."""

    downloads: int = 0
    selected: int = 0
    mappings: int = 0
    confirmed_updates: int = 0
    unchanged: int = 0
    retries: int = 0
    stale_writes: int = 0
    failures: int = 0
    conflicts: int = 0
    overdue: int = 0
    source_failure: bool = False
    manual_correction_failure: bool = False


@dataclass(slots=True)
class _RunState:
    downloads: int = 0
    selected: int = 0
    mappings: int = 0
    confirmed_updates: int = 0
    unchanged: int = 0
    retries: int = 0
    stale_writes: int = 0
    failures: int = 0
    conflicts: int = 0
    overdue: int = 0
    source_failure: bool = False
    manual_correction_failure: bool = False

    def result(self) -> ReconciliationResult:
        return ReconciliationResult(
            downloads=self.downloads,
            selected=self.selected,
            mappings=self.mappings,
            confirmed_updates=self.confirmed_updates,
            unchanged=self.unchanged,
            retries=self.retries,
            stale_writes=self.stale_writes,
            failures=self.failures,
            conflicts=self.conflicts,
            overdue=self.overdue,
            source_failure=self.source_failure,
            manual_correction_failure=self.manual_correction_failure,
        )


class NflverseReconciliationService:
    """Reconcile selected durable games against one loaded nflverse season."""

    def __init__(
        self,
        repository: GameRepository,
        schedule_provider: NFLVerseScheduleProvider,
        play_provider: NFLVersePlayProvider,
        *,
        calculator: Callable[[RatingInput], float] = calculate_rating,
        logger: logging.Logger | None = None,
        model_version: str = MODEL_VERSION,
        season_loader: Callable[..., NflverseSeason] = load_nflverse_season,
    ) -> None:
        if not isinstance(model_version, str) or not model_version.strip():
            raise ValueError("model_version must be non-empty text")
        self._repository = repository
        self._schedule_provider = schedule_provider
        self._play_provider = play_provider
        self._calculator = calculator
        self._logger = logger or logging.getLogger(__name__)
        self._model_version = model_version
        self._season_loader = season_loader

    def run(
        self,
        season: int,
        *,
        now: datetime,
        mode: ReconciliationMode = "due",
        game_id: GameId | None = None,
    ) -> ReconciliationResult:
        """Run due reconciliation or an explicit correction for one season."""
        _require_utc(now)
        if isinstance(season, bool) or not isinstance(season, int) or season < 1:
            raise ValueError("season must be a positive integer")
        if mode not in {"due", "correction"}:
            raise ValueError("mode must be due or correction")
        if game_id is not None and (
            not isinstance(game_id, str) or not game_id.strip()
        ):
            raise ValueError("game_id must be non-empty text")

        games = self._repository.list_season(season)
        state = _RunState()
        if mode == "due":
            state.overdue = sum(
                _is_overdue(game, now)
                for game in games
                if game.status.state is GameState.FINAL
                and game.rating.state is not RatingState.CONFIRMED
            )
        selected = self._select_games(games, now, mode, game_id, state)
        state.selected = len(selected)
        if not selected:
            if mode == "correction" and game_id is not None and not any(
                game.game_id == game_id for game in games
            ):
                state.failures = 1
                state.manual_correction_failure = True
                self._emit(
                    "error",
                    "nflverse_correction_failed",
                    error_code="game_not_found",
                )
            return state.result()

        state.downloads = 1
        self._emit(
            "info",
            "nflverse_download_started",
            mode=mode,
            season=season,
            selected=len(selected),
        )
        try:
            source = self._season_loader(
                season,
                schedule_provider=self._schedule_provider,
                play_provider=self._play_provider,
            )
        except ProviderError as error:
            state.source_failure = True
            state.failures += 1
            self._emit(
                "error",
                "nflverse_download_failed",
                error_code=_provider_error_code(error),
            )
            if mode == "correction":
                state.manual_correction_failure = True
            for candidate in selected:
                current = self._repository.get(candidate.game_id)
                if current is None or not self._still_relevant(
                    current, now, mode, game_id
                ):
                    self._emit(
                        "info",
                        "nflverse_reconciliation_skipped",
                        game_id=str(candidate.game_id),
                        reason="changed_after_selection",
                    )
                    continue
                if current.rating.state is not RatingState.CONFIRMED:
                    self._schedule_retry(
                        current, now, _provider_error_code(error), state, mode
                    )
            return state.result()

        self._emit("info", "nflverse_download_succeeded", season=season)
        for candidate in selected:
            current = self._repository.get(candidate.game_id)
            if current is None or not self._still_relevant(current, now, mode, game_id):
                self._emit(
                    "info",
                    "nflverse_reconciliation_skipped",
                    game_id=str(candidate.game_id),
                    reason="changed_after_selection",
                )
                continue
            self._reconcile_one(current, source, now, mode, state)
        return state.result()

    def run_due(self, season: int, *, now: datetime) -> ReconciliationResult:
        """Run normal due reconciliation for one season."""
        return self.run(season, now=now)

    def run_correction(
        self,
        season: int,
        *,
        now: datetime,
        game_id: GameId | None = None,
    ) -> ReconciliationResult:
        """Recalculate all confirmed games or one named final game."""
        return self.run(season, now=now, mode="correction", game_id=game_id)

    def _select_games(
        self,
        games: list[Game],
        now: datetime,
        mode: ReconciliationMode,
        game_id: GameId | None,
        state: _RunState,
    ) -> list[Game]:
        if mode == "correction":
            if game_id is not None:
                selected = [game for game in games if game.game_id == game_id]
                if selected and selected[0].status.state is not GameState.FINAL:
                    state.failures = 1
                    state.manual_correction_failure = True
                    self._emit(
                        "error",
                        "nflverse_correction_failed",
                        error_code="game_not_final",
                    )
                    return []
                return selected
            return [
                game
                for game in games
                if game.status.state is GameState.FINAL
                and game.rating.state is RatingState.CONFIRMED
            ]

        selected: list[Game] = []
        for game in games:
            if game.status.state is not GameState.FINAL:
                continue
            if game.rating.state is RatingState.CONFIRMED:
                continue
            due_at = _confirmation_due_at(game)
            if due_at <= now:
                selected.append(game)
        selected.sort(key=lambda game: (_confirmation_due_at(game), str(game.game_id)))
        return selected

    @staticmethod
    def _still_relevant(
        current: Game,
        now: datetime,
        mode: ReconciliationMode,
        game_id: GameId | None,
    ) -> bool:
        if current.status.state is not GameState.FINAL:
            return False
        if mode == "correction":
            return game_id is not None or current.rating.state is RatingState.CONFIRMED
        return current.rating.state is not RatingState.CONFIRMED and _confirmation_due_at(current) <= now

    def _reconcile_one(
        self,
        current: Game,
        source: NflverseSeason,
        now: datetime,
        mode: ReconciliationMode,
        state: _RunState,
    ) -> None:
        try:
            mapped = source.find_game(GameId(current.espn_id))
        except NflverseGameMappingError:
            self._record_validation_failure(current, now, "nflverse_mapping_missing", mode, state)
            return

        try:
            if mapped.schedule_game.season_week != current.season_week:
                raise _ValidationFailure("nflverse_week_mismatch")
            mapping_result = self._repository.apply_nflverse_mapping(
                current,
                str(mapped.schedule_game.nflverse_game_id),
            )
            verified_current = replace(
                current,
                nflverse_id=str(mapped.schedule_game.nflverse_game_id),
            )
            state.mappings += 1
            self._emit(
                "info",
                "nflverse_mapping_applied" if mapping_result is WriteResult.APPLIED else "nflverse_mapping_unchanged",
                game_id=str(current.game_id),
            )
            final_score = current.status.score
            if (
                mapped.schedule_game.final_score is None
                or final_score is None
                or mapped.schedule_game.final_score != final_score
            ):
                raise _ValidationFailure("nflverse_schedule_score_mismatch")
            plays = sorted(mapped.plays, key=lambda play: play.play_number)
            wp_history = tuple(
                play.home_win_probability
                for play in plays
                if play.home_win_probability is not None
            )
            if len(wp_history) < 2:
                raise _ValidationFailure("nflverse_plays_not_ready")
            play_score = next(
                (play.score for play in reversed(plays) if play.score is not None),
                None,
            )
            if play_score is None:
                raise _ValidationFailure("nflverse_plays_not_ready")
            if play_score != final_score:
                raise _ValidationFailure("nflverse_play_score_mismatch")
        except NflverseIdConflictError:
            state.conflicts += 1
            state.failures += 1
            if mode == "correction":
                state.manual_correction_failure = True
            self._emit(
                "error",
                "nflverse_mapping_conflict",
                game_id=str(current.game_id),
            )
            return
        except _ValidationFailure as error:
            self._record_validation_failure(current, now, error.code, mode, state)
            return

        rating_input = rating_input_for_outcome(
            wp_history,
            derive_final_outcome(current.status),
        )
        input_hash = hash_rating_input(rating_input)
        score = self._calculator(rating_input)
        if (
            mode == "correction"
            and current.rating.state is RatingState.CONFIRMED
            and current.rating.input_hash == input_hash
            and current.rating.model_version == self._model_version
        ):
            state.unchanged += 1
            self._emit("info", "nflverse_rating_unchanged", game_id=str(current.game_id))
            return

        rating = GameRating(
            state=RatingState.CONFIRMED,
            retry=RatingRetry(),
            score=score,
            source=RatingSource.NFLVERSE,
            model_version=self._model_version,
            input_hash=input_hash,
            calculated_at=now,
            confirmed_at=now,
        )
        result = self._repository.apply_confirmed_rating(verified_current, rating)
        if result is WriteResult.STALE:
            state.stale_writes += 1
            self._emit("info", "nflverse_rating_stale_write", game_id=str(current.game_id))
            return
        state.confirmed_updates += 1
        self._emit("info", "nflverse_rating_confirmed", game_id=str(current.game_id))

    def _record_validation_failure(
        self,
        current: Game,
        now: datetime,
        error_code: str,
        mode: ReconciliationMode,
        state: _RunState,
    ) -> None:
        state.failures += 1
        if mode == "correction":
            state.manual_correction_failure = True
        self._emit(
            "warning",
            "nflverse_reconciliation_failure",
            game_id=str(current.game_id),
            error_code=error_code,
        )
        if current.rating.state is not RatingState.CONFIRMED:
            self._schedule_retry(current, now, error_code, state, mode)

    def _schedule_retry(
        self,
        current: Game,
        now: datetime,
        error_code: str,
        state: _RunState,
        mode: ReconciliationMode,
    ) -> None:
        attempt = current.confirmation_retry.attempt_count + 1
        delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
        retry = RatingRetry(
            attempt_count=attempt,
            next_attempt_at=now + delay,
            last_error=error_code,
        )
        result = self._repository.apply_confirmation_retry(current, retry)
        if result is WriteResult.STALE:
            state.stale_writes += 1
            self._emit("info", "nflverse_retry_stale_write", game_id=str(current.game_id))
            return
        state.retries += 1
        self._emit(
            "info",
            "nflverse_retry_scheduled",
            game_id=str(current.game_id),
            attempt=attempt,
            error_code=error_code,
            mode=mode,
        )

    def _emit(self, level: str, event: str, **fields: object) -> None:
        payload = {"event": event, **fields}
        getattr(self._logger, level)(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


class _ValidationFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _confirmation_due_at(game: Game) -> datetime:
    retry_due = game.confirmation_retry.next_attempt_at
    if retry_due is not None:
        return retry_due
    return _initial_confirmation_due_at(game)


def _initial_confirmation_due_at(game: Game) -> datetime:
    if game.rating.state is RatingState.PROVISIONAL and game.rating.calculated_at is not None:
        return game.rating.calculated_at + CONFIRMATION_DELAY
    final_at = game.live_state_updated_at or game.schedule_updated_at
    return final_at + CONFIRMATION_DELAY


def _is_overdue(game: Game, now: datetime) -> bool:
    return now - _initial_confirmation_due_at(game) > OVERDUE_AFTER


def _provider_error_code(error: ProviderError) -> str:
    operation = error.operation
    if operation:
        return f"nflverse_{operation}_unavailable"
    return "nflverse_source_unavailable"


def _require_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("now must be a timezone-aware UTC datetime")


__all__ = [
    "CONFIRMATION_DELAY",
    "MODEL_VERSION",
    "OVERDUE_AFTER",
    "RETRY_DELAYS",
    "NflverseReconciliationService",
    "ReconciliationMode",
    "ReconciliationResult",
]
