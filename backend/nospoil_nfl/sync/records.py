"""Spoiler-safe team record preparation for ESPN schedule observations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from ..game import (
    GameState,
    GameStatus,
    RecordSnapshot,
    SeasonPhase,
    TeamGameSnapshot,
    TeamRecord,
    UNSET,
)


class TeamResult(StrEnum):
    """One team's result in a completed game."""

    WIN = "win"
    LOSS = "loss"
    TIE = "tie"


class TeamSide(StrEnum):
    """A team's home or away role in one game."""

    HOME = "home"
    AWAY = "away"


@dataclass(frozen=True, slots=True)
class PreparedRecords:
    """Explicit record fields for a new game or schedule update."""

    pregame: object
    postgame: object


def results_by_team(
    status: GameStatus, home_id: str, away_id: str
) -> dict[str, TeamResult]:
    """Return stable-team-ID results for a final status."""
    if status.state is not GameState.FINAL or status.score is None:
        return {}
    if status.score.home == status.score.away:
        return {home_id: TeamResult.TIE, away_id: TeamResult.TIE}
    if status.score.home > status.score.away:
        return {home_id: TeamResult.WIN, away_id: TeamResult.LOSS}
    return {home_id: TeamResult.LOSS, away_id: TeamResult.WIN}


def prepare_team_records(
    *,
    phase: SeasonPhase,
    source_record: RecordSnapshot | None,
    source_status: GameStatus,
    observed_at: datetime,
    side: TeamSide,
    saved_team: TeamGameSnapshot | None,
    saved_status: GameStatus | None,
    freeze_pregame: bool = False,
    excluded_results: tuple[TeamResult, ...] = (),
) -> PreparedRecords:
    """Prepare records while preserving omissions and freezing started games."""
    if not isinstance(side, TeamSide):
        raise ValueError("side must be a TeamSide")
    if source_record is None:
        if saved_team is None:
            return PreparedRecords(pregame=None, postgame=None)
        postgame = saved_team.postgame_record
        if (
            phase is not SeasonPhase.POSTSEASON
            and source_status.state is GameState.FINAL
            and postgame is None
            and saved_team.pregame_record is not None
        ):
            result = _result_for_side(source_status, side)
            if result is not None:
                postgame = _apply_result(
                    saved_team.pregame_record,
                    result,
                    observed_at=observed_at,
                )
        return PreparedRecords(
            pregame=saved_team.pregame_record,
            postgame=postgame,
        )

    adjusted = _subtract_results(source_record, excluded_results)
    saved_pregame = saved_team.pregame_record if saved_team is not None else None
    has_started = (
        freeze_pregame
        or source_status.has_started
        or (saved_status is not None and saved_status.has_started)
    )

    if phase is SeasonPhase.POSTSEASON:
        pregame = (
            saved_pregame if has_started and saved_pregame is not None else adjusted
        )
        return PreparedRecords(pregame=pregame, postgame=None)

    if source_status.state is GameState.FINAL:
        result = _result_for_side(source_status, side)
        pregame = saved_pregame
        if pregame is None and result is not None:
            pregame = _subtract_results(adjusted, (result,))
        return PreparedRecords(pregame=pregame or adjusted, postgame=adjusted)

    pregame = saved_pregame if has_started and saved_pregame is not None else adjusted
    postgame: object = UNSET if saved_team is not None else None
    return PreparedRecords(pregame=pregame, postgame=postgame)


def _subtract_results(
    snapshot: RecordSnapshot,
    results: tuple[TeamResult, ...],
) -> RecordSnapshot:
    record = snapshot.record
    for result in results:
        record = TeamRecord(
            wins=max(0, record.wins - (result is TeamResult.WIN)),
            losses=max(0, record.losses - (result is TeamResult.LOSS)),
            ties=max(0, record.ties - (result is TeamResult.TIE)),
        )
    return replace(snapshot, record=record)


def _apply_result(
    snapshot: RecordSnapshot,
    result: TeamResult,
    *,
    observed_at: datetime,
) -> RecordSnapshot:
    record = snapshot.record
    return replace(
        snapshot,
        record=TeamRecord(
            wins=record.wins + (result is TeamResult.WIN),
            losses=record.losses + (result is TeamResult.LOSS),
            ties=record.ties + (result is TeamResult.TIE),
        ),
        snapshot_at=observed_at,
    )


def _result_for_side(status: GameStatus, side: TeamSide) -> TeamResult | None:
    return results_by_team(
        status,
        TeamSide.HOME.value,
        TeamSide.AWAY.value,
    ).get(side.value)


__all__ = [
    "PreparedRecords",
    "TeamResult",
    "TeamSide",
    "prepare_team_records",
    "results_by_team",
]
