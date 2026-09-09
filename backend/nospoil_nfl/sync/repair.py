"""Operator-only repair of one stale ESPN final score or one source week.

This module is deliberately separate from :mod:`nospoil_nfl.api`.  It is
invoked by the manual GitHub workflow and never exposed through the read
Lambda's public Function URL.
"""

from __future__ import annotations

import json
import math
import os
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..game.models import Game, GameId, GameState, SeasonPhase, SeasonWeek
from ..game.repository import GameRepository
from ..game.updates import UNSET, ScheduleUpdate, TeamScheduleUpdate, WriteResult
from ..providers import EspnScoreboardClient, ScheduleGame, ScoreboardBatch
from ..providers.errors import ProviderError

DEFAULT_ESPN_TIMEOUT_SECONDS = 8.0
MAX_ESPN_TIMEOUT_SECONDS = 8.0


class RepairError(RuntimeError):
    """A repair was rejected before or during its conditional write."""


class RepairConflictError(RepairError):
    """A concurrent update won the conditional write race."""


@dataclass(frozen=True, slots=True)
class RepairScope:
    """One explicit operator-selected game or source week."""

    game_id: GameId | None = None
    season_week: SeasonWeek | None = None

    def __post_init__(self) -> None:
        if self.game_id is not None and (
            not isinstance(self.game_id, str) or not self.game_id.strip()
        ):
            raise ValueError("game_id must be non-empty text")
        if (self.game_id is None) == (self.season_week is None):
            raise ValueError("repair scope must be exactly one game or one week")


@dataclass(frozen=True, slots=True)
class RepairResult:
    """Bounded outcome suitable for a workflow summary."""

    scope: RepairScope
    selected: int
    repaired: int
    unchanged: int


class ScheduleRepairService:
    """Preflight all selected records, then repair only final score/status."""

    def __init__(
        self,
        repository: GameRepository,
        scoreboard: EspnScoreboardClient,
    ) -> None:
        self._repository = repository
        self._scoreboard = scoreboard

    def run(self, scope: RepairScope) -> RepairResult:
        """Fetch one source week and fail closed before the first mutation."""
        if not isinstance(scope, RepairScope):
            raise TypeError("scope must be a RepairScope")

        selected, requested_week = self._select_durable_games(scope)
        batch = self._scoreboard.fetch_scoreboard(requested_week)
        self._validate_source_batch(batch, requested_week, selected, scope)
        source_by_id = {game.game_id: game for game in batch.games}

        prepared: list[tuple[Game, ScheduleGame]] = []
        unchanged = 0
        for current in selected:
            source = source_by_id.get(current.game_id)
            if source is None:
                raise RepairError(
                    f"ESPN scoreboard is missing durable game {current.game_id}"
                )
            self._validate_game_identity(current, source)
            if current.status.state is not GameState.FINAL:
                raise RepairError(
                    f"durable game {current.game_id} is not final; repair is final-only"
                )
            if source.status.state is not GameState.FINAL:
                raise RepairError(
                    f"ESPN game {current.game_id} is not final; no score invented"
                )
            if current.status == source.status:
                unchanged += 1
                continue
            # Keep every field outside ESPN's final status owned by its
            # existing durable record.  In particular, records remain frozen.
            prepared.append((current, source))

        repaired = 0
        for current, source in prepared:
            result = self._repository.apply_schedule(
                current,
                ScheduleUpdate(
                    game_id=current.game_id,
                    observed_at=batch.observed_at,
                    season_week=current.season_week,
                    kickoff_at=current.kickoff_at,
                    home=TeamScheduleUpdate(
                        team_id=current.home.team_id,
                        display_name=current.home.display_name,
                        abbreviation=current.home.abbreviation,
                        logo_key=UNSET,
                        pregame_record=UNSET,
                        postgame_record=UNSET,
                    ),
                    away=TeamScheduleUpdate(
                        team_id=current.away.team_id,
                        display_name=current.away.display_name,
                        abbreviation=current.away.abbreviation,
                        logo_key=UNSET,
                        pregame_record=UNSET,
                        postgame_record=UNSET,
                    ),
                    status=source.status,
                    broadcaster=UNSET,
                    odds=UNSET,
                ),
            )
            if result is WriteResult.STALE:
                raise RepairConflictError(
                    f"conditional repair write lost race for {current.game_id}"
                )
            repaired += 1
        return RepairResult(scope, len(selected), repaired, unchanged)

    def _select_durable_games(
        self, scope: RepairScope
    ) -> tuple[list[Game], SeasonWeek]:
        if scope.game_id is not None:
            current = self._repository.get(scope.game_id)
            if current is None:
                raise RepairError(f"durable game {scope.game_id} was not found")
            return [current], current.season_week
        assert scope.season_week is not None
        games = self._repository.list_week(scope.season_week)
        if not games:
            raise RepairError(
                f"durable season week {scope.season_week.season}/"
                f"{scope.season_week.phase.value}/{scope.season_week.week} was not found"
            )
        return games, scope.season_week

    @staticmethod
    def _validate_source_batch(
        batch: ScoreboardBatch,
        requested_week: SeasonWeek,
        selected: list[Game],
        scope: RepairScope,
    ) -> None:
        if not isinstance(batch, ScoreboardBatch):
            raise RepairError("ESPN scoreboard result was not a ScoreboardBatch")
        if batch.season_week != requested_week:
            raise RepairError("ESPN scoreboard season/week does not match request")
        source_ids = [game.game_id for game in batch.games]
        if len(source_ids) != len(set(source_ids)):
            raise RepairError("ESPN scoreboard returned duplicate game IDs")
        durable_ids = {game.game_id for game in selected}
        if scope.season_week is not None and set(source_ids) != durable_ids:
            raise RepairError(
                "ESPN scoreboard game IDs do not exactly match durable week"
            )
        if scope.game_id is not None and scope.game_id not in set(source_ids):
            raise RepairError(f"ESPN scoreboard is missing game {scope.game_id}")

    @staticmethod
    def _validate_game_identity(current: Game, source: ScheduleGame) -> None:
        if source.game_id != current.game_id:
            raise RepairError(f"ESPN game ID mismatch for {current.game_id}")
        if (
            source.home.team_id != current.home.team_id
            or source.away.team_id != current.away.team_id
        ):
            raise RepairError(f"ESPN team identity mismatch for {current.game_id}")


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_factory: Callable[[str, str], GameRepository] | None = None,
    scoreboard_factory: Callable[[float], EspnScoreboardClient] = EspnScoreboardClient,
) -> int:
    """Run one explicit repair and print a compact machine-readable result."""
    try:
        args = _parse_args(argv)
        scope = _scope_from_args(args)
        table_name = _required_environment("NOSPOIL_GAMES_TABLE")
        index_name = os.environ.get("NOSPOIL_SCHEDULE_INDEX", "season-schedule-index")
        timeout = _bounded_timeout()
        repository = (
            repository_factory(table_name, index_name)
            if repository_factory is not None
            else _build_repository(table_name, index_name)
        )
        result = ScheduleRepairService(
            repository,
            scoreboard_factory(timeout),
        ).run(scope)
        payload = {
            "ok": True,
            "selected": result.selected,
            "repaired": result.repaired,
            "unchanged": result.unchanged,
            "scope": _scope_payload(scope),
        }
        _publish(payload)
        return 0
    except (RepairError, ProviderError, ValueError, RuntimeError) as error:
        _publish(
            {
                "ok": False,
                "error": type(error).__name__,
                "message": str(error) or "repair failed",
            }
        )
        return 1


