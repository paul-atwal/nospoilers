"""Publication-aware automatic confirmation and bounded recent-week corrections.

Preflight uses only the small sync dependency set and performs no source calls.
The existing manual correction/verify CLI remains available in reconcile.py.
"""

from argparse import ArgumentParser
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import os
import random

from ..game.models import Game, GameState, RatingState
from ..nflverse import load_nflverse_season
from ..providers import NflversePlayClient, NflverseScheduleClient
from ..providers.errors import ProviderError
from ..providers.nflverse_publication import NflversePublicationClient, Publication
from .confirmation import confirmation_work_remains, is_confirmation_supported
from .publication import correction_cycle, next_probe_at
from .reconciliation import (
    MODEL_VERSION,
    NflverseReconciliationService,
    ReconciliationResult,
    _is_overdue,
)
from .reconcile import (
    _bounded_positive_float_environment,
    _combine_results,
    _configure_application_logging,
    _dynamodb_config,
    _exit_code,
    _log_failure,
    _publish,
    _required_environment,
    _result_attention,
    _result_payload,
    _seasons_to_run,
)
from .source_state import DynamoSourceStateRepository, SourceState


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _fingerprint(game: Game) -> str:
    """Only changes to actual confirmation inputs invalidate an evaluation."""
    value = (
        MODEL_VERSION,
        game.espn_id,
        game.season_week.season,
        game.season_week.phase.value,
        game.season_week.week,
        game.status.state.value,
        (game.status.score.home, game.status.score.away) if game.status.score else None,
    )
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def _candidates(
    games: list[Game], state: SourceState, now: datetime
) -> tuple[list[Game], list[Game]]:
    pending = [game for game in games if confirmation_work_remains(game)]
    cycle = correction_cycle(now)
    corrected = state.corrected if state.corrected_cycle == cycle.isoformat() else {}
    corrections = [
        game
        for game in games
        if game.rating.state is RatingState.CONFIRMED
        and game.status.state is GameState.FINAL
        and is_confirmation_supported(game.season_week)
        and game.kickoff_at is not None
        and cycle - timedelta(days=7) <= game.kickoff_at < cycle
        and corrected.get(str(game.game_id)) != _fingerprint(game)
    ]
    return pending, corrections


