from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from backend.nospoil_nfl.api.snapshots import ReadSnapshotService
from backend.nospoil_nfl.api.calendar import SeasonCalendar, UnknownSeasonWeekError
from backend.nospoil_nfl.game import (
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    OddsSnapshot,
    RatingRetry,
    RatingSource,
    RatingState,
    RecordScope,
    RecordSnapshot,
    SeasonPhase,
    SeasonWeek,
    Score,
    TeamGameSnapshot,
    TeamRecord,
)
from backend.nospoil_nfl.game.read_repository import GameRepositoryError


NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


def game(
    game_id: str,
    *,
    season_week: SeasonWeek = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1),
    kickoff_at: datetime | None = NOW,
    status: GameStatus | None = None,
    rating: GameRating | None = None,
) -> Game:
    status = status or GameStatus(GameState.SCHEDULED)
    return Game(
        game_id=GameId(game_id),
        espn_id=game_id,
        nflverse_id=None,
        season_week=season_week,
        kickoff_at=kickoff_at,
        home=TeamGameSnapshot("home-" + game_id, "Home", "H", "home", None),
        away=TeamGameSnapshot("away-" + game_id, "Away", "A", "away", None),
        status=status,
        rating=rating or GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=NOW,
        schedule_updated_at=NOW,
    )


class FakeRepository:
    def __init__(self, games: list[Game]) -> None:
        self.games = games
        self.week_calls = 0
        self.season_calls = 0

    def list_week(self, season_week: SeasonWeek) -> list[Game]:
        self.week_calls += 1
        return list(self.games)

    def list_season(self, season: int) -> list[Game]:
        self.season_calls += 1
        return list(self.games)


def service(repository: FakeRepository, now: datetime = NOW) -> ReadSnapshotService:
    return ReadSnapshotService(repository, SeasonCalendar(), lambda: now)


def test_calendar_selects_first_boundary_and_holds_final_after_end() -> None:
    calendar = SeasonCalendar()
    assert calendar.current_week(datetime(2026, 8, 1, tzinfo=UTC)) == SeasonWeek(
        2026, SeasonPhase.PRESEASON, 1
    )
    assert calendar.current_week(datetime(2026, 9, 16, 7, tzinfo=UTC)) == SeasonWeek(
        2026, SeasonPhase.REGULAR_SEASON, 2
    )
    assert calendar.current_week(datetime(2027, 3, 1, tzinfo=UTC)) == SeasonWeek(
        2026, SeasonPhase.POSTSEASON, 5
    )
    assert calendar.bootstrap(datetime(2027, 3, 1, tzinfo=UTC))["pollAfterSeconds"] == 86400


def test_calendar_contains_exact_source_ordered_2026_weeks_and_end() -> None:
    calendar = SeasonCalendar()
    expected = (
        [("preseason", week) for week in range(1, 5)]
        + [("regular_season", week) for week in range(1, 19)]
        + [("postseason", week) for week in range(1, 6)]
    )
    assert [(entry.season_week.phase.value, entry.season_week.week) for entry in calendar.entries] == expected
    starts = (
        [datetime(2026, 8, day, 7, tzinfo=UTC) for day in (6, 13, 20, 27)]
        + [datetime(2026, 9, day, 7, tzinfo=UTC) for day in (6, 16, 23, 30)]
        + [datetime(2026, 10, day, 7, tzinfo=UTC) for day in (7, 14, 21, 28)]
        + [datetime(2026, 11, day, 8, tzinfo=UTC) for day in (4, 11, 18, 25)]
        + [datetime(2026, 12, day, 8, tzinfo=UTC) for day in (2, 9, 16, 23, 30)]
        + [datetime(2027, 1, day, 8, tzinfo=UTC) for day in (6, 13, 20, 27)]
        + [datetime(2027, 2, day, 8, tzinfo=UTC) for day in (3, 10)]
    )
    assert len(calendar.entries) == len(expected) == len(starts) == 27
    assert [entry.starts_at for entry in calendar.entries] == starts
    assert calendar.season_end == datetime(2027, 2, 16, 8, tzinfo=UTC)


