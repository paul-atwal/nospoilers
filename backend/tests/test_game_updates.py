from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.nospoil_nfl.game import (
    DomainValidationError,
    GameId,
    GameState,
    GameStatus,
    LiveStatusUpdate,
    ScheduleUpdate,
    SeasonPhase,
    SeasonWeek,
    TeamScheduleUpdate,
    UNSET,
)


OBSERVED_AT = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


def make_team(team_id: str) -> TeamScheduleUpdate:
    return TeamScheduleUpdate(
        team_id=team_id,
        display_name=f"Team {team_id}",
        abbreviation=team_id.upper(),
    )


def test_schedule_update_distinguishes_absent_and_explicit_optional_values() -> None:
    update = ScheduleUpdate(
        game_id=GameId("401000001"),
        observed_at=OBSERVED_AT,
        season_week=SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1),
        kickoff_at=OBSERVED_AT + timedelta(hours=1),
        home=make_team("home"),
        away=TeamScheduleUpdate(
            team_id="away",
            display_name="Team away",
            abbreviation="AWAY",
            logo_key=None,
        ),
        broadcaster=None,
    )

    assert update.home.logo_key is UNSET
    assert update.away.logo_key is None
    assert update.broadcaster is None
    assert update.odds is UNSET


def test_live_status_update_requires_a_utc_observation_time() -> None:
    with pytest.raises(
        DomainValidationError,
        match="observed_at must be a timezone-aware UTC datetime",
    ):
        LiveStatusUpdate(
            game_id=GameId("401000001"),
            observed_at=datetime(2026, 9, 10, 18, 0),
            status=GameStatus(GameState.SCHEDULED),
        )


def test_schedule_update_rejects_one_team_on_both_sides() -> None:
    with pytest.raises(
        DomainValidationError,
        match="home and away teams must be different",
    ):
        ScheduleUpdate(
            game_id=GameId("401000001"),
            observed_at=OBSERVED_AT,
            season_week=SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1),
            kickoff_at=OBSERVED_AT + timedelta(hours=1),
            home=make_team("team"),
            away=make_team("team"),
        )
