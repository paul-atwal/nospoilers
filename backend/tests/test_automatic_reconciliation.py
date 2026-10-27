"""Exercise work gating across multiple independent scheduler invocations."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from backend.nospoil_nfl.game import (
    GameId,
    RatingState,
    Score,
    WriteResult,
)
from backend.nospoil_nfl.nflverse import NflverseSeason
from backend.nospoil_nfl.providers.errors import ProviderUnavailableError
from backend.nospoil_nfl.providers.nflverse_publication import (
    Publication,
    PublicationUnavailable,
)
from backend.nospoil_nfl.rating.automatic import AutomaticReconciliationService, main
from backend.nospoil_nfl.rating.publication import (
    confirmation_poll_seconds,
    correction_cycle,
    in_publication_window,
    next_probe_at,
)
from backend.nospoil_nfl.rating.reconciliation import NflverseReconciliationService
from backend.nospoil_nfl.rating.source_state import SourceState
from backend.tests.test_reconciliation import (
    NOW,
    FakeRepository,
    confirmed_rating,
    make_game,
    provisional_rating,
    source_for,
)


class States:
    def __init__(self):
        self.state = SourceState()
        self.busy = False
        self.writes = 0

    def load(self, season):
        return deepcopy(self.state)

    def acquire(self, season, now):
        if self.busy:
            return None
        self.busy = True
        return "lease"

    def save_and_release(self, season, token, state):
        assert self.busy and token == "lease"
        self.state = deepcopy(state)
        self.busy = False
        self.writes += 1


class Publications:
    def __init__(self):
        self.current = Publication("v1", "v1", NOW.isoformat())
        self.calls = []
        self.schedule_calls = 0
        self.schedule = Publication("schedule-v1")

    def probe(self, season, previous, *, now):
        self.calls.append((season, previous))
        if isinstance(self.current, Exception):
            raise self.current
        return self.current

    def probe_schedule(self, previous, *, now):
        self.schedule_calls += 1
        return self.schedule


class Harness:
    def __init__(self, *games, empty=False, complete=True):
        self.repository = FakeRepository(tuple(games))
        self.states = States()
        self.publications = Publications()
        self.loads = 0
        self.error = None
        self.set_source(*games, empty=empty, complete=complete)

    def set_source(self, *games, empty=False, complete=True, **kwargs):
        schedule, plays = source_for(
            tuple(games),
            empty_plays={str(g.game_id) for g in games} if empty else set(),
            **kwargs
        )
        plays = replace(
            plays.plays,
            plays=tuple(
                replace(play, game_complete=complete) for play in plays.plays.plays
            ),
        )
        self.source = NflverseSeason(schedule.schedule, plays)

    def load(self, *args, **kwargs):
        self.loads += 1
        if self.error:
            raise self.error
        return self.source

    def worker(self):
        return AutomaticReconciliationService(
            self.repository,
            self.states,
            self.publications,
            NflverseReconciliationService(
                self.repository, None, None, calculator=lambda value: 8.0
            ),
            season_loader=self.load,
            jitter=lambda: 1,
        )

    def run(self, now=NOW):
        # New worker each time: no accidental in-memory cache across runners.
        return self.worker().run(2026, now=now)


def test_no_work_and_preflight_make_no_provider_calls_or_state_writes():
    game = make_game("one", final_at=NOW, rating=confirmed_rating())
    harness = Harness(game)
    needed, overdue = harness.worker().needed(2026, now=NOW)
    assert not needed and not overdue
    assert harness.states.writes == 0
    assert harness.run().downloads == 0
    assert harness.publications.calls == []


def test_recent_final_confirms_immediately_and_one_snapshot_rates_all_games():
    games = [make_game(str(i), final_at=NOW - timedelta(seconds=1)) for i in range(3)]
    harness = Harness(*games)
    result = harness.run()
    assert result.downloads == harness.loads == 1
    assert result.confirmed_updates == 3
    assert len(harness.publications.calls) == 1
    assert harness.run(NOW + timedelta(minutes=15)).downloads == 0
    assert len(harness.publications.calls) == 1


def test_unchanged_version_never_redownloads_not_ready_game_and_new_version_bypasses_retry():
    game = make_game("one")
    harness = Harness(game, empty=True)
    assert harness.run().retries == 1
    assert (
        harness.repository.games[game.game_id].confirmation_retry.next_attempt_at > NOW
    )
    assert not harness.worker().needed(2026, now=NOW + timedelta(minutes=15))[0]
    assert harness.run(NOW + timedelta(hours=6)).downloads == 0
    assert harness.loads == 1
    harness.publications.current = Publication(
        "v2", "v2", (NOW + timedelta(hours=7)).isoformat()
    )
    harness.set_source(game)
    assert harness.run(NOW + timedelta(hours=12)).confirmed_updates == 1
    assert harness.loads == 2


def test_new_game_can_use_same_revision_seen_by_previous_batch():
    first = make_game("one", final_at=NOW)
    second = make_game("two", final_at=NOW)
    harness = Harness(first)
    assert harness.run().confirmed_updates == 1
    harness.repository.games[second.game_id] = second
    harness.set_source(first, second)
    assert harness.run(NOW + timedelta(minutes=1)).confirmed_updates == 1
    assert harness.loads == 2


def test_quiet_retry_wakes_at_next_publication_instead_of_sleeping_six_hours():
    quiet = datetime(2026, 10, 28, 8, tzinfo=UTC)
    assert next_probe_at(quiet) == quiet.replace(hour=9, minute=3)
    fast = quiet.replace(hour=9, minute=10)
    assert next_probe_at(fast) == fast + timedelta(minutes=15)
    assert confirmation_poll_seconds(quiet) == 3600
    assert confirmation_poll_seconds(fast) == 300
    assert confirmation_poll_seconds(quiet.replace(hour=9, minute=2)) == 60


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-23T05:33:00+00:00",
        "2026-10-25T22:03:00+00:00",
        "2026-10-26T00:07:00+00:00",
        "2026-10-26T05:33:00+00:00",
        "2026-10-27T05:33:00+00:00",
        "2027-01-01T09:03:00+00:00",
    ],
)
def test_utc_publication_hints_cover_game_windows_and_year_rollover(timestamp):
    assert in_publication_window(datetime.fromisoformat(timestamp))


def test_out_of_season_uses_six_hour_fallback():
    quiet = datetime(2026, 6, 1, 9, tzinfo=UTC)
    assert not in_publication_window(quiet)
    assert next_probe_at(quiet) == quiet + timedelta(hours=6)


def test_source_error_applies_shared_cooldown_and_honors_retry_after():
    games = [make_game(str(i), rating=provisional_rating()) for i in range(4)]
    harness = Harness(*games)
    harness.publications.current = PublicationUnavailable(retry_after=7200)
    result = harness.run()
    assert result.source_failure and result.downloads == 0
    assert harness.states.state.cooldown_until == (NOW + timedelta(hours=2)).isoformat()
    assert not harness.worker().needed(2026, now=NOW + timedelta(minutes=15))[0]
    harness.run(NOW + timedelta(minutes=15))
    assert len(harness.publications.calls) == 1
    assert all(
        g.rating.state is RatingState.PROVISIONAL
        for g in harness.repository.games.values()
    )


def test_failed_download_does_not_commit_observed_version():
    harness = Harness(make_game("one"))
    harness.error = ProviderUnavailableError(
        "outage", provider="nflverse", operation="plays"
    )
    assert harness.run().source_failure
    assert harness.states.state.revision is None
    harness.error = None
    assert harness.run(NOW + timedelta(minutes=15)).confirmed_updates == 1


def test_incomplete_pbp_does_not_confirm_even_when_final_score_matches():
    harness = Harness(make_game("one"), complete=False)
    assert harness.run().confirmed_updates == 0
    assert (
        harness.repository.games[GameId("one")].confirmation_retry.last_error
        == "nflverse_plays_incomplete"
    )


def test_stale_write_is_not_marked_evaluated_and_retries_same_revision():
    game = make_game("one")
    harness = Harness(game)
    original = harness.repository.apply_confirmed_rating
    harness.repository.apply_confirmed_rating = (
        lambda current, rating: WriteResult.STALE
    )
    assert harness.run().stale_writes == 1
    assert harness.states.state.evaluated == {}
    harness.repository.apply_confirmed_rating = original
    assert harness.run(NOW + timedelta(minutes=1)).confirmed_updates == 1


def test_crash_after_one_game_preserves_unfinished_work():
    first, second = make_game("one"), make_game("two")
    harness = Harness(first, second)
    original = harness.repository.apply_confirmed_rating

    def write(current, rating):
        if current.game_id == second.game_id:
            raise RuntimeError("write failed")
        return original(current, rating)

    harness.repository.apply_confirmed_rating = write
    with pytest.raises(RuntimeError):
        harness.run()
    harness.repository.apply_confirmed_rating = original
    assert harness.repository.games[first.game_id].rating.state is RatingState.CONFIRMED
    assert harness.run(NOW + timedelta(minutes=1)).confirmed_updates == 1


def test_missing_head_validators_limit_payload_fallback_to_six_hours():
    harness = Harness(make_game("one"), empty=True)
    harness.publications.current = Publication(None)
    assert harness.run().downloads == 1
    fast = NOW.replace(hour=9, minute=5) + timedelta(days=1)
    assert harness.run(fast).downloads == 1
    assert harness.run(fast + timedelta(minutes=15)).downloads == 0
    assert harness.loads == 2


def test_schedule_repair_can_unblock_without_pbp_revision_change():
    game = make_game("one")
    harness = Harness(game)
    harness.set_source(game, schedule_score=Score(24, 16))
    assert harness.run().retries == 1
    harness.set_source(game)
    assert harness.run(NOW + timedelta(hours=6)).confirmed_updates == 1
    assert harness.publications.schedule_calls == 1


def test_lease_excludes_overlapping_workers():
    harness = Harness(make_game("one"))
    harness.states.busy = True
    assert harness.run().probes == 0
    assert harness.publications.calls == []


def test_correction_waits_for_new_midweek_publication_then_stops():
    cycle = correction_cycle(NOW)
    game = make_game(
        "one", final_at=cycle - timedelta(days=2), rating=confirmed_rating()
    )
    harness = Harness(game)
    harness.publications.current = Publication(
        "old", "old", (cycle - timedelta(hours=1)).isoformat()
    )
    assert harness.run().downloads == 0
    harness.publications.current = Publication(
        "fresh", "fresh", (NOW + timedelta(hours=6)).isoformat()
    )
    assert harness.run(NOW + timedelta(hours=6)).confirmed_updates == 1
    assert harness.run(NOW + timedelta(hours=12)).downloads == 0
    assert len(harness.publications.calls) == 2
    assert harness.states.state.corrected[str(game.game_id)]


def test_historical_confirmed_games_do_not_trigger_correction():
    game = make_game(
        "one", final_at=NOW - timedelta(days=30), rating=confirmed_rating()
    )
    harness = Harness(game)
    assert harness.run().probes == 0


def test_preflight_cli_writes_action_output_without_running_worker(
    monkeypatch, tmp_path
):
    harness = Harness(make_game("one"))
    monkeypatch.setattr(
        "backend.nospoil_nfl.rating.automatic._build_runner", harness.worker
    )
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main(["--preflight", "--season", "2026"], clock=lambda: NOW) == 0
    assert output.read_text() == "needed=true\n"
    assert harness.publications.calls == []
    assert harness.states.writes == 0


def test_overdue_pending_work_is_reported_even_when_probe_is_not_due():
    game = make_game("one", final_at=NOW - timedelta(days=2))
    harness = Harness(game, empty=True)
    assert harness.run().overdue == 1
    assert harness.run(NOW + timedelta(minutes=15)).overdue == 1
    assert len(harness.publications.calls) == 1