def test_week_is_one_read_and_unknown_week_fails_before_read() -> None:
    repository = FakeRepository([game("b"), game("a", kickoff_at=NOW - timedelta(hours=1))])
    result = service(repository).week_snapshot(2026, "regular_season", 1)
    assert repository.week_calls == 1
    assert [item["id"] for item in result["games"]] == ["a", "b"]

    with pytest.raises(UnknownSeasonWeekError):
        service(repository).week_snapshot(2026, "regular_season", 19)
    assert repository.week_calls == 1


def test_empty_current_and_past_week_polling() -> None:
    repository = FakeRepository([])
    current = service(repository).week_snapshot(2026, "regular_season", 1)
    past = service(repository).week_snapshot(2026, "preseason", 1)
    assert current["games"] == []
    assert current["pollAfterSeconds"] == 300
    assert past["pollAfterSeconds"] is None
    assert current["snapshotAsOf"] is None


def test_final_rating_projection_and_poll_precedence() -> None:
    final = game(
        "final",
        status=GameStatus(GameState.FINAL, detail="Final", score=Score(27, 24)),
    )
    repository = FakeRepository([final])
    result = service(repository).week_snapshot(SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1))
    mapped = result["games"][0]
    assert mapped["rating"] == {
        "state": "pending",
        "score": None,
        "source": None,
        "modelVersion": None,
        "calculatedAt": None,
        "confirmedAt": None,
        "confirmationSupported": True,
        "confirmationWorkRemains": True,
    }
    assert result["pollAfterSeconds"] == 60


def _final_rating(state: RatingState, *, score: float = 7.0) -> GameRating:
    if state is RatingState.PROVISIONAL:
        return GameRating(
            state,
            RatingRetry(),
            score=score,
            source=RatingSource.ESPN,
            model_version="rating-v1",
            input_hash="input-hash",
            calculated_at=NOW,
        )
    if state is RatingState.CONFIRMED:
        return GameRating(
            state,
            RatingRetry(),
            score=score,
            source=RatingSource.NFLVERSE,
            model_version="rating-v2",
            input_hash="input-hash",
            calculated_at=NOW,
            confirmed_at=NOW,
        )
    if state is RatingState.UNAVAILABLE:
        return GameRating(
            state,
            RatingRetry(attempt_count=1, last_error="provider failed"),
        )
    return GameRating(state, RatingRetry())


