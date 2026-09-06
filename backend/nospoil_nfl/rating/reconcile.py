"""Command-line entry point for scheduled nflverse reconciliation."""

from __future__ import annotations

from argparse import ArgumentParser, ArgumentTypeError, Namespace
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json
import math
import os
from typing import Callable, Sequence

from ..nflverse import load_nflverse_season
from ..providers import NflversePlayClient, NflverseScheduleClient
from ..providers.errors import ProviderError
from .reconciliation import NflverseReconciliationService, ReconciliationResult


DEFAULT_NFLVERSE_TIMEOUT_SECONDS = 20.0
MAX_NFLVERSE_TIMEOUT_SECONDS = 60.0


class _ConfigurationError(RuntimeError):
    """Raised for missing or invalid operator configuration."""


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    season_loader: Callable[..., object] = load_nflverse_season,
    service_factory: Callable[..., NflverseReconciliationService] = (
        NflverseReconciliationService
    ),
) -> int:
    """Run one reconciliation mode and return a process exit code."""
    args = _parse_args(argv)
    now = (clock or (lambda: datetime.now(UTC)))()
    try:
        _require_utc(now)
        seasons = _seasons_to_run(args.mode, args.season, now)
        report_season = args.season or _active_season(now)
        timeout = _bounded_positive_float_environment(
            "NOSPOIL_NFLVERSE_TIMEOUT_SECONDS",
            DEFAULT_NFLVERSE_TIMEOUT_SECONDS,
            maximum=MAX_NFLVERSE_TIMEOUT_SECONDS,
        )
        schedule_provider = NflverseScheduleClient(timeout_seconds=timeout)
        play_provider = NflversePlayClient(timeout_seconds=timeout)

        if args.mode == "verify":
            season_loader(
                report_season,
                schedule_provider=schedule_provider,
                play_provider=play_provider,
            )
            payload: dict[str, object] = {
                "mode": "verify",
                "season": report_season,
                "ok": True,
                "verified": True,
            }
            _publish(payload)
            return 0

        table_name = _required_environment("NOSPOIL_GAMES_TABLE")
        index_name = os.environ.get("NOSPOIL_SCHEDULE_INDEX") or "season-schedule-index"
        import boto3

        from ..game.dynamodb_repository import DynamoGameRepository

        repository = DynamoGameRepository(
            boto3.resource("dynamodb").Table(table_name),
            index_name=index_name,
        )
        service = service_factory(
            repository,
            schedule_provider,
            play_provider,
        )
        results = [
            service.run(
                season,
                now=now,
                mode=args.mode,
                game_id=args.game_id,
            )
            for season in seasons
        ]
        result = _combine_results(results)
        payload = _result_payload(
            result,
            mode=args.mode,
            season=report_season,
            seasons=seasons,
        )
        _publish(payload, attention=_result_attention(result))
        return _exit_code(result)
    except ProviderError:
        payload = {
            "mode": args.mode,
            "season": report_season,
            "ok": False,
            "error": "source_failure",
        }
        _publish(payload, attention="source failure")
        return 1
    except Exception as error:
        payload = {
            "mode": args.mode,
            "season": report_season,
            "ok": False,
            "error": _safe_error(error),
        }
        _publish(payload, attention="execution failure")
        return 1


def _parse_args(argv: Sequence[str] | None) -> Namespace:
    parser = ArgumentParser(description="Reconcile nflverse ratings safely.")
    parser.add_argument(
        "--mode",
        choices=("due", "correction", "verify"),
        default="due",
    )
    parser.add_argument("--season", type=_positive_int)
    parser.add_argument("--game-id")
    args = parser.parse_args(argv)
    if args.game_id is not None and args.mode != "correction":
        parser.error("--game-id is allowed only with --mode correction")
    if args.game_id is not None and not args.game_id.strip():
        parser.error("--game-id must be non-empty text")
    return args


def _result_payload(
    result: ReconciliationResult,
    *,
    mode: str,
    season: int,
    seasons: tuple[int, ...] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": mode,
        "season": season,
        "ok": _result_attention(result) is None,
        **asdict(result),
    }
    if seasons is not None and len(seasons) > 1:
        payload["seasons"] = list(seasons)
    return payload


def _combine_results(results: Sequence[ReconciliationResult]) -> ReconciliationResult:
    return ReconciliationResult(
        downloads=sum(result.downloads for result in results),
        selected=sum(result.selected for result in results),
        mappings=sum(result.mappings for result in results),
        confirmed_updates=sum(result.confirmed_updates for result in results),
        unchanged=sum(result.unchanged for result in results),
        retries=sum(result.retries for result in results),
        stale_writes=sum(result.stale_writes for result in results),
        failures=sum(result.failures for result in results),
        conflicts=sum(result.conflicts for result in results),
        overdue=sum(result.overdue for result in results),
        source_failure=any(result.source_failure for result in results),
        manual_correction_failure=any(
            result.manual_correction_failure for result in results
        ),
    )


def _exit_code(result: ReconciliationResult) -> int:
    return 1 if _result_attention(result) is not None else 0


def _result_attention(result: ReconciliationResult) -> str | None:
    if result.source_failure:
        return "source failure"
    if result.conflicts > 0:
        return "mapping conflict"
    if result.manual_correction_failure:
        return "manual correction failure"
    if result.overdue > 0:
        return "overdue reconciliation"
    return None


def _publish(payload: dict[str, object], *, attention: str | None = None) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if attention is not None:
        print(f"::error::nflverse reconciliation {attention}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path is None or not summary_path.strip():
        return
    lines = [
        "## NFLverse reconciliation",
        "",
        f"- Mode: `{payload.get('mode', 'unknown')}`",
        f"- Season: `{payload.get('season', 'unknown')}`",
        f"- Status: `{('ok' if payload.get('ok') else 'attention')}`",
    ]
    for key in (
        "selected",
        "confirmed_updates",
        "unchanged",
        "retries",
        "failures",
        "conflicts",
        "overdue",
    ):
        if key in payload:
            lines.append(f"- {key.replace('_', ' ').title()}: `{payload[key]}`")
    if attention is not None:
        lines.extend(("", f"Attention: {attention}."))
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def _active_season(now: datetime) -> int:
    return now.year - 1 if now.month in {1, 2} else now.year


def _seasons_to_run(
    mode: str,
    explicit_season: int | None,
    now: datetime,
) -> tuple[int, ...]:
    if explicit_season is not None:
        return (explicit_season,)
    if mode == "due":
        return (now.year, now.year - 1)
    return (_active_season(now),)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise ArgumentTypeError("must be a positive integer")
    return parsed


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise _ConfigurationError(f"{name} is not configured")
    return value.strip()


def _bounded_positive_float_environment(
    name: str,
    default: float,
    *,
    maximum: float,
) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise _ConfigurationError(f"{name} must be a positive number") from error
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise _ConfigurationError(f"{name} must be between 0 and {maximum:g} seconds")
    return value


def _require_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("clock must return a timezone-aware UTC datetime")


def _safe_error(error: Exception) -> str:
    if isinstance(error, _ConfigurationError):
        return "configuration_error"
    return "execution_failed"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