class AutomaticReconciliationService:
    def __init__(
        self,
        repository,
        states,
        publications,
        service,
        *,
        season_loader=load_nflverse_season,
        schedule_provider=None,
        play_provider=None,
        jitter=lambda: random.uniform(1.0, 1.2),
    ) -> None:
        self.repository = repository
        self.states = states
        self.publications = publications
        self.service = service
        self.season_loader = season_loader
        self.schedule_provider = schedule_provider
        self.play_provider = play_provider
        self.jitter = jitter

    def needed(self, season: int, *, now: datetime) -> tuple[bool, int]:
        """Read-only preflight; zero provider calls, leases, or state writes."""
        state = self.states.load(season)
        pending, corrections = _candidates(
            self.repository.list_season(season), state, now
        )
        overdue = sum(_is_overdue(game, now) for game in pending)
        if now - correction_cycle(now) > timedelta(hours=24):
            overdue += len(corrections)
        return self._due(state, pending, corrections, now), overdue

    @staticmethod
    def _due(
        state: SourceState, pending: list[Game], corrections: list[Game], now: datetime
    ) -> bool:
        if not pending and not corrections:
            return False
        if state.cooldown_until and now < _time(state.cooldown_until):
            return False
        # New finals can use an already published revision. Never hide them
        # behind the cursor for games evaluated by a previous invocation.
        new_final = any(
            state.evaluated.get(f"due:{g.game_id}") != _fingerprint(g) for g in pending
        )
        if (
            state.revision is None
            and state.fallback_after
            and now < _time(state.fallback_after)
        ):
            new_final = False
        return new_final or state.next_probe is None or now >= _time(state.next_probe)

    def run(self, season: int, *, now: datetime) -> ReconciliationResult:
        token = self.states.acquire(season, now)
        if token is None:
            self._emit("lease_busy", season=season)
            return ReconciliationResult()
        state = self.states.load(season)
        try:
            games = self.repository.list_season(season)
            pending, corrections = _candidates(games, state, now)
            overdue = sum(_is_overdue(game, now) for game in pending)
            if not self._due(state, pending, corrections, now):
                self._emit(
                    "probe_skipped",
                    season=season,
                    pending=len(pending),
                    corrections=len(corrections),
                )
                return ReconciliationResult(overdue=overdue)
            return self._run(season, state, pending, corrections, now, overdue)
        finally:
            # Exceptions do not mark candidates evaluated. The lease prevents
            # a second writer overwriting processing state; crashes expire it.
            self.states.save_and_release(season, token, state)

    def _run(self, season, state, pending, corrections, now, overdue):
        probes = 1
        downloaded = False
        try:
            publication = self.publications.probe(
                season, Publication(state.revision, state.etag, state.modified), now=now
            )
            state.next_probe = next_probe_at(now).isoformat()
            # Do not advance the revision until the corresponding payload was
            # loaded. A failed download must still be attempted on the next run.
            changed = publication.revision != state.revision
            evaluated = {} if changed else state.evaluated
            cycle = correction_cycle(now).isoformat()
            eligible_corrections = (
                corrections
                if publication.modified and _time(publication.modified) >= _time(cycle)
                else []
            )
            candidates = [(g, "due", f"due:{g.game_id}") for g in pending]
            candidates += [
                (g, "correction", f"{cycle}:{g.game_id}") for g in eligible_corrections
            ]
            selected = [
                (g, mode, key)
                for g, mode, key in candidates
                if evaluated.get(key) != _fingerprint(g)
            ]

            # A schedule fix can unblock mapping/score validation independently
            # of PBP. Only probe it for this specific kind of unresolved work.
            schedule_changed = False
            if not changed and any(
                g.confirmation_retry.last_error
                in {
                    "nflverse_mapping_missing",
                    "nflverse_schedule_score_mismatch",
                    "nflverse_week_mismatch",
                }
                for g in pending
            ):
                probes += 1
                schedule = self.publications.probe_schedule(
                    Publication(state.schedule_revision), now=now
                )
                schedule_changed = (
                    schedule.revision is None
                    or schedule.revision != state.schedule_revision
                )
                if schedule_changed:
                    selected = candidates
            else:
                schedule = None

            if publication.revision is None:
                # Unsupported validators get a bounded, six-hour payload
                # fallback, rather than a full download on every fast probe.
                if state.fallback_after and now < _time(state.fallback_after):
                    selected = []
                else:
                    selected = candidates
            if not selected:
                state.failures = 0
                state.cooldown_until = None
                self._emit(
                    "source_unchanged",
                    season=season,
                    probes=probes,
                    pending=len(pending),
                )
                if now - _time(cycle) > timedelta(hours=24):
                    overdue += len(corrections)
                return ReconciliationResult(probes=probes, overdue=overdue)

            downloaded = True
            source = self.season_loader(
                season,
                schedule_provider=self.schedule_provider,
                play_provider=self.play_provider,
            )
            state.revision, state.etag, state.modified = (
                publication.revision,
                publication.etag,
                publication.modified,
            )
            state.evaluated = evaluated
            if schedule is not None:
                state.schedule_revision = schedule.revision
            state.fallback_after = (now + timedelta(hours=6)).isoformat()
            state.failures = 0
            state.cooldown_until = None
            if state.corrected_cycle != cycle:
                state.corrected_cycle, state.corrected = cycle, {}

            results = []
            for game, mode, key in selected:
                result = self.service.run(
                    season,
                    now=now,
                    mode=mode,
                    games=[game],
                    source=source,
                    game_ids=(game.game_id,) if mode == "correction" else None,
                    ignore_retry=True,
                    require_complete=True,
                )
                # Automatic not-ready corrections wait for a new source; they
                # do not fail like an explicit manual repair. Conflicts and
                # overdue work still raise attention through existing policy.
                results.append(
                    replace(result, manual_correction_failure=False, overdue=0)
                )
                if not result.stale_writes and not result.conflicts:
                    if (
                        result.confirmed_updates
                        or result.unchanged
                        or result.retries
                        or result.failures
                    ):
                        state.evaluated[key] = _fingerprint(game)
                    if (
                        (result.confirmed_updates or result.unchanged)
                        and publication.modified
                        and _time(publication.modified) >= _time(cycle)
                    ):
                        state.corrected[str(game.game_id)] = _fingerprint(game)
            # Keep the item bounded and retain only still-relevant candidates.
            keys = {key for _, _, key in candidates}
            state.evaluated = {
                key: value for key, value in state.evaluated.items() if key in keys
            }
            result = _combine_results(results)
            if now - _time(cycle) > timedelta(hours=24):
                overdue += sum(
                    state.corrected.get(str(game.game_id)) != _fingerprint(game)
                    for game in corrections
                )
            self._emit(
                "batch_processed",
                season=season,
                probes=probes,
                selected=result.selected,
                downloads=1,
            )
            return replace(result, downloads=1, probes=probes, overdue=overdue)
        except ProviderError as error:
            state.failures += 1
            delay = min(21600, 900 * 2 ** min(state.failures - 1, 5)) * self.jitter()
            delay = max(delay, getattr(error, "retry_after", 0))
            state.cooldown_until = (now + timedelta(seconds=delay)).isoformat()
            self._emit(
                "source_failure",
                season=season,
                cooldown_until=state.cooldown_until,
                exception_type=type(error).__name__,
                operation=error.operation,
            )
            return ReconciliationResult(
                downloads=int(downloaded),
                probes=probes,
                failures=1,
                source_failure=True,
                overdue=overdue,
            )

    @staticmethod
    def _emit(event: str, **fields) -> None:
        logging.getLogger(__name__).info(
            json.dumps({"event": f"nflverse_{event}", **fields}, sort_keys=True)
        )


