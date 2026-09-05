from __future__ import annotations

from datetime import UTC, datetime
import json
import sys
from types import SimpleNamespace

from backend.nospoil_nfl.rating.reconcile import main
from backend.nospoil_nfl.rating.reconciliation import ReconciliationResult


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
        def resource(self, name: str) -> object:
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
        def resource(self, name: str) -> object:
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


def test_scheduled_due_run_checks_current_and_prior_seasons(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    calls: list[int] = []

    def build_service(repository, schedule_provider, play_provider):
        def run(season, *, now, mode, game_id):
            calls.append(season)
            if season == 2025:
                return ReconciliationResult(selected=1, downloads=1)
            return ReconciliationResult()

        return SimpleNamespace(run=run)

    assert main(["--mode", "due"], clock=lambda: datetime(2026, 3, 1, tzinfo=UTC), service_factory=build_service) == 0

    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert calls == [2026, 2025]
    assert payload["seasons"] == [2026, 2025]
    assert payload["selected"] == 1
    assert payload["downloads"] == 1


def test_scheduled_due_run_loads_each_due_season_once(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str) -> object:
            return SimpleNamespace(Table=lambda table_name: object())

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    calls: list[int] = []

    def build_service(repository, schedule_provider, play_provider):
        def run(season, *, now, mode, game_id):
            calls.append(season)
            return ReconciliationResult(selected=1, downloads=1)

        return SimpleNamespace(run=run)

    assert main(["--mode", "due"], clock=lambda: NOW, service_factory=build_service) == 0

    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert calls == [2026, 2025]
    assert payload["downloads"] == 2


def test_explicit_season_limits_due_run(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "games")

    class FakeBoto3:
        def resource(self, name: str) -> object:
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
