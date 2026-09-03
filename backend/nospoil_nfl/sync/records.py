"""Spoiler-safe team record preparation for ESPN schedule observations."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    side: TeamSide,
    saved_team: TeamGameSnapshot | None,
    saved_status: GameStatus | None,
    excluded_results: tuple[TeamResult, ...] = (),
) -> PreparedRecords:
    """Prepare records while preserving omissions and freezing started games."""
    if not isinstance(side, TeamSide):
        raise ValueError("side must be a TeamSide")
    if source_record is None:
        missing = UNSET if saved_team is not None else None
        return PreparedRecords(pregame=missing, postgame=missing)

    adjusted = _subtract_results(source_record, excluded_results)
    saved_pregame = saved_team.pregame_record if saved_team is not None else None
    has_started = source_status.has_started or (
        saved_status is not None and saved_status.has_started
    )

    if phase is SeasonPhase.POSTSEASON:
        pregame = (
            saved_pregame if has_started and saved_pregame is not None else adjusted
        )
        return PreparedRecords(pregame=pregame, postgame=None)

    if source_status.state is GameState.FINAL:
        result = results_by_team(
            source_status,
            TeamSide.HOME.value,
            TeamSide.AWAY.value,
        ).get(side.value)
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


__all__ = [
    "PreparedRecords",
    "TeamResult",
    "TeamSide",
    "prepare_team_records",
    "results_by_team",
]
