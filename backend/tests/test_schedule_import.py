from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import threading
import time
from types import SimpleNamespace

import pytest

from backend.nospoil_nfl.api.calendar import SeasonCalendar
from backend.nospoil_nfl.game import (
    Game,
    GameId,
    GameState,
    GameStatus,
    RecordScope,
    RecordSnapshot,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamRecord,
    WriteResult,
)
from backend.nospoil_nfl.providers import ScheduleGame, ScheduleTeam, ScoreboardBatch
from backend.nospoil_nfl.sync.import_schedule import main
from backend.nospoil_nfl.sync.service import (
    ImportResult,
    SCHEDULE_FETCH_WORKERS,
    ScheduleSyncService,
)


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
HISTORICAL_WEEK = SeasonWeek(2025, SeasonPhase.REGULAR_SEASON, 1)
CURRENT_WEEK = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1)
FUTURE_WEEK = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 2)


class MemoryRepository:
    def __init__(self) -> None:
        self.games: dict[GameId, Game] = {}
        self.create_attempts: list[GameId] = []

    def create_if_absent(self, game: Game) -> bool:
        self.create_attempts.append(game.game_id)
        if game.game_id in self.games:
            return False
        self.games[game.game_id] = game
        return True

    def get(self, game_id: GameId) -> Game | None:
        return self.games.get(game_id)

    def list_week(self, season_week: SeasonWeek) -> list[Game]:
        return [game for game in self.games.values() if game.season_week == season_week]

    def list_season(self, season: int) -> list[Game]:
        return [
            game for game in self.games.values() if game.season_week.season == season
        ]

    def apply_schedule(self, current: Game, update: object) -> WriteResult:
        stored = self.games[current.game_id]
        if stored != current or update.observed_at <= stored.schedule_checked_at:
            return WriteResult.STALE
        self.games[current.game_id] = replace(
            stored,
            schedule_checked_at=update.observed_at,
        )
        return WriteResult.APPLIED


class ExactProvider:
    def __init__(
        self,
        batches: dict[SeasonWeek | None, ScoreboardBatch],
    ) -> None:
        self.batches = batches
        self.calls: list[SeasonWeek | None] = []

    def fetch_scoreboard(
        self,
        season_week: SeasonWeek | None = None,
    ) -> ScoreboardBatch:
        self.calls.append(season_week)
        return self.batches[season_week]


def _record(wins: int, losses: int) -> RecordSnapshot:
    return RecordSnapshot(
        TeamRecord(wins, losses),
        RecordScope.REGULAR_SEASON,
        NOW,
    )


def _game(
    game_id: str,
    week: SeasonWeek,
    *,
    status: GameStatus,
    home_id: str = "home",
    away_id: str = "away",
    home_record: RecordSnapshot | None = None,
    away_record: RecordSnapshot | None = None,
) -> ScheduleGame:
    return ScheduleGame(
        game_id=GameId(game_id),
        kickoff_at=NOW + timedelta(days=1),
        home=ScheduleTeam(
            home_id, f"Team {home_id}", home_id.upper(), record=home_record
        ),
        away=ScheduleTeam(
            away_id, f"Team {away_id}", away_id.upper(), record=away_record
        ),
        status=status,
    )


def _batch(
    week: SeasonWeek,
    *games: ScheduleGame,
    known_weeks: tuple[SeasonWeek, ...] = (),
) -> ScoreboardBatch:
    return ScoreboardBatch(NOW, week, tuple(games), known_weeks)


def test_unknown_season_fails_before_aws_or_source(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "nospoil-staging-games")
    accesses: list[str] = []

    def forbidden(*args, **kwargs):
        accesses.append("called")
        raise AssertionError("AWS or source construction must not run")

    assert (
        main(
            ["--season", "1900"],
            boto_resource=forbidden,
            scoreboard_factory=forbidden,
        )
        == 1
    )
    assert accesses == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "UnknownSeasonError"


def test_production_table_fails_before_aws_or_source(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "nospoil-production-games")
    called: list[str] = []

    def forbidden(*args, **kwargs):
        called.append("called")
        raise AssertionError("production import must not construct AWS/source")

    assert (
        main(
            ["--season", "2020"],
            boto_resource=forbidden,
            scoreboard_factory=forbidden,
        )
        == 1
    )
    assert called == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "RuntimeError"
    assert "nospoil-staging-games" in payload["message"]


