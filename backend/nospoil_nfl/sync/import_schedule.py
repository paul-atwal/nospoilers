"""Operator entry point for one reviewed, catalogued staging season import."""

from __future__ import annotations

from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
import json
import math
import os

from ..api.calendar import SeasonCalendar
from ..game.repository import GameRepository
from ..providers import ESPNScoreboardProvider, EspnScoreboardClient
from .service import ImportResult, ScheduleSyncService


DEFAULT_ESPN_TIMEOUT_SECONDS = 8.0
MAX_ESPN_TIMEOUT_SECONDS = 8.0


def main(
    argv: Sequence[str] | None = None,
    *,
    calendar: SeasonCalendar | None = None,
    boto_resource: Callable[..., object] | None = None,
    scoreboard_factory: Callable[..., ESPNScoreboardProvider] = EspnScoreboardClient,
    repository_factory: Callable[..., GameRepository] | None = None,
    service_factory: Callable[..., ScheduleSyncService] = ScheduleSyncService,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Import one catalogued season and print a compact operational result."""
    args = _parse_args(argv)
    try:
        selected_calendar = calendar or SeasonCalendar()
        season = selected_calendar.validate_season(args.season)
        weeks = tuple(
            week for week in selected_calendar.known_weeks if week.season == season
        )
        table_name = _required_environment("NOSPOIL_GAMES_TABLE")
        index_name = os.environ.get("NOSPOIL_SCHEDULE_INDEX") or "season-schedule-index"
        timeout = _bounded_timeout()
        repository = _build_repository(
            table_name,
            index_name,
            boto_resource=boto_resource,
            repository_factory=repository_factory,
        )
        result = service_factory(
            repository,
            scoreboard_factory(timeout_seconds=timeout),
        ).import_weeks(
            season,
            weeks,
            now=clock(),
            active_season=selected_calendar.active_season,
        )
        payload = _result_payload(result)
        if result.rejected_transitions:
            payload["ok"] = False
            payload["error"] = "rejected_transitions"
            payload["message"] = "source lifecycle transitions were rejected"
        _publish(payload)
        return 0 if payload["ok"] else 1
    except Exception as error:
        _publish(
            {
                "ok": False,
                "error": type(error).__name__,
                "message": str(error) or "staging import failed",
            }
        )
        return 1


def _parse_args(argv: Sequence[str] | None) -> Namespace:
    parser = ArgumentParser(description="Import one reviewed staging season.")
    parser.add_argument("--season", required=True, type=_positive_int)
    return parser.parse_args(argv)


def _build_repository(
    table_name: str,
    index_name: str,
    *,
    boto_resource: Callable[..., object] | None,
    repository_factory: Callable[..., GameRepository] | None,
) -> GameRepository:
    if boto_resource is None:
        import boto3

        boto_resource = boto3.resource
    if repository_factory is None:
        from ..game.dynamodb_repository import DynamoGameRepository

        repository_factory = DynamoGameRepository
    resource = boto_resource("dynamodb", config=_dynamodb_config())
    table = resource.Table(table_name)  # type: ignore[attr-defined]
    return repository_factory(table, index_name=index_name)


def _dynamodb_config():
    from botocore.config import Config

    return Config(
        connect_timeout=2,
        read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    )


def _bounded_timeout() -> float:
    raw = os.environ.get("NOSPOIL_ESPN_TIMEOUT_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_ESPN_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError as error:
        raise RuntimeError(
            "NOSPOIL_ESPN_TIMEOUT_SECONDS must be a positive number"
        ) from error
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_ESPN_TIMEOUT_SECONDS:
        raise RuntimeError(
            "NOSPOIL_ESPN_TIMEOUT_SECONDS must be between 0 and 8 seconds"
        )
    return timeout


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} must be configured")
    return value.strip()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise ArgumentTypeError("must be a positive integer")
    return parsed


def _result_payload(result: ImportResult) -> dict[str, object]:
    return {
        "ok": True,
        "season": result.season,
        "requested_weeks": len(result.requested_weeks),
        "verified_weeks": len(result.verified_weeks),
        "empty_weeks": len(result.empty_weeks),
        "source_calls": result.source_calls,
        "persistence_writes": result.persistence_writes,
        "games_created": result.games_created,
        "games_updated": result.games_updated,
        "stale_writes": result.stale_writes,
        "rejected_transitions": result.rejected_transitions,
        "supported_final_game_ids": [
            str(value) for value in result.supported_final_game_ids
        ],
    }


def _publish(payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    print(encoded)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if payload["ok"] and output_path is not None and output_path.strip():
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"result_json={encoded}\n")
            output.write(
                "supported_final_game_ids="
                + json.dumps(
                    payload["supported_final_game_ids"],
                    separators=(",", ":"),
                )
                + "\n"
            )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path is None or not summary_path.strip():
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("## Staging schedule import\n\n")
        summary.write(f"- Status: `{('ok' if payload['ok'] else 'attention')}`\n")
        if payload["ok"]:
            summary.write(f"- Season: `{payload['season']}`\n")
            summary.write(f"- Verified weeks: `{payload['verified_weeks']}`\n")
            summary.write(f"- Empty weeks: `{payload['empty_weeks']}`\n")
            summary.write(f"- Source calls: `{payload['source_calls']}`\n")
            summary.write(f"- Persistence writes: `{payload['persistence_writes']}`\n")
            summary.write(
                f"- Rating candidates: `{len(payload['supported_final_game_ids'])}`\n"
            )
        else:
            safe_message = " ".join(str(payload.get("message", "failed")).split())
            summary.write(f"- Error: `{safe_message}`\n")


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())


__all__ = ["main"]
