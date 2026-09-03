from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pytest

from backend.nospoil_nfl.game import (
    DomainValidationError,
    GameId,
    Score,
    SeasonPhase,
    SeasonWeek,
)
from backend.nospoil_nfl.nflverse import (
    NflverseGameMappingError,
    NflverseSeason,
    load_nflverse_season,
)
from backend.nospoil_nfl.providers import (
    NflverseGameId,
    NflversePlay,
    NflversePlayClient,
    NflversePlaySeason,
    NflverseScheduleClient,
    NflverseScheduleGame,
    NflverseScheduleSeason,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
REGULAR_ESPN_ID = GameId("401772840")
REGULAR_NFLVERSE_ID = NflverseGameId("2025_03_NYJ_TB")
OVERTIME_ESPN_ID = GameId("401772888")
OVERTIME_NFLVERSE_ID = NflverseGameId("2025_12_NYG_DET")
PLAYOFF_ESPN_ID = GameId("401671878")
PLAYOFF_NFLVERSE_ID = NflverseGameId("2024_19_LAC_HOU")


def make_schedule_game(
    *,
    season: int,
    phase: SeasonPhase,
    week: int,
    espn_id: GameId,
    nflverse_id: NflverseGameId,
) -> NflverseScheduleGame:
    return NflverseScheduleGame(
        season_week=SeasonWeek(season=season, phase=phase, week=week),
        nflverse_game_id=nflverse_id,
        espn_id=espn_id,
        final_score=Score(home=29, away=27),
    )


def make_play(
    nflverse_id: NflverseGameId,
    play_number: int,
) -> NflversePlay:
    return NflversePlay(
        nflverse_game_id=nflverse_id,
        play_number=play_number,
        period=4,
        home_win_probability=0.75,
        score=Score(home=29, away=27),
    )


@dataclass
class PolarsLikeTable:
    rows: list[dict[str, Any]]
    columns: list[str]

    def to_dicts(self) -> list[dict[str, Any]]:
        return self.rows


def load_fixture(name: str) -> list[dict[str, Any]]:
    with (FIXTURE_DIR / name).open() as fixture_file:
        return json.load(fixture_file)


def fixture_table(name: str) -> PolarsLikeTable:
    rows = load_fixture(name)
    return PolarsLikeTable(rows=rows, columns=list(rows[0]))


def test_loads_each_regular_season_collection_once_and_reuses_the_mapping() -> None:
    schedule_calls: list[list[int]] = []
    play_calls: list[list[int]] = []

    def schedule_loader(seasons: list[int]) -> PolarsLikeTable:
        schedule_calls.append(seasons)
        return fixture_table("nflverse_schedule_regular.json")

    def play_loader(seasons: list[int]) -> PolarsLikeTable:
        play_calls.append(seasons)
        return fixture_table("nflverse_play_provider.json")

    season = load_nflverse_season(
        2025,
        schedule_provider=NflverseScheduleClient(loader=schedule_loader),
        play_provider=NflversePlayClient(loader=play_loader),
    )
    regulation = season.find_game(REGULAR_ESPN_ID)
    overtime = season.find_game(OVERTIME_ESPN_ID)

    assert schedule_calls == [[2025]]
    assert play_calls == [[2025]]
    assert regulation.schedule_game.nflverse_game_id == REGULAR_NFLVERSE_ID
    assert [play.play_number for play in regulation.plays] == [1, 419, 4902]
    assert overtime.schedule_game.nflverse_game_id == OVERTIME_NFLVERSE_ID
    assert [play.play_number for play in overtime.plays] == [1, 5000]


def test_maps_a_postseason_espn_id_to_the_phase_local_schedule_game() -> None:
    schedule = NflverseScheduleClient(
        loader=lambda seasons: fixture_table("nflverse_schedule.json")
    ).load_schedule(2024)
    season = NflverseSeason(
        schedule=schedule,
        plays=NflversePlaySeason(
            season=2024,
            plays=(make_play(PLAYOFF_NFLVERSE_ID, 101),),
        ),
    )

    mapped = season.find_game(PLAYOFF_ESPN_ID)

    assert mapped.schedule_game.season_week == SeasonWeek(
        season=2024,
        phase=SeasonPhase.POSTSEASON,
        week=1,
    )
    assert mapped.schedule_game.nflverse_game_id == PLAYOFF_NFLVERSE_ID
    assert mapped.plays[0].nflverse_game_id == PLAYOFF_NFLVERSE_ID


def test_missing_schedule_mapping_fails_with_the_requested_identity() -> None:
    season = NflverseSeason(
        schedule=NflverseScheduleSeason(season=2025, games=()),
        plays=NflversePlaySeason(season=2025, plays=()),
    )

    with pytest.raises(
        NflverseGameMappingError,
        match="ESPN game 401000999 in season 2025",
    ) as error:
        season.find_game(GameId("401000999"))

    assert error.value.espn_id == "401000999"
    assert error.value.season == 2025


def test_valid_mapping_with_no_plays_remains_a_successful_mapping() -> None:
    schedule_game = make_schedule_game(
        season=2025,
        phase=SeasonPhase.REGULAR_SEASON,
        week=3,
        espn_id=REGULAR_ESPN_ID,
        nflverse_id=REGULAR_NFLVERSE_ID,
    )
    season = NflverseSeason(
        schedule=NflverseScheduleSeason(season=2025, games=(schedule_game,)),
        plays=NflversePlaySeason(season=2025, plays=()),
    )

    mapped = season.find_game(REGULAR_ESPN_ID)

    assert mapped.schedule_game == schedule_game
    assert mapped.plays == ()


def test_rejects_mismatched_schedule_and_play_seasons() -> None:
    with pytest.raises(
        DomainValidationError,
        match="schedule and play seasons must match",
    ):
        NflverseSeason(
            schedule=NflverseScheduleSeason(season=2024, games=()),
            plays=NflversePlaySeason(season=2025, plays=()),
        )
