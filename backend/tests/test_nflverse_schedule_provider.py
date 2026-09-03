from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backend.nospoil_nfl.game.models import SeasonPhase, Score
from backend.nospoil_nfl.providers import (
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
        columns=REQUIRED_COLUMNS.copy(),
    )


def test_loads_one_complete_season_from_a_polars_like_table() -> None:
    calls: list[list[int]] = []

    def loader(seasons: list[int]) -> PolarsLikeTable:
        calls.append(seasons)
        return make_table()

    result = NflverseScheduleClient(loader=loader).load_schedule(2024)

    assert isinstance(result, NflverseScheduleSeason)
    assert calls == [[2024]]
    assert len(result.games) == 4
    assert [game.season_week.phase for game in result.games] == [
        SeasonPhase.POSTSEASON,
        SeasonPhase.POSTSEASON,
        SeasonPhase.POSTSEASON,
        SeasonPhase.POSTSEASON,
    ]
    assert [game.season_week.week for game in result.games] == [1, 2, 3, 5]
    assert [game.nflverse_game_id for game in result.games] == [
        "2024_19_LAC_HOU",
        "2024_20_HOU_KC",
        "2024_21_WAS_PHI",
        "2024_22_KC_PHI",
    ]
    assert result.games[0].espn_id == "401671878"
    assert result.games[0].final_score == Score(home=32, away=12)
    assert result.games[3].final_score == Score(home=40, away=22)


def test_load_schedule_accepts_a_pandas_like_table() -> None:
    class PandasLikeTable:
        columns = REQUIRED_COLUMNS

        def to_dict(self, *, orient: str) -> list[dict[str, Any]]:
            assert orient == "records"
            return load_fixture()

    result = NflverseScheduleClient(
        loader=lambda seasons: PandasLikeTable()
    ).load_schedule(2024)

    assert len(result.games) == 4


def test_load_schedule_accepts_an_actual_pandas_dataframe() -> None:
    result = NflverseScheduleClient(
        loader=lambda seasons: pd.DataFrame(load_fixture())
    ).load_schedule(2024)

    assert len(result.games) == 4
    assert result.games[1].season_week.week == 2
    assert result.games[1].final_score == Score(home=23, away=14)


def test_rejects_table_conversion_failure_as_provider_data_error() -> None:
    class BrokenTable:
        columns = REQUIRED_COLUMNS

        def to_dicts(self) -> list[dict[str, Any]]:
            raise ValueError("malformed table")

    with pytest.raises(ProviderDataError, match="could not be converted"):
        NflverseScheduleClient(loader=lambda seasons: BrokenTable()).load_schedule(2024)


def test_rejects_missing_required_columns() -> None:
    table = make_table()
    table.columns.remove("away_score")
    table.rows = [
        {key: value for key, value in row.items() if key != "away_score"}
        for row in table.rows
    ]

    with pytest.raises(ProviderDataError, match="missing columns: away_score"):
        NflverseScheduleClient(loader=lambda seasons: table).load_schedule(2024)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("season", 2023, "does not match"),
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
            2024
        )


def test_rejects_inconsistent_score_pair() -> None:
    rows = load_fixture()
    rows[0]["home_score"] = None

    with pytest.raises(ProviderDataError, match="both present or both missing"):
        NflverseScheduleClient(loader=lambda seasons: make_table(rows)).load_schedule(
            2024
        )


@pytest.mark.parametrize("duplicate_field", ["game_id", "espn"])
def test_rejects_duplicate_identifiers(duplicate_field: str) -> None:
    rows = load_fixture()
    rows[1][duplicate_field] = rows[2][duplicate_field]

    with pytest.raises(ProviderDataError, match="must be unique"):
        NflverseScheduleClient(loader=lambda seasons: make_table(rows)).load_schedule(
            2024
        )


def test_source_failure_is_a_transport_error() -> None:
    def loader(seasons: list[int]) -> object:
        raise TimeoutError("source timed out")

    with pytest.raises(ProviderTransportError) as error:
        NflverseScheduleClient(loader=loader).load_schedule(2025)

    assert error.value.provider == "nflverse"
    assert error.value.operation == "schedule"


def test_duplicate_source_rows_are_rejected_at_collection_boundary() -> None:
    rows = load_fixture()
    rows.append(rows[1].copy())

    with pytest.raises(ProviderDataError, match="must be unique"):
        NflverseScheduleClient(loader=lambda seasons: make_table(rows)).load_schedule(
            2024
        )
