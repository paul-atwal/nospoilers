"""Application service for schedule imports and safe live synchronization."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
import logging
from typing import Protocol

from ..game import (
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    LiveFinalizationUpdate,
    LiveStatusUpdate,
    RatingRetry,
    RatingState,
    RecordSnapshot,
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
from ..rating.confirmation import is_confirmation_supported
from .models import SyncEvent, SyncMode, SyncResult
from .records import TeamResult, TeamSide, prepare_team_records, results_by_team


NEAR_TERM_WEEK_COUNT = 3
PREGAME_CHECK_WINDOW = timedelta(minutes=75)
POST_KICKOFF_CHECK_WINDOW = timedelta(hours=8)
MINIMUM_LIVE_CHECK_INTERVAL = timedelta(seconds=45)
MAX_STARTED_RECOVERY_WINDOW = timedelta(hours=12)
SCHEDULE_FETCH_WORKERS = 8
MAX_KNOWN_WEEKS = 32


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


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Bounded inventory import summary for one catalogued season."""

    season: int
    requested_weeks: tuple[SeasonWeek, ...]
    verified_weeks: tuple[SeasonWeek, ...]
    empty_weeks: tuple[SeasonWeek, ...]
    scoreboard_requests: int
    games_created: int
    games_updated: int
    stale_writes: int
    rejected_transitions: int
    supported_final_game_ids: tuple[GameId, ...]

    @property
    def source_calls(self) -> int:
        return self.scoreboard_requests

    @property
    def persistence_writes(self) -> int:
        return self.games_created + self.games_updated


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
            self._run_schedule(event, now, counts, handoffs)

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

    def import_weeks(
        self,
        season: int,
        weeks: tuple[SeasonWeek, ...],
        *,
        now: datetime,
        active_season: int,
    ) -> ImportResult:
        """Import exactly the supplied weeks, without live discovery for history.

        The active season gets one discovery response solely to protect future
        record snapshots. Every requested week is still fetched explicitly.
        """
        if isinstance(season, bool) or not isinstance(season, int) or season < 1:
            raise ValueError("season must be a positive integer")
        if (
            isinstance(active_season, bool)
            or not isinstance(active_season, int)
            or active_season < 1
        ):
            raise ValueError("active_season must be a positive integer")
        if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
            raise ValueError("now must be a timezone-aware UTC datetime")
        if not isinstance(weeks, tuple) or not weeks:
            raise ValueError("import weeks must be a non-empty tuple")
        if len(weeks) > MAX_KNOWN_WEEKS:
            raise ValueError("import weeks exceed the bounded week limit")
        if any(not isinstance(week, SeasonWeek) for week in weeks):
            raise ValueError("import weeks must contain SeasonWeek values")
        if any(week.season != season for week in weeks):
            raise ValueError("import weeks must be a non-empty single-season tuple")
        if len(set(weeks)) != len(weeks):
            raise ValueError("import weeks must be unique")
        requested = tuple(weeks)
        counts = _Counts()
        exclusions: dict[str, tuple[TeamResult, ...]] = {}
        future_weeks: set[SeasonWeek] = set()
        if season == active_season:
            discovery = self._fetch(None, counts, purpose="active_import_discovery")
            if discovery.season_week.season != season:
                raise ValueError("active discovery season does not match import season")
            if (
                not discovery.known_weeks
                or len(discovery.known_weeks) > MAX_KNOWN_WEEKS
            ):
                raise ValueError("active discovery calendar is missing or unbounded")
            try:
                current_index = discovery.known_weeks.index(discovery.season_week)
            except ValueError as exc:
                raise ValueError("active discovery current week is not catalogued") from exc
            future_weeks = set(discovery.known_weeks[current_index + 1 :])
            exclusions = _future_week_exclusions(discovery)

        batches = self._fetch_import_batches(requested, counts)
        handoffs: list[GameId] = []
        supported: set[GameId] = set()
        for batch in batches:
            week_exclusions = exclusions if batch.season_week in future_weeks else {}
            for observation in batch.games:
                self._sync_schedule_game(
                    batch.season_week,
                    batch.observed_at,
                    observation,
                    week_exclusions,
                    now,
                    counts,
                    handoffs,
                )
                durable = self._repository.get(observation.game_id)
                if (
                    durable is not None
                    and durable.season_week == batch.season_week
                    and durable.status.state is GameState.FINAL
                    and is_confirmation_supported(batch.season_week)
                ):
                    supported.add(durable.game_id)
        empty = tuple(batch.season_week for batch in batches if not batch.games)
        return ImportResult(
            season=season,
            requested_weeks=requested,
            verified_weeks=tuple(batch.season_week for batch in batches),
            empty_weeks=empty,
            scoreboard_requests=counts.scoreboard_requests,
            games_created=counts.games_created,
            games_updated=counts.games_updated,
            stale_writes=counts.stale_writes,
            rejected_transitions=counts.rejected_transitions,
            supported_final_game_ids=tuple(sorted(supported, key=str)),
        )

    def _fetch_import_batches(
        self, weeks: tuple[SeasonWeek, ...], counts: _Counts
    ) -> tuple[ScoreboardBatch, ...]:
        """Fetch and validate every exact envelope before any persistence."""
        for week in weeks:
            self._log_source_call(week, purpose="inventory_import")
        counts.scoreboard_requests += len(weeks)
        with ThreadPoolExecutor(
            max_workers=min(SCHEDULE_FETCH_WORKERS, len(weeks))
        ) as executor:
            fetched = tuple(executor.map(self._scoreboard.fetch_scoreboard, weeks))
        for requested, batch in zip(weeks, fetched):
            if not isinstance(batch, ScoreboardBatch) or batch.season_week != requested:
                raise ValueError(
                    "ESPN scoreboard envelope does not match requested inventory week"
                )
        return fetched

    def _run_schedule(
        self,
        event: SyncEvent,
        now: datetime,
        counts: _Counts,
        handoffs: list[GameId],
    ) -> None:
        discovery = self._fetch(None, counts, purpose="calendar_discovery")
        weeks = _select_schedule_weeks(event, discovery)
        weeks = self._include_recovery_weeks(discovery, weeks)
        exclusions = _future_week_exclusions(discovery)
        current_index = discovery.known_weeks.index(discovery.season_week)
        future_weeks = set(discovery.known_weeks[current_index + 1 :])

        for batch in self._fetch_schedule_batches(discovery, weeks, counts):
            week = batch.season_week
            week_exclusions = exclusions if week in future_weeks else {}
            for observation in batch.games:
                self._sync_schedule_game(
                    batch.season_week,
                    batch.observed_at,
                    observation,
                    week_exclusions,
                    now,
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
        handoff_candidates = sorted(
            (game for game in candidates if _needs_rating_handoff(game, now)),
            key=_rating_handoff_priority,
        )
        handoffs.extend(game.game_id for game in handoff_candidates)
        scoreboard_due = [
            game for game in candidates if _needs_scoreboard_check(game, now)
        ]
        if not scoreboard_due:
            _emit(
                self._logger,
                "info",
                "live_tick_no_scoreboard_work",
                mode=event.mode.value,
                season=event.season,
                provisional_rating_requests=len(handoffs),
            )
            return

        selected_week = _select_live_week(scoreboard_due)
        due = [game for game in scoreboard_due if game.season_week == selected_week]
        batch = self._fetch(selected_week, counts, purpose="live_tick")
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
                self._checkpoint_missing_game(current, batch.observed_at, counts)
                continue
            self._sync_live_game(
                batch.season_week,
                batch.observed_at,
                current,
                observation,
                now,
                counts,
                handoffs,
            )

    def _include_recovery_weeks(
        self,
        discovery: ScoreboardBatch,
        selected: tuple[SeasonWeek, ...],
    ) -> tuple[SeasonWeek, ...]:
        """Include known past weeks that still contain a nonterminal game."""
        current_index = discovery.known_weeks.index(discovery.season_week)
        past_weeks = set(discovery.known_weeks[:current_index])
        recovery = {
            game.season_week
            for game in self._repository.list_season(discovery.season_week.season)
            if game.season_week in past_weeks
            and game.status.state not in {GameState.FINAL, GameState.CANCELLED}
        }
        wanted = set(selected) | recovery
        return tuple(week for week in discovery.known_weeks if week in wanted)

    def _fetch_schedule_batches(
        self,
        discovery: ScoreboardBatch,
        weeks: tuple[SeasonWeek, ...],
        counts: _Counts,
    ) -> tuple[ScoreboardBatch, ...]:
        """Fetch independent weeks concurrently, then return source order."""
        requested = tuple(week for week in weeks if week != discovery.season_week)
        if not requested:
            return (discovery,) if discovery.season_week in weeks else ()

        for week in requested:
            self._log_source_call(week, purpose="schedule_refresh")
        counts.scoreboard_requests += len(requested)
        with ThreadPoolExecutor(
            max_workers=min(SCHEDULE_FETCH_WORKERS, len(requested))
        ) as executor:
            fetched = tuple(executor.map(self._scoreboard.fetch_scoreboard, requested))
        by_week = {batch.season_week: batch for batch in fetched}
        if len(by_week) != len(fetched):
            raise ValueError("ESPN returned duplicate requested schedule weeks")
        if discovery.season_week in weeks:
            by_week[discovery.season_week] = discovery
        return tuple(by_week[week] for week in weeks)

    def _checkpoint_missing_game(
        self,
        current: Game,
        observed_at: datetime,
        counts: _Counts,
    ) -> None:
        """Record a successful week check that omitted one expected game."""
        result = self._repository.apply_live_status(
            current,
            LiveStatusUpdate(
                game_id=current.game_id,
                observed_at=observed_at,
                status=current.status,
            ),
        )
        if result is WriteResult.APPLIED:
            counts.games_updated += 1
        else:
            counts.stale_writes += 1
            _emit(
                self._logger,
                "info",
                "stale_or_duplicate_write",
                game_id=str(current.game_id),
                write_type="missing_game_checkpoint",
            )

    def _fetch(
        self,
        week: SeasonWeek | None,
        counts: _Counts,
        *,
        purpose: str,
    ) -> ScoreboardBatch:
        counts.scoreboard_requests += 1
        self._log_source_call(week, purpose=purpose)
        return self._scoreboard.fetch_scoreboard(week)

    def _log_source_call(
        self,
        week: SeasonWeek | None,
        *,
        purpose: str,
    ) -> None:
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

    def _sync_schedule_game(
        self,
        season_week: SeasonWeek,
        observed_at: datetime,
        observation: ScheduleGame,
        exclusions: dict[str, tuple[TeamResult, ...]],
        now: datetime,
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
                _add_rating_handoff(game, handoffs, now)
                return
            current = self._repository.get(observation.game_id)
            if current is None:
                raise RuntimeError("game create lost a race but no stored game exists")

        update, rejected, bridged = _schedule_update(
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
        if bridged:
            _emit(
                self._logger,
                "info",
                "reschedule_transition_bridged",
                game_id=str(current.game_id),
                current_state=current.status.state.value,
                source_state=observation.status.state.value,
            )
        result = self._repository.apply_schedule(current, update)
        _record_write(result, current.game_id, counts, self._logger)
        if (
            result is WriteResult.APPLIED
            and observation.status.state is GameState.FINAL
        ):
            persisted = self._repository.get(current.game_id)
            if persisted is not None:
                _add_rating_handoff(persisted, handoffs, now)

    def _sync_live_game(
        self,
        season_week: SeasonWeek,
        observed_at: datetime,
        current: Game,
        observation: ScheduleGame,
        now: datetime,
        counts: _Counts,
        handoffs: list[GameId],
    ) -> None:
        live_status, rejected, bridged = _schedule_status(
            current,
            observation,
            include_status=True,
        )
        any_applied = False
        finalization = None
        finalization_applied = False
        schedule_update = None
        if live_status is not None:
            if live_status.state is GameState.FINAL:
                # Prepare records from the pre-final current snapshot.  The
                # final status is not durable until these values can be
                # committed with it.
                schedule_update, _, _ = _schedule_update(
                    season_week,
                    observed_at,
                    observation,
                    current,
                    {},
                    include_status=False,
                )
                # Final preparation always resolves omitted source records to
                # either a saved/derived snapshot or None.  UNSET is reserved
                # for non-final schedule updates.
                assert schedule_update.home.pregame_record is not UNSET
                assert schedule_update.home.postgame_record is not UNSET
                assert schedule_update.away.pregame_record is not UNSET
                assert schedule_update.away.postgame_record is not UNSET
                finalization = LiveFinalizationUpdate(
                    game_id=current.game_id,
                    observed_at=observed_at,
                    status=live_status,
                    home_team_id=schedule_update.home.team_id,
                    away_team_id=schedule_update.away.team_id,
                    home_pregame_record=schedule_update.home.pregame_record,
                    home_postgame_record=schedule_update.home.postgame_record,
                    away_pregame_record=schedule_update.away.pregame_record,
                    away_postgame_record=schedule_update.away.postgame_record,
                )
                live_result = self._repository.apply_live_finalization(
                    current,
                    finalization,
                )
                finalization_applied = live_result is WriteResult.APPLIED
            else:
                live_result = self._repository.apply_live_status(
                    current,
                    LiveStatusUpdate(
                        game_id=current.game_id,
                        observed_at=observed_at,
                        status=live_status,
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
                    write_type=(
                        "live_finalization"
                        if finalization is not None
                        else "live_status"
                    ),
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
        if bridged:
            _emit(
                self._logger,
                "info",
                "reschedule_transition_bridged",
                game_id=str(current.game_id),
                current_state=current.status.state.value,
                source_state=observation.status.state.value,
            )

        latest = self._repository.get(current.game_id)
        if latest is None:
            raise RuntimeError("game disappeared during live sync")
        prepared_team_ids_match = (
            schedule_update is not None
            and latest.home.team_id == schedule_update.home.team_id
            and latest.away.team_id == schedule_update.away.team_id
        )
        if (
            finalization_applied
            and latest.status.state is GameState.FINAL
            and prepared_team_ids_match
        ):
            # Record fields are already part of the atomic finalization.
            # Keep this follow-up limited to schedule metadata and team
            # display data.
            assert schedule_update is not None
            schedule_update = replace(
                schedule_update,
                home=replace(
                    schedule_update.home,
                    pregame_record=UNSET,
                    postgame_record=UNSET,
                ),
                away=replace(
                    schedule_update.away,
                    pregame_record=UNSET,
                    postgame_record=UNSET,
                ),
            )
        else:
            # Every non-final follow-up must be prepared from the strong
            # reread.  A pre-live update can describe an old record snapshot
            # and clear fields committed by a concurrent finalization.
            schedule_update, _, _ = _schedule_update(
                season_week,
                observed_at,
                observation,
                latest,
                {},
                include_status=False,
            )
            if finalization is not None:
                if latest.status.state is GameState.FINAL:
                    # Another writer won finalization.  Do not let this
                    # observation overwrite its record snapshots.
                    schedule_update = replace(
                        schedule_update,
                        home=replace(
                            schedule_update.home,
                            pregame_record=UNSET,
                            postgame_record=UNSET,
                        ),
                        away=replace(
                            schedule_update.away,
                            pregame_record=UNSET,
                            postgame_record=UNSET,
                        ),
                    )
                else:
                    # A finalization attempt did not win.  Keep final-only
                    # records out of a non-final follow-up.
                    schedule_update = replace(
                        schedule_update,
                        home=replace(schedule_update.home, postgame_record=UNSET),
                        away=replace(schedule_update.away, postgame_record=UNSET),
                    )
        if not prepared_team_ids_match:
            schedule_update = _materialize_stable_team_records(
                schedule_update,
                latest,
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
            if observation.status.state is GameState.FINAL:
                persisted = self._repository.get(current.game_id)
                if persisted is not None:
                    _add_rating_handoff(persisted, handoffs, now)


def _select_schedule_weeks(
    event: SyncEvent,
    discovery: ScoreboardBatch,
) -> tuple[SeasonWeek, ...]:
    known = discovery.known_weeks
    if not known:
        raise ValueError("ESPN scoreboard did not include schedule calendar metadata")
    if len(known) > MAX_KNOWN_WEEKS:
        raise ValueError("ESPN schedule calendar exceeds the bounded week limit")
    try:
        current_index = known.index(discovery.season_week)
    except ValueError as exc:
        raise ValueError(
            "ESPN current week is absent from its schedule calendar"
        ) from exc
    if event.mode is SyncMode.MANUAL_PRESEASON:
        assert event.season is not None
        if discovery.season_week.season != event.season:
            raise ValueError("ESPN season does not match the manual import season")
        return tuple(
            week
            for week in known
            if week.phase in {SeasonPhase.REGULAR_SEASON, SeasonPhase.POSTSEASON}
        )

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
        observed_at=observed_at,
        side=TeamSide.HOME,
        saved_team=None,
        saved_status=None,
        excluded_results=exclusions.get(observation.home.team_id, ()),
    )
    away_records = prepare_team_records(
        phase=season_week.phase,
        source_record=observation.away.record,
        source_status=observation.status,
        observed_at=observed_at,
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
        odds=(
            None
            if observation.status.has_started
            or (
                observation.kickoff_at is not None
                and observed_at >= observation.kickoff_at
            )
            else observation.odds
        ),
    )


def _new_team(
    source: ScheduleTeam,
    pregame: RecordSnapshot | None,
    postgame: RecordSnapshot | None,
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
) -> tuple[ScheduleUpdate, bool, bool]:
    status, rejected, bridged = _schedule_status(current, observation, include_status)
    freeze_pregame = _pregame_values_frozen(current, observation, observed_at)
    record_status = current.status if rejected else observation.status
    home_saved = _saved_team_by_id(current, observation.home.team_id)
    away_saved = _saved_team_by_id(current, observation.away.team_id)
    home_records = prepare_team_records(
        phase=season_week.phase,
        source_record=None if rejected else observation.home.record,
        source_status=record_status,
        observed_at=observed_at,
        side=TeamSide.HOME,
        saved_team=home_saved,
        saved_status=current.status,
        freeze_pregame=freeze_pregame,
        excluded_results=exclusions.get(observation.home.team_id, ()),
    )
    away_records = prepare_team_records(
        phase=season_week.phase,
        source_record=None if rejected else observation.away.record,
        source_status=record_status,
        observed_at=observed_at,
        side=TeamSide.AWAY,
        saved_team=away_saved,
        saved_status=current.status,
        freeze_pregame=freeze_pregame,
        excluded_results=exclusions.get(observation.away.team_id, ()),
    )
    odds = UNSET if freeze_pregame or observation.odds is None else observation.odds
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
            status=status,
            broadcaster=(
                observation.broadcaster
                if observation.broadcaster is not None
                else UNSET
            ),
            odds=odds,
        ),
        rejected,
        bridged,
    )


def _schedule_status(
    current: Game,
    observation: ScheduleGame,
    include_status: bool,
) -> tuple[GameStatus | None, bool, bool]:
    if not include_status:
        return None, False, False
    if can_transition_game_state(current.status, observation.status):
        return observation.status, False, False
    if (
        current.status.state is GameState.DELAYED
        and not current.status.has_started
        and observation.status.state is GameState.SCHEDULED
        and observation.kickoff_at is not None
        and observation.kickoff_at != current.kickoff_at
    ):
        return GameStatus(GameState.POSTPONED), False, True
    return None, True, False


def _pregame_values_frozen(
    current: Game,
    observation: ScheduleGame,
    observed_at: datetime,
) -> bool:
    if current.status.state in {GameState.FINAL, GameState.CANCELLED}:
        return True
    if current.status.has_started or observation.status.has_started:
        return True
    return observation.kickoff_at is not None and observed_at >= observation.kickoff_at


def _saved_team_by_id(current: Game, team_id: str) -> TeamGameSnapshot | None:
    for team in (current.home, current.away):
        if team.team_id == team_id:
            return team
    return None


def _materialize_stable_team_records(
    update: ScheduleUpdate,
    latest: Game,
) -> ScheduleUpdate:
    """Fill masked records from the latest team snapshot by stable ID."""
    return replace(
        update,
        home=_materialize_stable_records(update.home, latest),
        away=_materialize_stable_records(update.away, latest),
    )


def _materialize_stable_records(
    update: TeamScheduleUpdate,
    latest: Game,
) -> TeamScheduleUpdate:
    saved_team = _saved_team_by_id(latest, update.team_id)
    if saved_team is None:
        return update
    return replace(
        update,
        pregame_record=(
            saved_team.pregame_record
            if update.pregame_record is UNSET
            else update.pregame_record
        ),
        postgame_record=(
            saved_team.postgame_record
            if update.postgame_record is UNSET
            else update.postgame_record
        ),
    )


def _needs_scoreboard_check(game: Game, now: datetime) -> bool:
    if game.status.state in {GameState.FINAL, GameState.CANCELLED}:
        return False
    if game.live_source_checked_at is not None:
        if now - game.live_source_checked_at < MINIMUM_LIVE_CHECK_INTERVAL:
            return False
    if game.status.has_started:
        return (
            game.kickoff_at is not None
            and now <= game.kickoff_at + MAX_STARTED_RECOVERY_WINDOW
        )
    if game.kickoff_at is None:
        return False
    return (
        game.kickoff_at - PREGAME_CHECK_WINDOW
        <= now
        <= game.kickoff_at + POST_KICKOFF_CHECK_WINDOW
    )


def _needs_rating_handoff(game: Game, now: datetime) -> bool:
    if game.status.state is not GameState.FINAL:
        return False
    if game.rating.state is not RatingState.PENDING:
        return False
    retry = game.rating.retry
    if retry.next_attempt_at is not None:
        return retry.next_attempt_at <= now
    return retry.attempt_count == 0


def _rating_handoff_priority(game: Game) -> tuple[datetime, datetime, str]:
    """Order provisional work by due time, kickoff, and stable game ID."""
    due_at = game.rating.retry.next_attempt_at or (
        game.live_state_updated_at
        or game.schedule_updated_at
        or game.schedule_checked_at
    )
    kickoff_at = game.kickoff_at or datetime.max.replace(
        tzinfo=game.schedule_checked_at.tzinfo
    )
    return due_at, kickoff_at, str(game.game_id)


def _select_live_week(due: list[Game]) -> SeasonWeek:
    def priority(game: Game) -> tuple[bool, datetime, datetime, str]:
        oldest = game.live_source_checked_at or datetime.min.replace(
            tzinfo=game.schedule_checked_at.tzinfo
        )
        kickoff = game.kickoff_at or datetime.max.replace(
            tzinfo=game.schedule_checked_at.tzinfo
        )
        return (not game.status.has_started, oldest, kickoff, str(game.game_id))

    return min(due, key=priority).season_week


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


def _add_rating_handoff(
    game: Game,
    handoffs: list[GameId],
    now: datetime,
) -> None:
    if _needs_rating_handoff(game, now):
        handoffs.append(game.game_id)


def _emit(logger: _Logger, level: str, event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    getattr(logger, level)(json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = [
    "MINIMUM_LIVE_CHECK_INTERVAL",
    "MAX_KNOWN_WEEKS",
    "MAX_STARTED_RECOVERY_WINDOW",
    "NEAR_TERM_WEEK_COUNT",
    "POST_KICKOFF_CHECK_WINDOW",
    "PREGAME_CHECK_WINDOW",
    "SCHEDULE_FETCH_WORKERS",
    "ScheduleSyncService",
    "ImportResult",
]
