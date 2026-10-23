"""ESPN in-progress WP observations joined to their own play clocks.

The final-summary contract remains strict. No scoreboard clock is used here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re

from ..game.models import DomainValidationError, GameId
from .espn_summary import EspnGameSummaryClient, _data_error, _mapping, _source_id


@dataclass(frozen=True, slots=True)
class LiveObservation:
    play_id: str
    sequence: int
    home_win_probability: float
    period: int
    clock: str
    wallclock_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.play_id, str) or not self.play_id.strip():
            raise DomainValidationError("live play ID is required")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise DomainValidationError("live sequence must be nonnegative")
        p = self.home_win_probability
        if (
            isinstance(p, bool)
            or not isinstance(p, (int, float))
            or not math.isfinite(p)
            or not 0 <= p <= 1
        ):
            raise DomainValidationError("live probability must be finite and bounded")
        if (
            isinstance(self.period, bool)
            or not isinstance(self.period, int)
            or self.period < 1
        ):
            raise DomainValidationError("live period must be positive")
        if not isinstance(self.clock, str) or not re.fullmatch(
            r"\d{1,2}:[0-5]\d", self.clock
        ):
            raise DomainValidationError("live clock must be minutes:seconds")
        if self.clock_seconds > 900:
            raise DomainValidationError("live clock exceeds one period")
        if self.wallclock_at is not None and (
            not isinstance(self.wallclock_at, datetime)
            or self.wallclock_at.utcoffset() != timedelta(0)
        ):
            raise DomainValidationError("live wallclock must be UTC")

    @property
    def clock_seconds(self) -> int:
        minutes, seconds = self.clock.split(":")
        return int(minutes) * 60 + int(seconds)

    @property
    def regulation_elapsed_seconds(self) -> int:
        # OT has no assumed finishing time. This selects the last regulation
        # calibration stage; period/clock still explicitly identify overtime.
        return 3600 if self.period > 4 else self.period * 900 - self.clock_seconds


@dataclass(frozen=True, slots=True)
class LiveGameSummary:
    game_id: GameId
    observations: tuple[LiveObservation, ...]
    home_team_id: str
    away_team_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.game_id, str) or not self.game_id.strip():
            raise DomainValidationError("live game ID is required")
        if (
            any(
                not isinstance(x, str) or not x.strip()
                for x in (self.home_team_id, self.away_team_id)
            )
            or self.home_team_id == self.away_team_id
        ):
            raise DomainValidationError(
                "live summary requires distinct team identities"
            )
        if not isinstance(self.observations, tuple) or not self.observations:
            raise DomainValidationError("live observations must be a nonempty tuple")
        if any(not isinstance(x, LiveObservation) for x in self.observations):
            raise DomainValidationError("invalid live observation")
        if any(
            b.sequence <= a.sequence or b.period < a.period
            for a, b in zip(self.observations, self.observations[1:])
        ):
            raise DomainValidationError(
                "live observations must be in distinct play order"
            )


class EspnLiveSummaryClient(EspnGameSummaryClient):
    """Use the shared bounded transport, accepting in-progress games only."""

    def fetch_live_summary(self, game_id: GameId) -> LiveGameSummary:
        payload = self._fetch_payload(game_id)
        root = _mapping(payload, "summary response")
        header = _mapping(root.get("header"), "header")
        competitions = header.get("competitions")
        if not isinstance(competitions, list):
            raise _data_error("header.competitions must be a list")
        matches = [
            c
            for c in competitions
            if isinstance(c, dict) and _source_id(c.get("id")) == game_id
        ]
        if len(matches) != 1:
            raise _data_error("live summary game identity mismatch")
        status = _mapping(matches[0].get("status"), "status")
        status_type = _mapping(status.get("type"), "status.type")
        if (
            status_type.get("state") != "in"
            or status_type.get("completed") is not False
        ):
            raise _data_error("live summary is not in progress")
        competitors = matches[0].get("competitors")
        if not isinstance(competitors, list) or len(competitors) != 2:
            raise _data_error("live summary requires two competitors")
        teams = {}
        for raw in competitors:
            competitor = _mapping(raw, "competitor")
            side = competitor.get("homeAway")
            team_id = _source_id(_mapping(competitor.get("team"), "team").get("id"))
            if side not in {"home", "away"} or side in teams or team_id is None:
                raise _data_error("invalid live competitor identity")
            teams[side] = team_id
        if teams["home"] == teams["away"]:
            raise _data_error("duplicate live team identity")
        return LiveGameSummary(
            game_id, parse_live_observations(root), teams["home"], teams["away"]
        )


def _rating_play_fields(play: Mapping[str, object]) -> tuple[object, ...]:
    """Compare only the inputs consumed by the WP timing join.

    ESPN can repeat a play across previous/current drives with different display
    metadata (for example scoreValue). That does not change its rating inputs.
    """
    period = play.get("period")
    clock = play.get("clock")
    return (
        play.get("sequenceNumber"),
        period.get("number") if isinstance(period, Mapping) else period,
        clock.get("displayValue") if isinstance(clock, Mapping) else clock,
        play.get("wallclock"),
    )


def parse_live_observations(payload: object) -> tuple[LiveObservation, ...]:
    """Pure parser shared by the source replay and the production live client.

    ESPN's pregame baseline has no play clock and is excluded. Unjoined trailing
    WP is excluded until its play arrives; an interior gap rejects the snapshot.
    Repeated plays may differ in unused display metadata, but their timing
    inputs must agree. Clock corrections cannot reorder the source sequence.
    """
    root = _mapping(payload, "summary response")
    drives = _mapping(root.get("drives"), "drives")
    previous = drives.get("previous", [])
    if not isinstance(previous, list):
        raise _data_error("previous drives must be a list")
    all_drives = previous + (
        [drives["current"]] if drives.get("current") is not None else []
    )
    plays: dict[str, dict] = {}
    for drive in all_drives:
        source = _mapping(drive, "drive")
        raw_plays = source.get("plays", [])
        if not isinstance(raw_plays, list):
            raise _data_error("drive plays must be a list")
        for raw in raw_plays:
            play = dict(_mapping(raw, "play"))
            play_id = _source_id(play.get("id"))
            if play_id is None:
                raise _data_error("play ID is required")
            if play_id in plays and _rating_play_fields(
                plays[play_id]
            ) != _rating_play_fields(play):
                raise _data_error("conflicting duplicate play")
            plays[play_id] = play
    wp = root.get("winprobability")
    if not isinstance(wp, list) or not wp:
        raise _data_error("live WP is missing")
    observations: list[LiveObservation] = []
    gap = False
    try:
        for raw in wp:
            entry = _mapping(raw, "WP observation")
            play_id = _source_id(entry.get("playId"))
            play = plays.get(play_id)
            if play is None:
                gap = bool(observations)
                continue
            if gap:
                raise _data_error("interior WP timing gap")
            period = _mapping(play.get("period"), "play period").get("number")
            clock = _mapping(play.get("clock"), "play clock").get("displayValue")
            sequence = play.get("sequenceNumber")
            if isinstance(sequence, bool) or not re.fullmatch(r"\d+", str(sequence)):
                raise _data_error("play sequence is invalid")
            wallclock = play.get("wallclock")
            stamp = None
            if isinstance(wallclock, str):
                stamp = datetime.fromisoformat(wallclock.replace("Z", "+00:00"))
            observations.append(
                LiveObservation(
                    play_id=play_id,
                    sequence=int(sequence),
                    home_win_probability=entry.get("homeWinPercentage"),
                    period=period,
                    clock=clock,
                    wallclock_at=stamp,
                )
            )
        if not observations:
            raise _data_error("no WP observations with matching play clocks")
        # Reuse contract validation for ordering, including completed replays.
        return LiveGameSummary(
            GameId("replay"), tuple(observations), "home", "away"
        ).observations
    except (DomainValidationError, ValueError, TypeError) as exc:
        raise _data_error("invalid timed live WP observations") from exc
