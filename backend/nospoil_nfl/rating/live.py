"""Bounded semi-live work, scheduled after completed-game provisional ratings."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import logging
from typing import Protocol

from ..game.live_rating import LiveRating
from ..game.models import Game, GameId, GameState
from ..game.repository import GameRepository
from ..game.updates import WriteResult
from ..providers.espn_live import LiveGameSummary
from ..providers.errors import ProviderError
from .live_calculator import MODEL_VERSION, calculate_live_rating, hash_live_input

MAX_LIVE_GAMES = 8
FETCH_WORKERS = 4
MIN_REMAINING_TIME_MS = 18_000
MIN_CHECK_INTERVAL = timedelta(seconds=45)
MAX_SOURCE_AGE = timedelta(minutes=10)


class LiveSummaryProvider(Protocol):
    def fetch_live_summary(self, game_id: GameId) -> LiveGameSummary: ...


class LiveRatingService:
    def __init__(
        self,
        repository: GameRepository,
        provider: LiveSummaryProvider,
        *,
        logger: logging.Logger | None = None,
        remaining_time_ms: Callable[[], int] = lambda: 2**63 - 1,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._logger = logger or logging.getLogger(__name__)
        self._remaining_time_ms = remaining_time_ms

    def rate_due(self, game_ids: Iterable[GameId], *, now: datetime) -> None:
        if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
            raise ValueError("now must be UTC")
        # The sync service supplies oldest-attempt-first IDs. Revalidate at most
        # eight with strong reads, so GSI lag or a final race cannot revive one.
        ids = list(dict.fromkeys(game_ids))[:MAX_LIVE_GAMES]
        for offset in range(0, len(ids), FETCH_WORKERS):
            if self._remaining_time_ms() < MIN_REMAINING_TIME_MS:
                self._emit("live_rating_budget_skip")
                return
            due = []
            for game_id in ids[offset : offset + FETCH_WORKERS]:
                if self._remaining_time_ms() < MIN_REMAINING_TIME_MS:
                    break
                game = self._repository.get(game_id)
                if (
                    game is not None
                    and game.status.state is GameState.IN_PROGRESS
                    and game.kickoff_at is not None
                    and now <= game.kickoff_at + timedelta(hours=12)
                    and (
                        game.live_rating_checked_at is None
                        or now - game.live_rating_checked_at >= MIN_CHECK_INTERVAL
                    )
                ):
                    due.append(game)
            if not due:
                continue
            if self._remaining_time_ms() < MIN_REMAINING_TIME_MS:
                self._emit("live_rating_budget_skip")
                return
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
                # Only HTTP work is concurrent. Writes and operational logs retain
                # deterministic order and never share mutable game objects.
                results = list(executor.map(self._fetch, due))
            for game, summary in zip(due, results):
                if self._remaining_time_ms() < MIN_REMAINING_TIME_MS:
                    self._emit("live_rating_budget_skip")
                    return
                rating = (
                    self._rating(game, summary, now) if summary is not None else None
                )
                result = self._repository.apply_live_rating(
                    game, rating, checked_at=now
                )
                self._emit(
                    "live_rating_stale_write"
                    if result is WriteResult.STALE
                    else "live_rating_applied"
                    if rating is not None
                    else "live_rating_no_data",
                    game_id=str(game.game_id),
                )

    def _fetch(self, game: Game) -> LiveGameSummary | None:
        self._emit("live_rating_source_call", game_id=str(game.game_id))
        try:
            return self._provider.fetch_live_summary(GameId(game.espn_id))
        except ProviderError as error:
            self._emit(
                "live_rating_source_failed",
                game_id=str(game.game_id),
                error_type=type(error).__name__,
            )
            return None

    def _rating(
        self, game: Game, summary: LiveGameSummary, now: datetime
    ) -> LiveRating | None:
        if (
            not isinstance(summary, LiveGameSummary)
            or summary.game_id != game.espn_id
            or summary.home_team_id != game.home.team_id
            or summary.away_team_id != game.away.team_id
        ):
            self._emit("live_rating_source_mismatch", game_id=str(game.game_id))
            return None
        last = summary.observations[-1]
        stamp = last.wallclock_at
        if (
            stamp is None
            or stamp > now + timedelta(minutes=1)
            or now - stamp >= MAX_SOURCE_AGE
        ):
            return None
        if (
            game.live_rating is not None
            and last.sequence < game.live_rating.last_play_sequence
        ):
            return None
        score = calculate_live_rating(summary.observations)
        if score is None:
            return None
        digest = hash_live_input(summary.observations)
        if (
            game.live_rating is not None
            and digest == game.live_rating.input_hash
            and game.live_rating.model_version == MODEL_VERSION
        ):
            return game.live_rating  # A repeated fetch must never extend freshness.
        return LiveRating(
            score=score,
            model_version=MODEL_VERSION,
            input_hash=digest,
            calculated_at=now,
            source_updated_at=stamp,
            expires_at=min(now + MAX_SOURCE_AGE, stamp + MAX_SOURCE_AGE),
            period=last.period,
            clock=last.clock,
            last_play_sequence=last.sequence,
            observation_count=len(summary.observations),
        )

    def _emit(self, event: str, **fields: object) -> None:
        self._logger.info(
            json.dumps(
                {"event": event, **fields}, sort_keys=True, separators=(",", ":")
            )
        )
