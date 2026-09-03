from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import requests

from backend.nospoil_nfl.game.models import Score
from backend.nospoil_nfl.providers._nflreadpy import load_nflreadpy
from backend.nospoil_nfl.providers import (
    NflversePlayClient,
    NflversePlaySeason,
    ProviderDataError,
    ProviderTransportError,
    ProviderUnavailableError,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
REQUIRED_COLUMNS = [
    "season",
    "game_id",
    "play_id",
    "qtr",
    "home_wp",
    "total_home_score",
    "total_away_score",
]


@dataclass
class PolarsLikeTable:
    rows: list[dict[str, Any]]
    columns: list[str]

    def to_dicts(self) -> list[dict[str, Any]]:
        return self.rows


def load_fixture() -> list[dict[str, Any]]:
    with (FIXTURE_DIR / "nflverse_play_provider.json").open() as fixture_file:
        return json.load(fixture_file)


def make_table(rows: list[dict[str, Any]] | None = None) -> PolarsLikeTable:
    return PolarsLikeTable(
        rows=load_fixture() if rows is None else rows,
        columns=REQUIRED_COLUMNS.copy(),
    )


def test_loads_one_complete_season_in_source_order() -> None:
    calls: list[list[int]] = []

    def loader(seasons: list[int]) -> PolarsLikeTable:
        calls.append(seasons)
        return make_table()

    result = NflversePlayClient(loader=loader).load_plays(2025)

    assert isinstance(result, NflversePlaySeason)
    assert calls == [[2025]]
    assert len(result.plays) == 5
    assert [play.nflverse_game_id for play in result.plays] == [
        "2025_03_NYJ_TB",
        "2025_03_NYJ_TB",
        "2025_03_NYJ_TB",
        "2025_12_NYG_DET",
        "2025_12_NYG_DET",
    ]
    assert [play.play_number for play in result.plays] == [1, 419, 4902, 1, 5000]
    assert result.plays[0].score == Score(home=0, away=0)
    assert result.plays[2].home_win_probability == 1.0
    assert result.plays[3].period is None
    assert result.plays[3].home_win_probability is None
    assert result.plays[3].score is None
    assert result.plays[4].period == 5
    assert result.plays[4].score == Score(home=34, away=27)


def test_load_plays_accepts_an_actual_pandas_dataframe() -> None:
    result = NflversePlayClient(
        loader=lambda seasons: pd.DataFrame(load_fixture())
    ).load_plays(2025)

    assert len(result.plays) == 5
    assert result.plays[1].play_number == 419


def test_default_loader_scopes_and_restores_nflreadpy_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(timeout=23)
    nflreadpy = ModuleType("nflreadpy")
    config_module = ModuleType("nflreadpy.config")
    seen_timeouts: list[int] = []

    def update_config(*, timeout: int) -> None:
        seen_timeouts.append(timeout)
        config.timeout = timeout

    def load_pbp(seasons: list[int]) -> PolarsLikeTable:
        assert seasons == [2025]
        assert config.timeout == 3
        return make_table()

    nflreadpy.load_pbp = load_pbp  # type: ignore[attr-defined]
    config_module.get_config = lambda: config  # type: ignore[attr-defined]
    config_module.update_config = update_config  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nflreadpy", nflreadpy)
    monkeypatch.setitem(sys.modules, "nflreadpy.config", config_module)

    result = NflversePlayClient(timeout_seconds=2.5).load_plays(2025)

    assert len(result.plays) == 5
    assert config.timeout == 23
    assert seen_timeouts == [3, 23]


def test_rejects_table_conversion_failure_as_provider_data_error() -> None:
    class BrokenTable:
        columns = REQUIRED_COLUMNS

        def to_dicts(self) -> list[dict[str, Any]]:
            raise ValueError("malformed table")

    with pytest.raises(ProviderDataError, match="could not be converted"):
        NflversePlayClient(loader=lambda seasons: BrokenTable()).load_plays(2025)


def test_rejects_missing_required_columns() -> None:
    table = make_table()
    table.columns.remove("home_wp")
    table.rows = [
        {key: value for key, value in row.items() if key != "home_wp"}
        for row in table.rows
    ]

    with pytest.raises(ProviderDataError, match="missing columns: home_wp"):
        NflversePlayClient(loader=lambda seasons: table).load_plays(2025)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("season", 2024, "does not match"),
        ("game_id", "", "play.game_id"),
        ("play_id", -1, "play.play_id"),
        ("qtr", 0, "play.qtr"),
        ("home_wp", 1.1, "play.home_wp"),
    ],
)
def test_rejects_invalid_play_rows(
    field: str,
    value: object,
    message: str,
) -> None:
    rows = load_fixture()
    rows[0][field] = value

    with pytest.raises(ProviderDataError, match=message):
        NflversePlayClient(loader=lambda seasons: make_table(rows)).load_plays(2025)


