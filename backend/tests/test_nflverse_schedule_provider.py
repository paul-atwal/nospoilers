from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backend.nospoil_nfl.game.models import SeasonPhase, Score
from backend.nospoil_nfl.providers import (
    NFLVerseScheduleProvider,
    NflverseScheduleClient,
    NflverseScheduleSeason,
    ProviderDataError,
    ProviderTransportError,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
REQUIRED_COLUMNS = [
    "season",
    "game_type",
    "week",
    "game_id",
    "espn",
    "home_score",
    "away_score",
]


@dataclass
class PolarsLikeTable:
    rows: list[dict[str, Any]]
    columns: list[str]

    def to_dicts(self) -> list[dict[str, Any]]:
        return self.rows


def load_fixture() -> list[dict[str, Any]]:
    with (FIXTURE_DIR / "nflverse_schedule.json").open() as fixture_file:
        return json.load(fixture_file)


def make_table(rows: list[dict[str, Any]] | None = None) -> PolarsLikeTable:
    return PolarsLikeTable(
        rows=load_fixture() if rows is None else rows,
        columns=REQUIRED_COLUMNS,
    )


def test_loads_one_complete_season_from_a_polars_like_table() -> None:
    calls: list[list[int]] = []

    def loader(seasons: list[int]) -> PolarsLikeTable:
        calls.append(seasons)
        return make_table()

    result = NflverseScheduleClient(loader=loader).load_schedule(2025)

    assert isinstance(result, NflverseScheduleSeason)
    assert calls == [[2025]]
    assert len(result.games) == 4
    assert result.games[0].season_week.phase is SeasonPhase.PRESEASON
    assert result.games[0].final_score is None
    assert result.games[1].season_week.season == 2025
    assert result.games[1].season_week.phase is SeasonPhase.REGULAR_SEASON
    assert result.games[1].season_week.week == 3
    assert result.games[1].nflverse_game_id == "2025_03_NYJ_TB"
    assert result.games[1].espn_id == "401772840"
    assert result.games[1].final_score == Score(home=29, away=27)
    assert result.games[3].season_week.phase is SeasonPhase.POSTSEASON
    assert result.games[3].season_week.week == 19


def test_load_schedule_accepts_a_pandas_like_table() -> None:
    class PandasLikeTable:
        columns = REQUIRED_COLUMNS

        def to_dict(self, *, orient: str) -> list[dict[str, Any]]:
            assert orient == "records"
            return load_fixture()

    result = NflverseScheduleClient(
        loader=lambda seasons: PandasLikeTable()
    ).load_schedule(2025)

    assert len(result.games) == 4


def test_load_schedule_accepts_an_actual_pandas_dataframe() -> None:
    result = NflverseScheduleClient(
        loader=lambda seasons: pd.DataFrame(load_fixture())
    ).load_schedule(2025)

    assert len(result.games) == 4
    assert result.games[1].final_score == Score(home=29, away=27)


def test_rejects_table_conversion_failure_as_provider_data_error() -> None:
    class BrokenTable:
        columns = REQUIRED_COLUMNS

        def to_dicts(self) -> list[dict[str, Any]]:
            raise ValueError("malformed table")

    with pytest.raises(ProviderDataError, match="could not be converted"):
        NflverseScheduleClient(loader=lambda seasons: BrokenTable()).load_schedule(2025)


def test_rejects_missing_required_columns() -> None:
    table = make_table()
    table.columns.remove("away_score")
    table.rows = [
        {key: value for key, value in row.items() if key != "away_score"}
        for row in table.rows
    ]

    with pytest.raises(ProviderDataError, match="missing columns: away_score"):
        NflverseScheduleClient(loader=lambda seasons: table).load_schedule(2025)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("season", 2024, "does not match"),
        ("game_type", "UNKNOWN", "unsupported"),
        ("week", 0, "schedule.week"),
        ("game_id", "", "schedule.game_id"),
    ],
)
def test_rejects_invalid_schedule_rows(
    field: str,
    value: object,
    message: str,
) -> None:
    rows = load_fixture()
    rows[0][field] = value

    with pytest.raises(ProviderDataError, match=message):
        NflverseScheduleClient(loader=lambda seasons: make_table(rows)).load_schedule(
            2025
        )


def test_rejects_inconsistent_score_pair() -> None:
    rows = load_fixture()
    rows[0]["home_score"] = 7

    with pytest.raises(ProviderDataError, match="both present or both missing"):
        NflverseScheduleClient(loader=lambda seasons: make_table(rows)).load_schedule(
            2025
        )


@pytest.mark.parametrize("duplicate_field", ["game_id", "espn"])
def test_rejects_duplicate_identifiers(duplicate_field: str) -> None:
    rows = load_fixture()
    rows[1][duplicate_field] = rows[2][duplicate_field]

    with pytest.raises(ProviderDataError, match="must be unique"):
        NflverseScheduleClient(loader=lambda seasons: make_table(rows)).load_schedule(
            2025
        )


def test_source_failure_is_a_transport_error() -> None:
    def loader(seasons: list[int]) -> object:
        raise TimeoutError("source timed out")

    with pytest.raises(ProviderTransportError) as error:
        NflverseScheduleClient(loader=loader).load_schedule(2025)

    assert error.value.provider == "nflverse"
    assert error.value.operation == "schedule"


def test_schedule_contract_accepts_fixture_provider() -> None:
    class FixtureScheduleProvider:
        def load_schedule(self, season: int) -> NflverseScheduleSeason:
            assert season == 2025
            return NflverseScheduleClient(
                loader=lambda seasons: make_table()
            ).load_schedule(season)

    provider: NFLVerseScheduleProvider = FixtureScheduleProvider()

    assert len(provider.load_schedule(2025).games) == 4


def test_fixture_provider_does_not_require_live_nflreadpy() -> None:
    provider = NflverseScheduleClient(loader=lambda seasons: make_table())

    assert provider.load_schedule(2025).season == 2025


def test_duplicate_source_rows_are_rejected_at_collection_boundary() -> None:
    rows = load_fixture()
    rows.append(rows[1].copy())

    with pytest.raises(ProviderDataError, match="must be unique"):
        NflverseScheduleClient(loader=lambda seasons: make_table(rows)).load_schedule(
            2025
        )
