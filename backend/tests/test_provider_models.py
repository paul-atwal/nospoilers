from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.nospoil_nfl.game import (
    DomainValidationError,
    GameState,
    GameStatus,
    RecordScope,
    RecordSnapshot,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamRecord,
)
from backend.nospoil_nfl.providers import (
    NflversePlay,
    NflversePlaySeason,
    ScheduleGame,
    ScheduleTeam,
)


OBSERVED_AT = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)
SEASON_WEEK = SeasonWeek(
    season=2026,
    phase=SeasonPhase.REGULAR_SEASON,
    week=1,
)


def make_team(team_id: str, record: RecordSnapshot | None = None) -> ScheduleTeam:
    return ScheduleTeam(
        team_id=team_id,
        display_name=f"Team {team_id}",
        abbreviation=team_id.upper(),
        record=record,
    )


def make_schedule_game(
    *,
    kickoff_at: datetime | None = OBSERVED_AT,
    status: GameStatus = GameStatus(GameState.SCHEDULED),
) -> ScheduleGame:
    return ScheduleGame(
        game_id="401000001",
        kickoff_at=kickoff_at,
        home=make_team("home"),
        away=make_team("away"),
        status=status,
    )


def make_play(play_number: int, game_id: str = "2026_01_HME_AWY") -> NflversePlay:
    return NflversePlay(
        nflverse_game_id=game_id,
        play_number=play_number,
        period=1,
        home_win_probability=0.5,
        score=Score(0, 0),
    )


def test_schedule_team_requires_a_scoped_record_snapshot() -> None:
    with pytest.raises(DomainValidationError, match="RecordSnapshot"):
        make_team("home", TeamRecord(wins=1, losses=0))  # type: ignore[arg-type]


def test_schedule_team_preserves_record_scope_and_observation_time() -> None:
    snapshot = RecordSnapshot(
        record=TeamRecord(wins=1, losses=0),
        scope=RecordScope.PRESEASON,
        snapshot_at=OBSERVED_AT,
    )

    team = make_team("home", snapshot)

    assert team.record == snapshot
    assert team.record.scope is RecordScope.PRESEASON
    assert team.record.snapshot_at == OBSERVED_AT


def test_schedule_game_requires_kickoff_for_scheduled_status() -> None:
    with pytest.raises(
        DomainValidationError,
        match="scheduled game requires kickoff_at",
    ):
        make_schedule_game(kickoff_at=None)


def test_nflverse_play_season_rejects_duplicate_game_and_play_number() -> None:
    with pytest.raises(
        DomainValidationError,
        match="play game IDs and play numbers must be unique",
    ):
        NflversePlaySeason(
            season=2026,
            plays=(make_play(1), make_play(1)),
        )