def _parse_args(argv: Sequence[str] | None) -> Namespace:
    parser = ArgumentParser(description="Repair one stale final ESPN score safely.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--game-id")
    group.add_argument("--season", type=_positive_int)
    parser.add_argument("--phase", choices=[phase.value for phase in SeasonPhase])
    parser.add_argument("--week", type=_positive_int)
    args = parser.parse_args(argv)
    if args.game_id is not None:
        if not args.game_id.strip():
            parser.error("--game-id must be non-empty text")
        if args.phase is not None or args.week is not None:
            parser.error("--game-id cannot be combined with --phase or --week")
    elif args.phase is None or args.week is None:
        parser.error("--season requires --phase and --week")
    return args


def _scope_from_args(args: Namespace) -> RepairScope:
    if args.game_id is not None:
        return RepairScope(game_id=GameId(args.game_id.strip()))
    assert args.season is not None and args.phase is not None and args.week is not None
    return RepairScope(
        season_week=SeasonWeek(
            args.season,
            SeasonPhase(args.phase),
            args.week,
        )
    )


def _build_repository(table_name: str, index_name: str) -> GameRepository:
    import boto3
    from botocore.config import Config

    from ..game.dynamodb_repository import DynamoGameRepository

    table = boto3.resource(
        "dynamodb",
        config=Config(
            connect_timeout=2,
            read_timeout=5,
            retries={"mode": "standard", "total_max_attempts": 2},
        ),
    ).Table(table_name)
    return DynamoGameRepository(table, index_name=index_name)


def _bounded_timeout() -> float:
    raw = os.environ.get("NOSPOIL_ESPN_TIMEOUT_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_ESPN_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError("NOSPOIL_ESPN_TIMEOUT_SECONDS must be positive") from error
    if not math.isfinite(value) or value <= 0 or value > MAX_ESPN_TIMEOUT_SECONDS:
        raise RuntimeError(
            f"NOSPOIL_ESPN_TIMEOUT_SECONDS must be no greater than {MAX_ESPN_TIMEOUT_SECONDS:g}"
        )
    return value


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


def _scope_payload(scope: RepairScope) -> dict[str, object]:
    if scope.game_id is not None:
        return {"game_id": str(scope.game_id)}
    assert scope.season_week is not None
    return {
        "season": scope.season_week.season,
        "phase": scope.season_week.phase.value,
        "week": scope.season_week.week,
    }


def _publish(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path is None or not summary_path.strip():
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("## ESPN schedule repair\n\n")
        summary.write(f"- Status: `{('ok' if payload['ok'] else 'attention')}`\n")
        summary.write(
            f"- Scope: `{json.dumps(payload.get('scope', {}), sort_keys=True)}`\n"
        )
        if payload["ok"]:
            summary.write(f"- Repaired: `{payload['repaired']}`\n")
            summary.write(f"- Unchanged: `{payload['unchanged']}`\n")
        else:
            summary.write(f"- Error: `{payload.get('message', 'repair failed')}`\n")


if __name__ == "__main__":  # pragma: no cover - exercised by the workflow
    sys.exit(main())


__all__ = [
    "RepairConflictError",
    "RepairError",
    "RepairResult",
    "RepairScope",
    "ScheduleRepairService",
    "main",
]
