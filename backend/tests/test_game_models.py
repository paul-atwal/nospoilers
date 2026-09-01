from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.nospoil_nfl.game import (
    DomainValidationError,
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    RatingRetry,
    RatingSource,
    RatingState,
    RecordScope,
    RecordSnapshot,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
    TeamRecord,
)
from backend.nospoil_nfl.game.repository import validate_rating_update


CHECKED_AT = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


def make_record_snapshot(
    scope: RecordScope = RecordScope.REGULAR_SEASON,
    *,
    snapshot_at: datetime = CHECKED_AT,
) -> RecordSnapshot:
    return RecordSnapshot(
        record=TeamRecord(wins=0, losses=0),
        scope=scope,
        snapshot_at=snapshot_at,
    )


def make_team(
    team_id: str,
    *,
    scope: RecordScope = RecordScope.REGULAR_SEASON,
) -> TeamGameSnapshot:
    return TeamGameSnapshot(
        team_id=team_id,
        display_name=f"Team {team_id}",
        abbreviation=team_id.upper(),
        logo_key=team_id,
        pregame_record=make_record_snapshot(scope),
    )


def make_pending_rating() -> GameRating:
    return GameRating(
        state=RatingState.PENDING,
        retry=RatingRetry(),
    )


def make_provisional_rating() -> GameRating:
    return GameRating(
        state=RatingState.PROVISIONAL,
        retry=RatingRetry(),
        score=6.5,
        source=RatingSource.ESPN,
        model_version="rating-v1",
        input_hash="input-hash",
        calculated_at=CHECKED_AT,
    )


def make_confirmed_rating() -> GameRating:
    return GameRating(
        state=RatingState.CONFIRMED,
        retry=RatingRetry(),
        score=6.5,
        source=RatingSource.NFLVERSE,
        model_version="rating-v1",
        input_hash="input-hash",
        calculated_at=CHECKED_AT,
        confirmed_at=CHECKED_AT,
    )