def test_rejects_inconsistent_score_pair() -> None:
    rows = load_fixture()
    rows[0]["total_home_score"] = 7
    rows[0]["total_away_score"] = None

    with pytest.raises(ProviderDataError, match="both present or both missing"):
        NflversePlayClient(loader=lambda seasons: make_table(rows)).load_plays(2025)


def test_rejects_duplicate_game_and_play_number() -> None:
    rows = load_fixture()
    rows.append(rows[0].copy())

    with pytest.raises(ProviderDataError, match="must be unique"):
        NflversePlayClient(loader=lambda seasons: make_table(rows)).load_plays(2025)


def test_source_failure_is_a_transport_error() -> None:
    def loader(seasons: list[int]) -> object:
        raise TimeoutError("source timed out")

    with pytest.raises(ProviderTransportError) as error:
        NflversePlayClient(loader=loader).load_plays(2025)

    assert error.value.provider == "nflverse"
    assert error.value.operation == "plays"


def test_empty_table_requires_a_validated_schema() -> None:
    table = PolarsLikeTable(rows=[], columns=REQUIRED_COLUMNS)

    result = NflversePlayClient(loader=lambda seasons: table).load_plays(2025)

    assert result == NflversePlaySeason(season=2025, plays=())


def test_empty_untyped_list_is_not_an_empty_success() -> None:
    with pytest.raises(ProviderDataError, match="missing columns"):
        NflversePlayClient(loader=lambda seasons: []).load_plays(2025)


def _install_fake_nflreadpy(
    monkeypatch: pytest.MonkeyPatch,
    loader: Any,
) -> SimpleNamespace:
    config = SimpleNamespace(timeout=23)
    nflreadpy = ModuleType("nflreadpy")
    config_module = ModuleType("nflreadpy.config")
    nflreadpy.load_pbp = loader  # type: ignore[attr-defined]
    config_module.get_config = lambda: config  # type: ignore[attr-defined]
    config_module.update_config = lambda **kwargs: setattr(
        config, "timeout", kwargs["timeout"]
    )  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nflreadpy", nflreadpy)
    monkeypatch.setitem(sys.modules, "nflreadpy.config", config_module)
    return config


def test_nflreadpy_value_error_is_provider_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def loader(seasons: list[int]) -> object:
        raise ValueError("bad data")

    config = _install_fake_nflreadpy(monkeypatch, loader)

    with pytest.raises(ProviderDataError):
        load_nflreadpy("load_pbp", [2025], timeout_seconds=2, operation="plays")

    assert config.timeout == 23


def test_nflreadpy_timeout_is_provider_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def loader(seasons: list[int]) -> object:
        raise requests.Timeout("timed out")

    config = _install_fake_nflreadpy(monkeypatch, loader)

    with pytest.raises(ProviderTransportError):
        load_nflreadpy("load_pbp", [2025], timeout_seconds=2, operation="plays")

    assert config.timeout == 23


def test_nflreadpy_missing_source_data_is_provider_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_error = requests.HTTPError(response=SimpleNamespace(status_code=404))

    def loader(seasons: list[int]) -> object:
        raise ConnectionError("source data is not available") from source_error

    config = _install_fake_nflreadpy(monkeypatch, loader)

    with pytest.raises(ProviderUnavailableError):
        load_nflreadpy("load_pbp", [2025], timeout_seconds=2, operation="plays")

    assert config.timeout == 23
