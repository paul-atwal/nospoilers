from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.nospoil_nfl.game.live_rating import LiveRating
from backend.nospoil_nfl.game.models import GameId, GameState, GameStatus, Score
from backend.nospoil_nfl.game.updates import WriteResult
from backend.nospoil_nfl.providers.espn_live import (
    EspnLiveSummaryClient,
    LiveGameSummary,
    LiveObservation,
    parse_live_observations,
)
from backend.nospoil_nfl.providers.errors import (
    ProviderDataError,
    ProviderTransportError,
)
from backend.nospoil_nfl.rating.calculator import calculate_rating
from backend.nospoil_nfl.rating.input import RatingInput
from backend.nospoil_nfl.rating.live import LiveRatingService, MAX_LIVE_GAMES
from backend.nospoil_nfl.rating.live_calculator import (
    calculate_live_rating,
    live_raw_score,
)
from backend.tests.test_read_api_snapshots import (
    game as make_game,
    service as read_service,
    FakeRepository as ReadRepository,
)

NOW = datetime(2026, 10, 19, 18, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures/espn_live_overtime.json"


def payload():
    return json.loads(FIXTURE.read_text())


def observations():
    return tuple(
        LiveObservation(
            str(i),
            i * 100,
            0.35 + (i % 4) * 0.1,
            2,
            f"10:{59 - i:02d}",
            NOW - timedelta(seconds=30),
        )
        for i in range(12)
    )


def game(game_id="game-1"):
    return replace(
        make_game(game_id),
        kickoff_at=NOW,
        status=GameStatus(
            GameState.IN_PROGRESS, period=2, clock="10:30", score=Score(0, 0)
        ),
    )


def snapshot(**kwargs):
    values = dict(
        score=6.5,
        model_version="live-espn-v1",
        input_hash="hash",
        calculated_at=NOW,
        source_updated_at=NOW - timedelta(seconds=30),
        expires_at=NOW + timedelta(minutes=9),
        period=2,
        clock="10:30",
        last_play_sequence=1100,
        observation_count=12,
    )
    values.update(kwargs)
    return LiveRating(**values)


def summary(g, obs=None):
    return LiveGameSummary(
        g.game_id, obs or observations(), g.home.team_id, g.away.team_id
    )


def test_espn_drive_timing_includes_overtime_and_ignores_unjoined_baseline():
    p = payload()
    obs = parse_live_observations(p)
    assert len(obs) == len(p["winprobability"]) - 1
    assert any(x.period > 4 for x in obs)
    for x in obs:
        plays = {v["id"]: v for d in p["drives"]["previous"] for v in d["plays"]}
        assert x.clock == plays[x.play_id]["clock"]["displayValue"]
        assert x.period == plays[x.play_id]["period"]["number"]
    assert obs[-1].regulation_elapsed_seconds == 3600


def test_trailing_wp_waits_for_its_play_and_interior_gap_is_rejected():
    p = payload()
    expected = parse_live_observations(p)
    p["winprobability"].append(
        {"playId": "new-play-without-timing", "homeWinPercentage": 0.1}
    )
    assert parse_live_observations(p) == expected
    p["winprobability"].insert(
        20, {"playId": "missing-middle", "homeWinPercentage": 0.1}
    )
    with pytest.raises(ProviderDataError, match="interior"):
        parse_live_observations(p)


def test_current_drive_is_used_and_scoreboard_clock_cannot_advance_wp():
    p = payload()
    previous = p["drives"]["previous"].pop()
    p["drives"]["current"] = previous
    obs = parse_live_observations(p)
    p["header"]["competitions"][0]["status"].update(period=9, displayClock="0:00")
    assert parse_live_observations(p) == obs
    assert obs[-1].period != 9


@pytest.mark.parametrize("corruption", ["nan", "clock", "order", "duplicate"])
def test_invalid_timed_feeds_fail_closed(corruption):
    p = payload()
    if corruption == "nan":
        p["winprobability"][15]["homeWinPercentage"] = float("nan")
    elif corruption == "clock":
        p["drives"]["previous"][0]["plays"][15]["clock"]["displayValue"] = "30:00"
    elif corruption == "duplicate":
        p["winprobability"].insert(15, p["winprobability"][14])
    else:
        p["winprobability"][15], p["winprobability"][16] = (
            p["winprobability"][16],
            p["winprobability"][15],
        )
    with pytest.raises(ProviderDataError):
        parse_live_observations(p)


def test_live_client_requires_in_progress_and_final_client_remains_final_only(
    monkeypatch,
):
    from backend.nospoil_nfl.providers.espn_summary import EspnGameSummaryClient

    p = payload()
    client = EspnLiveSummaryClient()
    monkeypatch.setattr(client, "_fetch_payload", lambda _: p)
    game_id = GameId(p["header"]["competitions"][0]["id"])
    assert client.fetch_live_summary(game_id).game_id == game_id
    with pytest.raises(ProviderDataError):
        client.fetch_live_summary(GameId("wrong-game"))
    final_client = EspnGameSummaryClient()
    monkeypatch.setattr(final_client, "_fetch_payload", lambda _: p)
    with pytest.raises(ProviderDataError, match="not a completed final"):
        final_client.fetch_summary(game_id)
    for state, completed in [("post", True), ("pre", False), ("in", True)]:
        p["header"]["competitions"][0]["status"]["type"] = dict(
            state=state, completed=completed
        )
        with pytest.raises(ProviderDataError):
            client.fetch_live_summary(game_id)


def test_score_needs_enough_time_and_transitions_and_is_winner_free():
    obs = observations()
    assert calculate_live_rating(obs[:10]) is None
    assert (
        calculate_live_rating(tuple(replace(x, period=1, clock="14:00") for x in obs))
        is None
    )
    assert 0 <= calculate_live_rating(obs) <= 10
    for won, wp in [(True, (0.1, 0.3, 0.5, 1.0)), (False, (0.9, 0.7, 0.5, 0.0))]:
        assert round(max(0, min(10, live_raw_score(wp))), 1) == calculate_rating(
            RatingInput(wp, won)
        )


def test_overtime_uses_every_new_observation_without_inventing_a_finish_time():
    obs = tuple(
        LiveObservation(str(i), i * 10, 0.4 + (i % 4) * 0.02, 4, "0:10", NOW)
        for i in range(100)
    )
    first_ot = obs + (
        replace(
            obs[-1],
            play_id="ot1",
            sequence=1200,
            period=5,
            clock="9:55",
            home_win_probability=0.1,
        ),
    )
    late_ot = first_ot + (
        replace(
            first_ot[-1],
            play_id="ot2",
            sequence=1300,
            clock="1:00",
            home_win_probability=0.9,
        ),
    )
    second_ot = tuple(replace(x, period=6, clock="14:00") for x in late_ot)
    assert calculate_live_rating(first_ot) != calculate_live_rating(late_ot)
    assert calculate_live_rating(second_ot) == calculate_live_rating(late_ot)
    # Only the prefix is ever passed; unrelated future changes cannot leak in.
    source = list(late_ot)
    before = calculate_live_rating(tuple(source[:12]))
    source[12:] = [
        replace(x, home_win_probability=1 - x.home_win_probability) for x in source[12:]
    ]
    assert calculate_live_rating(tuple(source[:12])) == before


def test_live_service_bounds_work_and_keeps_final_games_out():
    games = {GameId(str(i)): game(str(i)) for i in range(15)}
    repository = Mock()
    repository.get.side_effect = games.get
    repository.apply_live_rating.return_value = WriteResult.APPLIED
    provider = Mock()
    provider.fetch_live_summary.side_effect = lambda gid: summary(games[gid])
    LiveRatingService(repository, provider).rate_due(list(games), now=NOW)
    assert provider.fetch_live_summary.call_count == MAX_LIVE_GAMES
    assert repository.apply_live_rating.call_count == MAX_LIVE_GAMES
    repository.get.side_effect = lambda gid: replace(
        games[gid], status=GameStatus(GameState.FINAL, score=Score(0, 0))
    )
    provider.reset_mock()
    LiveRatingService(repository, provider).rate_due(list(games), now=NOW)
    provider.fetch_live_summary.assert_not_called()


def test_service_budget_guard_prevents_http_or_writes():
    repository, provider = Mock(), Mock()
    LiveRatingService(repository, provider, remaining_time_ms=lambda: 17_000).rate_due(
        [GameId("a")], now=NOW
    )
    repository.get.assert_not_called()
    provider.fetch_live_summary.assert_not_called()


def test_source_failure_is_checkpointed_and_does_not_starve_other_games():
    games = [game("1"), game("2")]
    repository, provider = Mock(), Mock()
    repository.get.side_effect = games
    repository.apply_live_rating.return_value = WriteResult.APPLIED
    provider.fetch_live_summary.side_effect = (
        lambda gid: (_ for _ in ()).throw(
            ProviderTransportError("timeout", provider="espn", operation="summary")
        )
        if gid == "1"
        else summary(games[1])
    )
    LiveRatingService(repository, provider).rate_due(
        [g.game_id for g in games], now=NOW
    )
    assert repository.apply_live_rating.call_count == 2
    assert repository.apply_live_rating.call_args_list[0].args[1] is None
    assert repository.apply_live_rating.call_args_list[1].args[1].score is not None


@pytest.mark.parametrize(
    "kind",
    ["old", "future", "missing_timestamp", "wrong_game", "wrong_home", "regressed"],
)
def test_unfresh_or_mismatched_source_keeps_old_snapshot(kind):
    g = replace(game(), live_rating=snapshot(), live_rating_checked_at=NOW)
    obs = observations()
    result = summary(g, obs)
    if kind == "old":
        result = replace(
            result,
            observations=tuple(
                replace(x, wallclock_at=NOW - timedelta(hours=1)) for x in obs
            ),
        )
    elif kind == "future":
        result = replace(
            result,
            observations=tuple(
                replace(x, wallclock_at=NOW + timedelta(minutes=3)) for x in obs
            ),
        )
    elif kind == "missing_timestamp":
        result = replace(
            result, observations=tuple(replace(x, wallclock_at=None) for x in obs)
        )
    elif kind == "wrong_game":
        result = replace(result, game_id=GameId("other"))
    elif kind == "wrong_home":
        result = replace(result, home_team_id="other")
    else:
        result = replace(result, observations=obs[:-1])
    repository, provider = Mock(), Mock()
    service = LiveRatingService(repository, provider)
    assert service._rating(g, result, NOW) is None


def test_repeated_prefix_cannot_extend_expiry_and_new_prefix_recovers():
    g = game()
    service = LiveRatingService(Mock(), Mock())
    first = service._rating(g, summary(g), NOW)
    g = replace(g, live_rating=first, live_rating_checked_at=NOW)
    assert service._rating(g, summary(g), NOW + timedelta(minutes=2)) == first
    changed = observations() + (
        replace(
            observations()[-1],
            play_id="new",
            sequence=1200,
            wallclock_at=NOW + timedelta(minutes=2),
            home_win_probability=0.1,
        ),
    )
    next_rating = service._rating(g, summary(g, changed), NOW + timedelta(minutes=2))
    assert next_rating.expires_at > first.expires_at
    assert next_rating.input_hash != first.input_hash


def test_api_projection_expires_with_a_new_etag_and_keeps_completed_rating_pending():
    g = replace(game(), live_rating=snapshot(), live_rating_checked_at=NOW)
    repo = ReadRepository([g])
    fresh = read_service(repo, NOW).week_snapshot(g.season_week)
    assert fresh["games"][0]["liveRating"]["score"] == 6.5
    assert fresh["games"][0]["rating"]["score"] is None
    stale = read_service(repo, NOW + timedelta(minutes=10)).week_snapshot(g.season_week)
    assert stale["games"][0]["liveRating"] is None
    assert fresh.etag != stale.etag
    for status in [
        GameStatus(GameState.DELAYED, period=2, score=Score(0, 0)),
        GameStatus(GameState.FINAL, score=Score(0, 0)),
    ]:
        repo.games = [replace(g, status=status)]
        assert (
            read_service(repo, NOW).week_snapshot(g.season_week)["games"][0][
                "liveRating"
            ]
            is None
        )


def test_write_budget_reserves_enough_time_for_dynamodb_retries():
    g = game()
    repository, provider = Mock(), Mock()
    repository.get.return_value = g
    provider.fetch_live_summary.return_value = summary(g)
    # Entry, revalidation and fetch pass the guard; HTTP then consumes enough
    # time that a potentially retried DynamoDB write must wait for another tick.
    remaining = iter([20_000, 20_000, 20_000, 17_000])
    service = LiveRatingService(
        repository, provider, remaining_time_ms=lambda: next(remaining)
    )
    service.rate_due([g.game_id], now=NOW)
    provider.fetch_live_summary.assert_called_once_with(g.game_id)
    repository.apply_live_rating.assert_not_called()


def test_overlapping_drives_ignore_display_metadata_and_keep_each_wp_once():
    from copy import deepcopy

    p = payload()
    expected = parse_live_observations(p)
    repeated = deepcopy(p["drives"]["previous"][0]["plays"][-5:])
    repeated[-1]["scoreValue"] = 0
    repeated[-1]["text"] = "Updated provider display text"
    repeated[-1]["period"]["displayValue"] = "Overtime"
    repeated[-1]["clock"]["value"] = 123
    p["drives"]["current"] = {"plays": repeated}
    assert parse_live_observations(p) == expected
    assert calculate_live_rating(parse_live_observations(p)) == calculate_live_rating(
        expected
    )


@pytest.mark.parametrize("field", ["sequenceNumber", "period", "clock", "wallclock"])
def test_overlapping_drives_still_reject_conflicting_rating_inputs(field):
    from copy import deepcopy

    p = payload()
    repeated = deepcopy(p["drives"]["previous"][0]["plays"][-1])
    replacements = {
        "sequenceNumber": "999999",
        "period": {"number": 6},
        "clock": {"displayValue": "0:01"},
        "wallclock": "2026-10-19T18:00:01Z",
    }
    repeated[field] = replacements[field]
    p["drives"]["current"] = {"plays": [repeated]}
    with pytest.raises(ProviderDataError, match="conflicting duplicate play"):
        parse_live_observations(p)