def make_game(**overrides: Any) -> Game:
    values: dict[str, Any] = {
        "game_id": GameId("game-1"),
        "espn_id": "401000001",
        "nflverse_id": None,
        "season_week": SeasonWeek(
            season=2026,
            phase=SeasonPhase.REGULAR_SEASON,
            week=1,
        ),
        "kickoff_at": CHECKED_AT + timedelta(hours=1),
        "home": make_team("home"),
        "away": make_team("away"),
        "status": GameStatus(state=GameState.SCHEDULED),
        "rating": make_pending_rating(),
        "schedule_checked_at": CHECKED_AT,
        "schedule_updated_at": CHECKED_AT,
    }
    values.update(overrides)
    return Game(**values)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (
            GameState.FINAL,
            "final status requires a score",
        ),
        (
            GameState.IN_PROGRESS,
            "in-progress status requires a positive period and score",
        ),
    ],
    ids=["final-score", "live-period-and-score"],
)
def test_status_rejects_incomplete_game_data(
    state: GameState,
    message: str,
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        GameStatus(state=state)


def test_postseason_game_requires_regular_season_record_scope() -> None:
    game = make_game(
        season_week=SeasonWeek(
            season=2026,
            phase=SeasonPhase.POSTSEASON,
            week=1,
        )
    )

    assert game.home.pregame_record.scope is RecordScope.REGULAR_SEASON


def test_postseason_game_rejects_non_regular_season_record_scope() -> None:
    with pytest.raises(
        DomainValidationError,
        match="home pregame record must use scope regular_season",
    ):
        make_game(
            season_week=SeasonWeek(
                season=2026,
                phase=SeasonPhase.POSTSEASON,
                week=1,
            ),
            home=make_team("home", scope=RecordScope.PRESEASON),
        )


def test_provisional_rating_requires_espn_provenance() -> None:
    with pytest.raises(
        DomainValidationError,
        match="provisional rating requires source espn",
    ):
        GameRating(
            state=RatingState.PROVISIONAL,
            retry=RatingRetry(),
            score=6.5,
            source=RatingSource.NFLVERSE,
            model_version="rating-v1",
            input_hash="input-hash",
            calculated_at=CHECKED_AT,
        )


def test_confirmed_rating_cannot_have_retry_work() -> None:
    with pytest.raises(
        DomainValidationError,
        match="confirmed rating cannot have retry work",
    ):
        GameRating(
            state=RatingState.CONFIRMED,
            retry=RatingRetry(
                attempt_count=1,
                next_attempt_at=CHECKED_AT + timedelta(hours=1),
                last_error="temporary source failure",
            ),
            score=6.5,
            source=RatingSource.NFLVERSE,
            model_version="rating-v1",
            input_hash="input-hash",
            calculated_at=CHECKED_AT,
            confirmed_at=CHECKED_AT,
        )


def test_unavailable_rating_has_no_automatic_retry() -> None:
    with pytest.raises(
        DomainValidationError,
        match="unavailable rating cannot have an automatic next attempt",
    ):
        GameRating(
            state=RatingState.UNAVAILABLE,
            retry=RatingRetry(
                attempt_count=3,
                next_attempt_at=CHECKED_AT + timedelta(hours=1),
                last_error="retry policy exhausted",
            ),
        )


def test_non_final_game_cannot_have_a_completed_rating() -> None:
    with pytest.raises(
        DomainValidationError,
        match="only a final game can have a completed rating state",
    ):
        make_game(rating=make_provisional_rating())


def test_final_game_accepts_a_confirmed_nflverse_rating() -> None:
    game = make_game(
        status=GameStatus(
            state=GameState.FINAL,
            detail="Final",
            score=Score(home=24, away=17),
        ),
        rating=make_confirmed_rating(),
    )

    assert game.rating.state is RatingState.CONFIRMED


def test_rating_update_accepts_a_final_pending_game() -> None:
    current = make_game(
        status=GameStatus(
            state=GameState.FINAL,
            score=Score(home=24, away=17),
        )
    )

    validate_rating_update(current, make_provisional_rating())


def test_rating_update_rejects_a_non_final_game() -> None:
    with pytest.raises(
        DomainValidationError,
        match="rating updates require a final game",
    ):
        validate_rating_update(make_game(), make_pending_rating())


def test_rating_update_rejects_a_confirmed_to_provisional_transition() -> None:
    current = make_game(
        status=GameStatus(
            state=GameState.FINAL,
            score=Score(home=24, away=17),
        ),
        rating=make_confirmed_rating(),
    )

    with pytest.raises(
        DomainValidationError,
        match="rating update has an invalid rating transition",
    ):
        validate_rating_update(current, make_provisional_rating())


def test_non_final_game_cannot_have_postgame_records() -> None:
    home = replace(
        make_team("home"),
        pregame_record=None,
        postgame_record=make_record_snapshot(
            snapshot_at=CHECKED_AT + timedelta(hours=4),
        ),
    )

    with pytest.raises(
        DomainValidationError,
        match="only a final game can have postgame record snapshots",
    ):
        make_game(home=home)


def test_scheduled_game_can_have_unknown_pregame_records() -> None:
    game = make_game(
        home=replace(make_team("home"), pregame_record=None),
        away=replace(make_team("away"), pregame_record=None),
    )

    assert game.home.pregame_record is None
    assert game.away.pregame_record is None


def test_postgame_record_is_validated_when_pregame_record_is_unknown() -> None:
    home = replace(
        make_team("home", scope=RecordScope.PRESEASON),
        pregame_record=None,
        postgame_record=make_record_snapshot(
            scope=RecordScope.REGULAR_SEASON,
            snapshot_at=CHECKED_AT + timedelta(hours=4),
        ),
    )

    with pytest.raises(
        DomainValidationError,
        match="home postgame record must use scope preseason",
    ):
        make_game(
            season_week=SeasonWeek(
                season=2026,
                phase=SeasonPhase.PRESEASON,
                week=1,
            ),
            home=home,
            away=make_team("away", scope=RecordScope.PRESEASON),
            status=GameStatus(
                state=GameState.FINAL,
                detail="Final",
                score=Score(home=24, away=17),
            ),
        )


def test_change_timestamps_cannot_be_newer_than_their_source_check() -> None:
    with pytest.raises(
        DomainValidationError,
        match="schedule_updated_at cannot follow schedule_checked_at",
    ):
        make_game(schedule_updated_at=CHECKED_AT + timedelta(seconds=1))

    with pytest.raises(
        DomainValidationError,
        match="live_state_updated_at requires live_source_checked_at",
    ):
        make_game(live_state_updated_at=CHECKED_AT)


def test_postponed_game_can_have_an_unknown_kickoff() -> None:
    game = make_game(
        kickoff_at=None,
        status=GameStatus(state=GameState.POSTPONED),
    )

    assert game.kickoff_at is None


def test_domain_timestamps_must_be_utc() -> None:
    non_utc = datetime.fromisoformat("2026-09-10T11:00:00-07:00")

    with pytest.raises(
        DomainValidationError,
        match="record snapshot_at must be a timezone-aware UTC datetime",
    ):
        make_record_snapshot(snapshot_at=non_utc)