def test_historical_import_requests_every_exact_catalogue_week_with_bounded_workers() -> (
    None
):
    calendar = SeasonCalendar()
    weeks = tuple(week for week in calendar.known_weeks if week.season == 2020)

    class TrackingProvider:
        def __init__(self) -> None:
            self.calls: list[SeasonWeek] = []
            self.active = 0
            self.maximum_active = 0
            self.lock = threading.Lock()

        def fetch_scoreboard(self, week: SeasonWeek) -> ScoreboardBatch:
            with self.lock:
                self.calls.append(week)
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
            time.sleep(0.005)
            with self.lock:
                self.active -= 1
            return _batch(week)

    provider = TrackingProvider()
    result = ScheduleSyncService(MemoryRepository(), provider).import_weeks(
        2020,
        weeks,
        now=NOW,
        active_season=2026,
    )

    assert len(weeks) == 27
    assert set(provider.calls) == set(weeks)
    assert provider.maximum_active <= SCHEDULE_FETCH_WORKERS
    assert provider.maximum_active > 1
    assert result.verified_weeks == weeks
    assert result.empty_weeks == weeks
    assert result.source_calls == 27
    assert result.persistence_writes == 0


def test_mismatched_envelope_fails_before_any_persistence() -> None:
    second = SeasonWeek(2025, SeasonPhase.REGULAR_SEASON, 2)
    wrong = SeasonWeek(2025, SeasonPhase.REGULAR_SEASON, 3)
    observation = _game(
        "one",
        HISTORICAL_WEEK,
        status=GameStatus(GameState.SCHEDULED),
    )
    provider = ExactProvider(
        {
            HISTORICAL_WEEK: _batch(HISTORICAL_WEEK, observation),
            second: _batch(wrong),
        }
    )
    repository = MemoryRepository()

    with pytest.raises(ValueError, match="envelope"):
        ScheduleSyncService(repository, provider).import_weeks(
            2025,
            (HISTORICAL_WEEK, second),
            now=NOW,
            active_season=2026,
        )

    assert repository.create_attempts == []
    assert repository.games == {}


def test_active_import_excludes_incomplete_current_week_results_from_future_records() -> (
    None
):
    completed = _game(
        "completed",
        CURRENT_WEEK,
        status=GameStatus(GameState.FINAL, score=Score(21, 17)),
        home_id="shared",
        away_id="old-away",
    )
    unfinished = _game(
        "unfinished",
        CURRENT_WEEK,
        status=GameStatus(GameState.SCHEDULED),
        home_id="other-home",
        away_id="other-away",
    )
    future = _game(
        "future",
        FUTURE_WEEK,
        status=GameStatus(GameState.SCHEDULED),
        home_id="shared",
        away_id="future-away",
        home_record=_record(1, 0),
        away_record=_record(0, 1),
    )
    provider = ExactProvider(
        {
            None: _batch(
                CURRENT_WEEK,
                completed,
                unfinished,
                known_weeks=(CURRENT_WEEK, FUTURE_WEEK),
            ),
            FUTURE_WEEK: _batch(FUTURE_WEEK, future),
        }
    )
    repository = MemoryRepository()

    result = ScheduleSyncService(repository, provider).import_weeks(
        2026,
        (FUTURE_WEEK,),
        now=NOW,
        active_season=2026,
    )

    saved = repository.games[GameId("future")]
    assert provider.calls == [None, FUTURE_WEEK]
    assert result.source_calls == 2
    assert saved.home.pregame_record is not None
    assert saved.home.pregame_record.record == TeamRecord(0, 0)
    assert saved.away.pregame_record is not None
    assert saved.away.pregame_record.record == TeamRecord(0, 1)


def test_idempotent_rerun_reports_stale_write_and_durable_supported_final_id() -> None:
    final = _game(
        "final",
        HISTORICAL_WEEK,
        status=GameStatus(GameState.FINAL, score=Score(24, 20)),
        home_record=_record(1, 0),
        away_record=_record(0, 1),
    )
    provider = ExactProvider({HISTORICAL_WEEK: _batch(HISTORICAL_WEEK, final)})
    repository = MemoryRepository()
    service = ScheduleSyncService(repository, provider)

    first = service.import_weeks(
        2025,
        (HISTORICAL_WEEK,),
        now=NOW,
        active_season=2026,
    )
    saved = repository.games[GameId("final")]
    second = service.import_weeks(
        2025,
        (HISTORICAL_WEEK,),
        now=NOW,
        active_season=2026,
    )

    assert first.games_created == 1
    assert first.supported_final_game_ids == (GameId("final"),)
    assert second.games_created == 0
    assert second.stale_writes == 1
    assert second.supported_final_game_ids == (GameId("final"),)
    assert repository.games[GameId("final")] == saved