def _build_runner():
    import boto3
    from ..game.dynamodb_repository import DynamoGameRepository

    timeout = _bounded_positive_float_environment(
        "NOSPOIL_NFLVERSE_TIMEOUT_SECONDS", 20, maximum=60
    )
    table = boto3.resource("dynamodb", config=_dynamodb_config()).Table(
        _required_environment("NOSPOIL_GAMES_TABLE")
    )
    repository = DynamoGameRepository(
        table,
        index_name=os.environ.get("NOSPOIL_SCHEDULE_INDEX") or "season-schedule-index",
    )
    schedule, plays = NflverseScheduleClient(
        timeout_seconds=timeout
    ), NflversePlayClient(timeout_seconds=timeout)
    return AutomaticReconciliationService(
        repository,
        DynamoSourceStateRepository(table),
        NflversePublicationClient(timeout_seconds=timeout),
        NflverseReconciliationService(repository, schedule, plays),
        schedule_provider=schedule,
        play_provider=plays,
    )


def main(argv=None, *, clock=lambda: datetime.now(UTC)) -> int:
    logger = _configure_application_logging()
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    now = clock()
    try:
        if now.utcoffset() != timedelta(0) or (
            args.season is not None and args.season < 1
        ):
            raise ValueError("a UTC clock and positive season are required")
        runner = _build_runner()
        seasons = _seasons_to_run("due", args.season, now)
        if args.preflight:
            needs = [runner.needed(season, now=now) for season in seasons]
            needed = any(value[0] for value in needs)
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"needed={str(needed).lower()}\n")
            print(
                json.dumps(
                    {"needed": needed, "overdue": sum(value[1] for value in needs)}
                )
            )
            if not needed and any(value[1] for value in needs):
                print("::error::nflverse reconciliation overdue reconciliation")
                return 1
            return 0
        result = _combine_results([runner.run(season, now=now) for season in seasons])
        _publish(
            _result_payload(
                result, mode="due", season=args.season or now.year, seasons=seasons
            ),
            attention=_result_attention(result),
        )
        return _exit_code(result)
    except Exception as error:
        _log_failure(
            logger,
            {
                "event": "automatic_reconciliation_failed",
                "exception_type": type(error).__name__,
            },
        )
        print("::error::nflverse automatic reconciliation failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
