from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
import json
import logging
import sys
from types import SimpleNamespace

from backend.nospoil_nfl.nflverse import NflverseSeason
from backend.nospoil_nfl.rating.reconciliation import NflverseReconciliationService
from backend.nospoil_nfl.rating.reconcile import main
from backend.nospoil_nfl.rating.reconciliation import ReconciliationResult
from backend.tests.test_reconciliation import FakeRepository, make_game, source_for


NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


class _Provider:
    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds


def test_verify_loads_one_season_without_dynamodb_or_table_configuration(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.delenv("NOSPOIL_GAMES_TABLE", raising=False)
    monkeypatch.setenv("NOSPOIL_NFLVERSE_TIMEOUT_SECONDS", "")
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setattr(
        "backend.nospoil_nfl.rating.reconcile.NflverseScheduleClient",
        _Provider,
    )
    monkeypatch.setattr(
        "backend.nospoil_nfl.rating.reconcile.NflversePlayClient",
        _Provider,
    )
    calls: list[tuple[int, object, object]] = []

    def load_season(season: int, *, schedule_provider, play_provider) -> object:
        calls.append((season, schedule_provider, play_provider))
        return object()

    assert (
        main(
            ["--mode", "verify", "--season", "2026"],
            clock=lambda: NOW,
            season_loader=load_season,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert payload == {"mode": "verify", "season": 2026, "ok": True, "verified": True}
    assert len(calls) == 1
    summary = summary_path.read_text(encoding="utf-8")
    assert "NFLverse reconciliation" in summary
    assert "Status: `ok`" in summary


def test_nan_timeout_is_rejected_as_configuration_error(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_NFLVERSE_TIMEOUT_SECONDS", "nan")

    assert main(["--mode", "verify", "--season", "2026"], clock=lambda: NOW) == 1

    output = capsys.readouterr().out
    assert '"error":"configuration_error"' in output
    assert "nan" not in output


def test_attention_result_returns_failure_annotation_and_nonzero_exit(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            assert name == "dynamodb"
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())

    def build_service(repository, schedule_provider, play_provider):
        return SimpleNamespace(
            run=lambda season, now, mode, game_id: ReconciliationResult(overdue=1)
        )

    exit_code = main(
        ["--mode", "due", "--season", "2026"],
        clock=lambda: NOW,
        service_factory=build_service,
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "::error::nflverse reconciliation overdue reconciliation" in output
    assert '"ok":false' in output
    assert '"overdue":1' in output


def test_routine_due_retry_is_success_without_attention_conditions(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())

    def build_service(repository, schedule_provider, play_provider):
        return SimpleNamespace(
            run=lambda season, now, mode, game_id: ReconciliationResult(
                failures=1,
                retries=1,
            )
        )

    assert (
        main(
            ["--mode", "due", "--season", "2026"],
            clock=lambda: NOW,
            service_factory=build_service,
        )
        == 0
    )

    output = capsys.readouterr().out
    assert '"ok":true' in output
    assert "::error::" not in output


def test_scheduled_due_run_uses_real_service_for_current_and_prior_seasons(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    current_time = datetime(2026, 3, 1, 12, tzinfo=UTC)
    current = make_game("current", final_at=current_time - timedelta(hours=7), season=2026)
    prior = make_game("prior", final_at=current_time - timedelta(hours=7), season=2025)
    repository = FakeRepository((current, prior))
    current_schedule, current_plays = source_for((current,))
    prior_schedule, prior_plays = source_for((prior,))
    snapshots = {
        2026: NflverseSeason(current_schedule.schedule, current_plays.plays),
        2025: NflverseSeason(prior_schedule.schedule, prior_plays.plays),
    }
    loaded: list[int] = []

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    monkeypatch.setattr(
        "backend.nospoil_nfl.game.dynamodb_repository.DynamoGameRepository",
        lambda table, *, index_name: repository,
    )

    def load_season(season: int, **providers: object) -> NflverseSeason:
        loaded.append(season)
        return snapshots[season]

    def build_service(repository, schedule_provider, play_provider):
        return NflverseReconciliationService(
            repository,
            schedule_provider,
            play_provider,
            calculator=lambda rating_input: 8.0,
            season_loader=load_season,
        )

    assert main(
        ["--mode", "due"],
        clock=lambda: current_time,
        service_factory=build_service,
    ) == 0

    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert loaded == [2026, 2025]
    assert payload["seasons"] == [2026, 2025]
    assert payload["selected"] == 2
    assert payload["downloads"] == 2


def test_explicit_season_limits_due_run(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    calls: list[int] = []

    def build_service(repository, schedule_provider, play_provider):
        def run(season, *, now, mode, game_id):
            calls.append(season)
            return ReconciliationResult()

        return SimpleNamespace(run=run)

    assert (
        main(
            ["--mode", "due", "--season", "2025"],
            clock=lambda: NOW,
            service_factory=build_service,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert calls == [2025]
    assert payload["season"] == 2025
    assert "seasons" not in payload


def test_repeated_game_ids_use_one_bounded_season_reconciliation(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")
    resource_kwargs: list[dict[str, object]] = []

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            assert name == "dynamodb"
            resource_kwargs.append(kwargs)
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    calls: list[tuple[int, tuple[str, ...]]] = []

    def build_service(repository, schedule_provider, play_provider):
        def run(season, *, now, mode, game_ids):
            assert mode == "correction"
            calls.append((season, tuple(game_ids)))
            return ReconciliationResult(selected=2, downloads=1)

        return SimpleNamespace(run=run)

    assert (
        main(
            [
                "--mode",
                "correction",
                "--season",
                "2020",
                "--game-id",
                "one",
                "--game-id",
                "two",
            ],
            clock=lambda: NOW,
            service_factory=build_service,
        )
        == 0
    )

    assert calls == [(2020, ("one", "two"))]
    config = resource_kwargs[0]["config"]
    assert config.connect_timeout == 2
    assert config.read_timeout == 5
    assert config.retries == {"mode": "standard", "total_max_attempts": 2}
    assert json.loads(capsys.readouterr().out.splitlines()[0])["downloads"] == 1


def test_real_service_events_are_info_stderr_with_warning_root_handler(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    game = make_game("logged")
    repository = FakeRepository((game,))
    schedule, plays = source_for((game,))
    source = NflverseSeason(schedule.schedule, plays.plays)
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    monkeypatch.setattr(
        "backend.nospoil_nfl.game.dynamodb_repository.DynamoGameRepository",
        lambda table, *, index_name: repository,
    )
    monkeypatch.setattr(
        "backend.nospoil_nfl.rating.reconcile.NflverseScheduleClient", _Provider
    )
    monkeypatch.setattr(
        "backend.nospoil_nfl.rating.reconcile.NflversePlayClient", _Provider
    )

    def build_service(repository, schedule_provider, play_provider):
        return NflverseReconciliationService(
            repository,
            schedule_provider,
            play_provider,
            calculator=lambda rating_input: 8.0,
            season_loader=lambda season, **providers: source,
        )

    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_handler = logging.StreamHandler(io.StringIO())
    root_handler.setLevel(logging.WARNING)
    root_logger.addHandler(root_handler)
    root_logger.setLevel(logging.WARNING)
    try:
        assert main(
            ["--mode", "due", "--season", "2026"],
            clock=lambda: NOW,
            service_factory=build_service,
        ) == 0
        captured = capsys.readouterr()
    finally:
        root_logger.removeHandler(root_handler)
        root_logger.setLevel(previous_level)

    payload = json.loads(captured.out.splitlines()[0])
    assert payload["confirmed_updates"] == 1
    assert '"event":"nflverse_rating_confirmed"' in captured.err
    assert "24" not in captured.err and "17" not in captured.err


def test_repeated_main_calls_keep_one_current_stderr_handler(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())

    def build_service(repository, schedule_provider, play_provider):
        class LoggingService:
            def run(self, season, *, now, mode, game_id):
                logging.getLogger(
                    "backend.nospoil_nfl.rating.reconciliation"
                ).info('{"event":"repeat_probe"}')
                return ReconciliationResult()

        return LoggingService()

    for _ in range(2):
        assert main(
            ["--mode", "due", "--season", "2026"],
            clock=lambda: NOW,
            service_factory=build_service,
        ) == 0
        captured = capsys.readouterr()
        assert captured.err.count('{"event":"repeat_probe"}') == 1


def test_unexpected_cli_failure_is_structured_and_score_free(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str, **kwargs: object) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())

    def build_service(repository, schedule_provider, play_provider):
        def fail(*args, **kwargs):
            raise RuntimeError("diagnostic boom")

        return SimpleNamespace(run=fail)

    assert main(
        ["--mode", "due", "--season", "2026"],
        clock=lambda: NOW,
        service_factory=build_service,
    ) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out.splitlines()[0])
    event = json.loads(captured.err.splitlines()[0])
    assert payload["error"] == "execution_failed"
    assert event == {
        "error_code": "execution_failed",
        "event": "nflverse_cli_failed",
        "exception_type": "RuntimeError",
        "message": "diagnostic boom",
        "operation": None,
        "provider": None,
    }
    assert "Traceback" in captured.err
    assert "score" not in event