def test_source_final_is_not_handed_off_when_durable_write_is_stale() -> None:
    final = _game(
        "not-durable-final",
        HISTORICAL_WEEK,
        status=GameStatus(GameState.FINAL, score=Score(24, 20)),
    )
    repository = MemoryRepository()
    seeded = ScheduleSyncService(
        repository,
        ExactProvider(
            {
                HISTORICAL_WEEK: _batch(
                    HISTORICAL_WEEK,
                    _game(
                        "not-durable-final",
                        HISTORICAL_WEEK,
                        status=GameStatus(GameState.SCHEDULED),
                    ),
                )
            }
        ),
    )
    seeded.import_weeks(
        2025,
        (HISTORICAL_WEEK,),
        now=NOW,
        active_season=2026,
    )
    newer = repository.games[GameId("not-durable-final")]
    repository.games[newer.game_id] = replace(
        newer,
        schedule_checked_at=NOW + timedelta(minutes=1),
    )

    result = ScheduleSyncService(
        repository,
        ExactProvider({HISTORICAL_WEEK: _batch(HISTORICAL_WEEK, final)}),
    ).import_weeks(
        2025,
        (HISTORICAL_WEEK,),
        now=NOW,
        active_season=2026,
    )

    assert result.stale_writes == 1
    assert result.supported_final_game_ids == ()


def test_cli_uses_bounded_clients_and_publishes_exact_compact_outputs(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "nospoil-staging-games")
    monkeypatch.setenv("NOSPOIL_ESPN_TIMEOUT_SECONDS", "")
    output_path = tmp_path / "output"
    summary_path = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    resource_calls: list[tuple[str, object]] = []
    provider_timeouts: list[float] = []

    class Calendar:
        active_season = 2026
        known_weeks = (HISTORICAL_WEEK,)

        @staticmethod
        def validate_season(season: int) -> int:
            assert season == 2025
            return season

    class Resource:
        @staticmethod
        def Table(name: str) -> object:
            assert name == "nospoil-staging-games"
            return object()

    def boto_resource(name: str, *, config: object) -> Resource:
        resource_calls.append((name, config))
        return Resource()

    def scoreboard_factory(*, timeout_seconds: float) -> object:
        provider_timeouts.append(timeout_seconds)
        return object()

    expected = ImportResult(
        season=2025,
        requested_weeks=(HISTORICAL_WEEK,),
        verified_weeks=(HISTORICAL_WEEK,),
        empty_weeks=(),
        scoreboard_requests=1,
        games_created=1,
        games_updated=0,
        stale_writes=0,
        rejected_transitions=0,
        supported_final_game_ids=(GameId("one"), GameId("two")),
    )

    class Service:
        def import_weeks(self, season, weeks, *, now, active_season):
            assert (season, weeks, now, active_season) == (
                2025,
                (HISTORICAL_WEEK,),
                NOW,
                2026,
            )
            return expected

    assert (
        main(
            ["--season", "2025"],
            calendar=Calendar(),
            boto_resource=boto_resource,
            scoreboard_factory=scoreboard_factory,
            repository_factory=lambda table, **kwargs: table,
            service_factory=lambda repository, scoreboard: Service(),
            clock=lambda: NOW,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["supported_final_game_ids"] == ["one", "two"]
    assert output_path.read_text().splitlines() == [
        "result_json=" + json.dumps(payload, sort_keys=True, separators=(",", ":")),
        'supported_final_game_ids=["one","two"]',
    ]
    assert "Verified weeks: `1`" in summary_path.read_text()
    assert provider_timeouts == [8.0]
    assert resource_calls[0][0] == "dynamodb"
    config = resource_calls[0][1]
    assert config.connect_timeout == 2
    assert config.read_timeout == 5
    assert config.retries == {"mode": "standard", "total_max_attempts": 2}


def test_cli_rejected_transition_is_attention_and_does_not_publish_ids(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "nospoil-staging-games")
    output_path = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    class Calendar:
        active_season = 2026
        known_weeks = (HISTORICAL_WEEK,)

        @staticmethod
        def validate_season(season: int) -> int:
            return season

    attention = ImportResult(
        season=2025,
        requested_weeks=(HISTORICAL_WEEK,),
        verified_weeks=(HISTORICAL_WEEK,),
        empty_weeks=(),
        scoreboard_requests=1,
        games_created=0,
        games_updated=0,
        stale_writes=0,
        rejected_transitions=1,
        supported_final_game_ids=(),
    )

    assert (
        main(
            ["--season", "2025"],
            calendar=Calendar(),
            boto_resource=lambda *args, **kwargs: SimpleNamespace(
                Table=lambda name: object()
            ),
            scoreboard_factory=lambda **kwargs: object(),
            repository_factory=lambda table, **kwargs: table,
            service_factory=lambda *args: SimpleNamespace(
                import_weeks=lambda *args, **kwargs: attention
            ),
            clock=lambda: NOW,
        )
        == 1
    )
    assert not output_path.exists()
    assert json.loads(capsys.readouterr().out)["error"] == "rejected_transitions"
