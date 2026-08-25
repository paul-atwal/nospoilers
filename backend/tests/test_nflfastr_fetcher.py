from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from backend import nflfastr_fetcher
from backend.nflfastr_fetcher import NFLFastRFetcher
from backend.nflverse_normalizer import normalize_nflverse_game_data


FIXTURE_DIR = Path(__file__).parent / "fixtures"
REGULATION_FIXTURE = "nflverse_pbp_regulation.json"
OVERTIME_FIXTURE = "nflverse_pbp_overtime.json"


def load_plays(name: str) -> pd.DataFrame:
    with (FIXTURE_DIR / name).open() as fixture_file:
        return pd.DataFrame(json.load(fixture_file))


def make_fetcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NFLFastRFetcher:
    monkeypatch.delenv("REDIS_URL", raising=False)
    return NFLFastRFetcher(cache_dir=str(tmp_path))


def test_load_season_pbp_converts_non_pandas_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = load_plays(REGULATION_FIXTURE)

    class SourceTable:
        def to_pandas(self) -> pd.DataFrame:
            return expected

    load_pbp = Mock(return_value=SourceTable())
    monkeypatch.setattr(nflfastr_fetcher.nfl, "load_pbp", load_pbp)
    fetcher = make_fetcher(tmp_path, monkeypatch)

    result = fetcher._load_season_pbp(2025)

    pd.testing.assert_frame_equal(result, expected)
    load_pbp.assert_called_once_with([2025])


def test_fetch_game_wp_uses_cache_without_loading_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = make_fetcher(tmp_path, monkeypatch)
    cached = {"game_id": "cached-game", "excitement_score": 7.5}
    fetcher.cache["cached-game"] = cached
    load_season = Mock()
    monkeypatch.setattr(fetcher, "_load_season_pbp", load_season)

    result = fetcher.fetch_game_wp("cached-game")

    assert result is cached
    load_season.assert_not_called()


def test_fetch_game_wp_normalizes_and_caches_selected_game(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regulation_plays = load_plays(REGULATION_FIXTURE)
    overtime_plays = load_plays(OVERTIME_FIXTURE)
    season_plays = pd.concat([regulation_plays, overtime_plays], ignore_index=True)
    game_id = regulation_plays.iloc[0]["game_id"]
    expected = normalize_nflverse_game_data(game_id, regulation_plays)
    fetcher = make_fetcher(tmp_path, monkeypatch)
    load_season = Mock(return_value=season_plays)
    save_cache = Mock()
    normalizer = Mock(wraps=normalize_nflverse_game_data)
    monkeypatch.setattr(fetcher, "_load_season_pbp", load_season)
    monkeypatch.setattr(fetcher, "_save_cache", save_cache)
    monkeypatch.setattr(
        nflfastr_fetcher,
        "normalize_nflverse_game_data",
        normalizer,
    )

    result = fetcher.fetch_game_wp(game_id)

    assert result == expected
    assert fetcher.cache[game_id] == expected
    load_season.assert_called_once_with(2025)
    normalizer.assert_called_once()
    save_cache.assert_called_once_with()


def test_fetch_week_games_normalizes_each_valid_game_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regulation_plays = load_plays(REGULATION_FIXTURE).assign(week=12)
    overtime_plays = load_plays(OVERTIME_FIXTURE)
    invalid_plays = overtime_plays.assign(game_id="invalid-game")
    invalid_plays.loc[invalid_plays.index[0], "home_wp"] = 1.1
    season_plays = pd.concat(
        [regulation_plays, overtime_plays, invalid_plays],
        ignore_index=True,
    )
    regulation_id = regulation_plays.iloc[0]["game_id"]
    overtime_id = overtime_plays.iloc[0]["game_id"]
    expected = {
        regulation_id: normalize_nflverse_game_data(regulation_id, regulation_plays),
        overtime_id: normalize_nflverse_game_data(overtime_id, overtime_plays),
    }
    fetcher = make_fetcher(tmp_path, monkeypatch)
    load_season = Mock(return_value=season_plays)
    save_cache = Mock()
    normalizer = Mock(wraps=normalize_nflverse_game_data)
    monkeypatch.setattr(fetcher, "_load_season_pbp", load_season)
    monkeypatch.setattr(fetcher, "_save_cache", save_cache)
    monkeypatch.setattr(
        nflfastr_fetcher,
        "normalize_nflverse_game_data",
        normalizer,
    )

    result = fetcher.fetch_week_games(week=12, season=2025)

    assert result == expected
    assert fetcher.cache == expected
    assert "invalid-game" not in result
    load_season.assert_called_once_with(2025)
    assert normalizer.call_count == 3
    save_cache.assert_called_once_with()


def test_fetch_methods_preserve_empty_results_when_loading_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = make_fetcher(tmp_path, monkeypatch)
    monkeypatch.setattr(fetcher, "_load_season_pbp", Mock(return_value=None))
    save_cache = Mock()
    monkeypatch.setattr(fetcher, "_save_cache", save_cache)

    assert fetcher.fetch_game_wp("missing-game") is None
    assert fetcher.fetch_week_games(week=1) == {}
    save_cache.assert_not_called()