@pytest.mark.parametrize(
    ("label", "status", "rating", "season_week", "expected"),
    [
        ("in-progress", GameStatus(GameState.IN_PROGRESS, period=2, score=Score(7, 3)), None, None, 30),
        ("delayed-started", GameStatus(GameState.DELAYED, score=Score(7, 3)), None, None, 30),
        ("delayed-unstarted", GameStatus(GameState.DELAYED), None, None, 60),
        ("postponed", GameStatus(GameState.POSTPONED), None, None, 900),
        ("cancelled", GameStatus(GameState.CANCELLED), None, None, None),
        ("final-provisional", GameStatus(GameState.FINAL, score=Score(7, 3)), RatingState.PROVISIONAL, None, 3600),
        ("final-unavailable", GameStatus(GameState.FINAL, score=Score(7, 3)), RatingState.UNAVAILABLE, None, 3600),
        ("final-confirmed", GameStatus(GameState.FINAL, score=Score(7, 3)), RatingState.CONFIRMED, None, None),
        ("unsupported-provisional", GameStatus(GameState.FINAL, score=Score(7, 3)), RatingState.PROVISIONAL, SeasonWeek(2026, SeasonPhase.POSTSEASON, 4), None),
        ("unsupported-unavailable", GameStatus(GameState.FINAL, score=Score(7, 3)), RatingState.UNAVAILABLE, SeasonWeek(2026, SeasonPhase.POSTSEASON, 4), None),
        ("unsupported-pending", GameStatus(GameState.FINAL, score=Score(7, 3)), RatingState.PENDING, SeasonWeek(2026, SeasonPhase.POSTSEASON, 4), 60),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_polling_lifecycle_and_confirmation_policy(
    label: str,
    status: GameStatus,
    rating: RatingState | None,
    season_week: SeasonWeek | None,
    expected: int | None,
) -> None:
    stored = game(
        label,
        status=status,
        rating=_final_rating(rating) if rating is not None else None,
        season_week=season_week or SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1),
        kickoff_at=None if status.state in {GameState.POSTPONED, GameState.CANCELLED} else NOW,
    )
    result = service(FakeRepository([stored])).week_snapshot(stored.season_week)
    assert result["pollAfterSeconds"] == expected


def test_projection_preserves_complete_ui_data_and_redacts_internal_fields() -> None:
    final = game(
        "representative",
        status=GameStatus(
            GameState.FINAL,
            detail="Final/OT",
            period=5,
            clock="00:00",
            score=Score(31, 28),
        ),
        rating=_final_rating(RatingState.CONFIRMED),
    )
    pregame_at = NOW + timedelta(hours=1)
    postgame_at = NOW + timedelta(hours=3)
    confirmed_at = NOW + timedelta(hours=4)
    pregame = RecordSnapshot(TeamRecord(2, 1, 0), RecordScope.REGULAR_SEASON, pregame_at)
    postgame = RecordSnapshot(TeamRecord(3, 1, 0), RecordScope.REGULAR_SEASON, postgame_at)
    final = replace(
        final,
        kickoff_at=NOW - timedelta(hours=2),
        home=replace(final.home, pregame_record=pregame, postgame_record=postgame),
        away=replace(final.away, postgame_record=postgame),
        broadcaster="NBC",
        odds=OddsSnapshot("SEA -3.5", NOW + timedelta(minutes=30)),
        rating=replace(final.rating, calculated_at=NOW + timedelta(hours=2), confirmed_at=confirmed_at),
        live_source_checked_at=NOW + timedelta(hours=5),
        live_state_updated_at=NOW + timedelta(hours=5),
    )
    mapped = service(FakeRepository([final])).week_snapshot(final.season_week)["games"][0]
    assert mapped["id"] == "representative"
    assert mapped["espnId"] == "representative"
    assert mapped["kickoffAt"] == "2026-09-10T16:00:00Z"
    assert mapped["home"]["id"] == "home-representative"
    assert mapped["home"]["logoKey"] == "home"
    assert mapped["status"] == {
        "state": "final", "detail": "Final/OT", "period": 5,
        "clock": "00:00", "score": {"home": 31, "away": 28},
    }
    assert mapped["broadcaster"] == "NBC"
    assert mapped["odds"] == {"details": "SEA -3.5", "updatedAt": "2026-09-10T18:30:00Z"}
    assert mapped["home"]["pregameRecord"]["snapshotAt"] == "2026-09-10T19:00:00Z"
    assert mapped["away"]["pregameRecord"] is None
    assert mapped["away"]["postgameRecord"]["snapshotAt"] == "2026-09-10T21:00:00Z"
    assert mapped["rating"]["source"] == "nflverse"
    assert mapped["rating"]["modelVersion"] == "rating-v2"
    assert mapped["rating"]["calculatedAt"] == "2026-09-10T20:00:00Z"
    assert mapped["rating"]["confirmedAt"] == "2026-09-10T22:00:00Z"
    assert mapped["freshness"] == {
        "scheduleCheckedAt": "2026-09-10T18:00:00Z",
        "scheduleUpdatedAt": "2026-09-10T18:00:00Z",
        "liveSourceCheckedAt": "2026-09-10T23:00:00Z",
        "liveStateUpdatedAt": "2026-09-10T23:00:00Z",
    }
    assert mapped["rating"]["confirmationSupported"] is True
    assert mapped["rating"]["confirmationWorkRemains"] is False
    assert mapped["rating"].keys() == {
        "state", "score", "source", "modelVersion", "calculatedAt", "confirmedAt",
        "confirmationSupported", "confirmationWorkRemains",
    }
    result_snapshot_as_of = service(FakeRepository([final])).week_snapshot(final.season_week)[
        "snapshotAsOf"
    ]
    assert result_snapshot_as_of == "2026-09-10T23:00:00Z"
    assert "inputHash" not in str(mapped)
    assert "provider failed" not in str(mapped)


def test_postgame_record_does_not_fill_missing_pregame_record() -> None:
    final = game(
        "records",
        status=GameStatus(GameState.FINAL, score=Score(10, 7)),
    )
    postgame = RecordSnapshot(
        TeamRecord(1, 0), RecordScope.REGULAR_SEASON, NOW + timedelta(hours=1)
    )
    final = replace(final, home=replace(final.home, postgame_record=postgame))
    mapped = service(FakeRepository([final])).week_snapshot(2026, "regular_season", 1)[
        "games"
    ][0]
    assert mapped["home"]["pregameRecord"] is None
    assert mapped["home"]["postgameRecord"]["wins"] == 1


def test_season_best_order_uses_score_then_kickoff_and_id() -> None:
    def rated(identifier: str, score: float, kickoff: datetime) -> Game:
        rating = GameRating(
            RatingState.PROVISIONAL,
            RatingRetry(),
            score=score,
            source=RatingSource.ESPN,
            model_version="rating-v1",
            input_hash="hash",
            calculated_at=NOW,
        )
        return game(
            identifier,
            kickoff_at=kickoff,
            status=GameStatus(GameState.FINAL, score=Score(1, 0)),
            rating=rating,
        )

    repository = FakeRepository(
        [
            game("unrated", kickoff_at=NOW - timedelta(hours=3)),
            rated("same-later", 8.0, NOW + timedelta(hours=1)),
            rated("same-earlier", 8.0, NOW),
            rated("best", 9.0, NOW + timedelta(hours=4)),
        ]
    )
    result = service(repository).season_snapshot(2026)
    assert [item["id"] for item in result["games"]] == [
        "best",
        "same-earlier",
        "same-later",
        "unrated",
    ]


def test_etag_is_stable_for_clock_ticks_within_poll_class() -> None:
    repository = FakeRepository([game("one", kickoff_at=NOW + timedelta(hours=3))])
    first = service(repository, NOW).week_snapshot(2026, "regular_season", 1)
    second = service(repository, NOW + timedelta(minutes=1)).week_snapshot(
        2026, "regular_season", 1
    )
    assert first.etag == second.etag


def test_etag_changes_at_policy_threshold_and_for_stored_data() -> None:
    stored = game("one", kickoff_at=NOW + timedelta(hours=3))
    repository = FakeRepository([stored])
    before = service(repository, NOW).week_snapshot(2026, "regular_season", 1)
    after = service(repository, NOW + timedelta(hours=1, minutes=1)).week_snapshot(
        2026, "regular_season", 1
    )
    assert before["pollAfterSeconds"] == 900
    assert after["pollAfterSeconds"] == 300
    assert before.etag != after.etag

    repository.games = [replace(stored, broadcaster="ESPN")]
    changed = service(repository, NOW).week_snapshot(2026, "regular_season", 1)
    assert changed.etag != before.etag


@pytest.mark.parametrize("snapshot_kind", ["week", "season"])
def test_calendar_version_changes_snapshot_etag_without_changing_body(
    snapshot_kind: str,
) -> None:
    repository = FakeRepository([game("one")])
    first_service = ReadSnapshotService(
        repository, SeasonCalendar(version="calendar-a"), lambda: NOW
    )
    second_service = ReadSnapshotService(
        repository, SeasonCalendar(version="calendar-b"), lambda: NOW
    )
    if snapshot_kind == "week":
        first = first_service.week_snapshot(2026, "regular_season", 1)
        second = second_service.week_snapshot(2026, "regular_season", 1)
    else:
        first = first_service.season_snapshot(2026)
        second = second_service.season_snapshot(2026)
    assert dict(first) == dict(second)
    assert first.etag != second.etag


def test_repository_failure_is_not_converted_to_empty() -> None:
    class Failing(FakeRepository):
        def list_week(self, season_week: SeasonWeek) -> list[Game]:
            raise GameRepositoryError("read failed")

    with pytest.raises(GameRepositoryError, match="read failed"):
        service(Failing([])).week_snapshot(2026, "regular_season", 1)
