"""Application service for schedule imports and safe live synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from typing import Protocol

from ..game import (
    Game,
    GameId,
    GameRating,
    GameState,
    LiveStatusUpdate,
    RatingRetry,
    RatingState,
    ScheduleUpdate,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
    TeamScheduleUpdate,
    UNSET,
    WriteResult,
    can_transition_game_state,
)
from ..game.repository import GameRepository
from ..providers import (
    ESPNScoreboardProvider,
    ScheduleGame,
    ScheduleTeam,
    ScoreboardBatch,
)
from .models import SyncEvent, SyncMode, SyncResult
from .records import TeamResult, TeamSide, prepare_team_records, results_by_team


NEAR_TERM_WEEK_COUNT = 3
PREGAME_CHECK_WINDOW = timedelta(minutes=75)
POST_KICKOFF_CHECK_WINDOW = timedelta(hours=8)
MINIMUM_LIVE_CHECK_INTERVAL = timedelta(seconds=45)


class _Logger(Protocol):
    def info(self, message: str) -> object: ...

    def error(self, message: str) -> object: ...


@dataclass(slots=True)
class _Counts:
    scoreboard_requests: int = 0
    games_created: int = 0
    games_updated: int = 0
    stale_writes: int = 0
    rejected_transitions: int = 0


class ScheduleSyncService:
    """Coordinate ESPN observations and repository-owned persistence."""

    def __init__(
        self,
        repository: GameRepository,
        scoreboard: ESPNScoreboardProvider,
        *,
        logger: _Logger | None = None,
    ) -> None:
        self._repository = repository
        self._scoreboard = scoreboard
        self._logger = logger or logging.getLogger(__name__)

    def run(self, event: SyncEvent, *, now: datetime) -> SyncResult:
        """Run one validated mode and return its operational summary."""
        _emit(
            self._logger,
            "info",
            "sync_started",
            mode=event.mode.value,
            scheduled_time=event.scheduled_at.isoformat(),
        )
        if event.is_from_future(now):
            raise ValueError(
                "scheduled event time is more than one minute in the future"
            )
        if event.is_old(now):
            _emit(
                self._logger,
                "info",
                "ignored_old_event",
                mode=event.mode.value,
                scheduled_time=event.scheduled_at.isoformat(),
            )
            return SyncResult(mode=event.mode, ignored_old_event=True)

        counts = _Counts()
        handoffs: list[GameId] = []
        if event.mode is SyncMode.LIVE_TICK:
            self._run_live(event, now, counts, handoffs)
        else:
            self._run_schedule(event, counts, handoffs)

        result = SyncResult(
            mode=event.mode,
            scoreboard_requests=counts.scoreboard_requests,
            games_created=counts.games_created,
            games_updated=counts.games_updated,
            stale_writes=counts.stale_writes,
            rejected_transitions=counts.rejected_transitions,
            provisional_rating_game_ids=tuple(dict.fromkeys(handoffs)),
        )
        _emit(
            self._logger,
            "info",
            "sync_completed",
            mode=event.mode.value,
            scoreboard_requests=result.scoreboard_requests,
            games_created=result.games_created,
            games_updated=result.games_updated,
            stale_writes=result.stale_writes,
            rejected_transitions=result.rejected_transitions,
            provisional_rating_requests=len(result.provisional_rating_game_ids),
        )
        return result

    def _run_schedule(
        self,
        event: SyncEvent,
        counts: _Counts,
        handoffs: list[GameId],
    ) -> None:
        discovery = self._fetch(None, counts, purpose="calendar_discovery")
        weeks = _select_schedule_weeks(event, discovery)
        exclusions = _future_week_exclusions(discovery)

        for week in weeks:
            batch = (
                discovery
                if week == discovery.season_week
                else self._fetch(week, counts, purpose="schedule_refresh")
            )
            week_exclusions = exclusions if week != discovery.season_week else {}
            for observation in batch.games:
                self._sync_schedule_game(
                    batch.season_week,
                    batch.observed_at,
                    observation,
                    week_exclusions,
                    counts,
                    handoffs,
                )

    def _run_live(
        self,
        event: SyncEvent,
        now: datetime,
        counts: _Counts,
        handoffs: list[GameId],
    ) -> None:
        assert event.season is not None
        candidates = self._repository.list_season(event.season)
        due = [game for game in candidates if _needs_scoreboard_check(game, now)]
        if not due:
            _emit(
                self._logger,
                "info",
                "live_tick_no_work",
                mode=event.mode.value,
                season=event.season,
            )
            return

        batch = self._fetch(None, counts, purpose="live_tick")
        if batch.season_week.season != event.season:
            raise ValueError("ESPN live scoreboard season does not match event season")
        observations = {game.game_id: game for game in batch.games}
        for current in due:
            observation = observations.get(current.game_id)
            if observation is None:
                _emit(
                    self._logger,
                    "error",
                    "due_game_missing_from_scoreboard",
                    game_id=str(current.game_id),
                    season=event.season,
                )
                continue
            self._sync_live_game(
                batch.season_week,
                batch.observed_at,
                current,
                observation,
                counts,
                handoffs,
            )

    def _fetch(
        self,
        week: SeasonWeek | None,
        counts: _Counts,
        *,
        purpose: str,
    ) -> ScoreboardBatch:
        counts.scoreboard_requests += 1
        _emit(
            self._logger,
            "info",
            "source_call",
            provider="espn",
            operation="scoreboard",
            purpose=purpose,
            season=(week.season if week is not None else None),
            phase=(week.phase.value if week is not None else None),
            week=(week.week if week is not None else None),
        )
        return self._scoreboard.fetch_scoreboard(week)

    def _sync_schedule_game(
        self,
        season_week: SeasonWeek,
        observed_at: datetime,
        observation: ScheduleGame,
        exclusions: dict[str, tuple[TeamResult, ...]],
        counts: _Counts,
        handoffs: list[GameId],
    ) -> None:
        current = self._repository.get(observation.game_id)
        if current is None:
            game = _new_game(season_week, observed_at, observation, exclusions)
            if self._repository.create_if_absent(game):
                counts.games_created += 1
                _emit(
                    self._logger,
                    "info",
                    "game_created",
                    game_id=str(game.game_id),
                )
                _add_rating_handoff(game, handoffs)
                return
            current = self._repository.get(observation.game_id)
            if current is None:
                raise RuntimeError("game create lost a race but no stored game exists")

        update, rejected = _schedule_update(
            season_week,
            observed_at,
            observation,
            current,
            exclusions,
            include_status=True,
        )
        if rejected:
            counts.rejected_transitions += 1
            _emit(
                self._logger,
                "info",
                "rejected_state_regression",
                game_id=str(current.game_id),
                current_state=current.status.state.value,
                source_state=observation.status.state.value,
            )
        result = self._repository.apply_schedule(current, update)
        _record_write(result, current.game_id, counts, self._logger)
        if result is WriteResult.APPLIED:
            _add_observation_handoff(current, observation, handoffs)

    def _sync_live_game(
        self,
        season_week: SeasonWeek,
        observed_at: datetime,
        current: Game,
        observation: ScheduleGame,
        counts: _Counts,
        handoffs: list[GameId],
    ) -> None:
        transition_allowed = can_transition_game_state(
            current.status,
            observation.status.state,
        )
        any_applied = False
        if transition_allowed:
            live_result = self._repository.apply_live_status(
                current,
                LiveStatusUpdate(
                    game_id=current.game_id,
                    observed_at=observed_at,
                    status=observation.status,
                ),
            )
            if live_result is WriteResult.APPLIED:
                any_applied = True
            else:
                counts.stale_writes += 1
                _emit(
                    self._logger,
                    "info",
                    "stale_or_duplicate_write",
                    game_id=str(current.game_id),
                    write_type="live_status",
                )
        else:
            counts.rejected_transitions += 1
            _emit(
                self._logger,
                "info",
                "rejected_state_regression",
                game_id=str(current.game_id),
                current_state=current.status.state.value,
                source_state=observation.status.state.value,
            )

        latest = self._repository.get(current.game_id)
        if latest is None:
            raise RuntimeError("game disappeared during live sync")
        schedule_update, _ = _schedule_update(
            season_week,
            observed_at,
            observation,
            latest,
            {},
            include_status=False,
        )
        schedule_result = self._repository.apply_schedule(latest, schedule_update)
        if schedule_result is WriteResult.APPLIED:
            any_applied = True
        else:
            counts.stale_writes += 1
            _emit(
                self._logger,
                "info",
                "stale_or_duplicate_write",
                game_id=str(current.game_id),
                write_type="schedule",
            )

        if any_applied:
            counts.games_updated += 1
            _emit(
                self._logger,
                "info",
                "game_changed",
                game_id=str(current.game_id),
            )
            _add_observation_handoff(latest, observation, handoffs)


def _select_schedule_weeks(
    event: SyncEvent,
    discovery: ScoreboardBatch,
) -> tuple[SeasonWeek, ...]:
    known = discovery.known_weeks
    if not known:
        raise ValueError("ESPN scoreboard did not include schedule calendar metadata")
    if event.mode is SyncMode.MANUAL_PRESEASON:
        assert event.season is not None
        if discovery.season_week.season != event.season:
            raise ValueError("ESPN season does not match the manual import season")
        return tuple(
            week
            for week in known
            if week.phase in {SeasonPhase.REGULAR_SEASON, SeasonPhase.POSTSEASON}
        )

    try:
        current_index = known.index(discovery.season_week)
    except ValueError as exc:
        raise ValueError(
            "ESPN current week is absent from its schedule calendar"
        ) from exc
    remaining = known[current_index:]
    if event.mode is SyncMode.DAILY_NEAR_TERM:
        return remaining[:NEAR_TERM_WEEK_COUNT]
    if event.mode is SyncMode.WEEKLY_REMAINING:
        return remaining
    raise ValueError(f"unsupported schedule mode: {event.mode.value}")


def _future_week_exclusions(
    current_batch: ScoreboardBatch,
) -> dict[str, tuple[TeamResult, ...]]:
    if current_batch.season_week.phase is not SeasonPhase.REGULAR_SEASON:
        return {}
    if current_batch.games and all(
        game.status.state in {GameState.FINAL, GameState.CANCELLED}
        for game in current_batch.games
    ):
        return {}

    collected: dict[str, list[TeamResult]] = {}
    for game in current_batch.games:
        for team_id, result in results_by_team(
            game.status,
            game.home.team_id,
            game.away.team_id,
        ).items():
            collected.setdefault(team_id, []).append(result)
    return {team_id: tuple(results) for team_id, results in collected.items()}


def _new_game(
    season_week: SeasonWeek,
    observed_at: datetime,
    observation: ScheduleGame,
    exclusions: dict[str, tuple[TeamResult, ...]],
) -> Game:
    home_records = prepare_team_records(
        phase=season_week.phase,
        source_record=observation.home.record,
        source_status=observation.status,
        side=TeamSide.HOME,
        saved_team=None,
        saved_status=None,
        excluded_results=exclusions.get(observation.home.team_id, ()),
    )
    away_records = prepare_team_records(
        phase=season_week.phase,
        source_record=observation.away.record,
        source_status=observation.status,
        side=TeamSide.AWAY,
        saved_team=None,
        saved_status=None,
        excluded_results=exclusions.get(observation.away.team_id, ()),
    )
    return Game(
        game_id=observation.game_id,
        espn_id=str(observation.game_id),
        nflverse_id=None,
        season_week=season_week,
        kickoff_at=observation.kickoff_at,
        home=_new_team(observation.home, home_records.pregame, home_records.postgame),
        away=_new_team(observation.away, away_records.pregame, away_records.postgame),
        status=observation.status,
        rating=GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=observed_at,
        schedule_updated_at=observed_at,
        live_source_checked_at=(
            observed_at if observation.status.has_started else None
        ),
        live_state_updated_at=(observed_at if observation.status.has_started else None),
        broadcaster=observation.broadcaster,
        odds=observation.odds,
    )


def _new_team(
    source: ScheduleTeam,
    pregame: object,
    postgame: object,
) -> TeamGameSnapshot:
    return TeamGameSnapshot(
        team_id=source.team_id,
        display_name=source.display_name,
        abbreviation=source.abbreviation,
        logo_key=source.logo_key,
        pregame_record=pregame,
        postgame_record=postgame,
    )


def _schedule_update(
    season_week: SeasonWeek,
    observed_at: datetime,
    observation: ScheduleGame,
    current: Game,
    exclusions: dict[str, tuple[TeamResult, ...]],
    *,
    include_status: bool,
) -> tuple[ScheduleUpdate, bool]:
    home_saved = (
        current.home if current.home.team_id == observation.home.team_id else None
    )
    away_saved = (
        current.away if current.away.team_id == observation.away.team_id else None
    )
    home_records = prepare_team_records(
        phase=season_week.phase,
        source_record=observation.home.record,
        source_status=observation.status,
        side=TeamSide.HOME,
        saved_team=home_saved,
        saved_status=current.status,
        excluded_results=exclusions.get(observation.home.team_id, ()),
    )
    away_records = prepare_team_records(
        phase=season_week.phase,
        source_record=observation.away.record,
        source_status=observation.status,
        side=TeamSide.AWAY,
        saved_team=away_saved,
        saved_status=current.status,
        excluded_results=exclusions.get(observation.away.team_id, ()),
    )
    rejected = include_status and not can_transition_game_state(
        current.status,
        observation.status.state,
    )
    odds = (
        UNSET
        if current.status.has_started
        or observation.status.has_started
        or observation.odds is None
        else observation.odds
    )
    return (
        ScheduleUpdate(
            game_id=current.game_id,
            observed_at=observed_at,
            season_week=season_week,
            kickoff_at=observation.kickoff_at,
            home=TeamScheduleUpdate(
                team_id=observation.home.team_id,
                display_name=observation.home.display_name,
                abbreviation=observation.home.abbreviation,
                logo_key=observation.home.logo_key,
                pregame_record=home_records.pregame,
                postgame_record=home_records.postgame,
            ),
            away=TeamScheduleUpdate(
                team_id=observation.away.team_id,
                display_name=observation.away.display_name,
                abbreviation=observation.away.abbreviation,
                logo_key=observation.away.logo_key,
                pregame_record=away_records.pregame,
                postgame_record=away_records.postgame,
            ),
            status=(observation.status if include_status and not rejected else None),
            broadcaster=(
                observation.broadcaster
                if observation.broadcaster is not None
                else UNSET
            ),
            odds=odds,
        ),
        rejected,
    )


def _needs_scoreboard_check(game: Game, now: datetime) -> bool:
    if game.status.state in {GameState.FINAL, GameState.CANCELLED}:
        return False
    if game.live_source_checked_at is not None:
        if now - game.live_source_checked_at < MINIMUM_LIVE_CHECK_INTERVAL:
            return False
    if game.status.has_started:
        return True
    if game.kickoff_at is None:
        return False
    return (
        game.kickoff_at - PREGAME_CHECK_WINDOW
        <= now
        <= game.kickoff_at + POST_KICKOFF_CHECK_WINDOW
    )


def _record_write(
    result: WriteResult,
    game_id: GameId,
    counts: _Counts,
    logger: _Logger,
) -> None:
    if result is WriteResult.APPLIED:
        counts.games_updated += 1
        _emit(logger, "info", "game_changed", game_id=str(game_id))
    else:
        counts.stale_writes += 1
        _emit(logger, "info", "stale_or_duplicate_write", game_id=str(game_id))


def _add_observation_handoff(
    current: Game,
    observation: ScheduleGame,
    handoffs: list[GameId],
) -> None:
    if (
        observation.status.state is GameState.FINAL
        and current.rating.state is RatingState.PENDING
    ):
        handoffs.append(current.game_id)


def _add_rating_handoff(game: Game, handoffs: list[GameId]) -> None:
    if (
        game.status.state is GameState.FINAL
        and game.rating.state is RatingState.PENDING
    ):
        handoffs.append(game.game_id)


def _emit(logger: _Logger, level: str, event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    getattr(logger, level)(json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = [
    "MINIMUM_LIVE_CHECK_INTERVAL",
    "NEAR_TERM_WEEK_COUNT",
    "POST_KICKOFF_CHECK_WINDOW",
    "PREGAME_CHECK_WINDOW",
    "ScheduleSyncService",
]
