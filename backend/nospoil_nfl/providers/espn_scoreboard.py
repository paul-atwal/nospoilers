"""Concrete ESPN scoreboard provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
import math
import re
from typing import Any
from urllib.parse import urlencode

import requests

from ..game.models import (
    DomainValidationError,
    GameId,
    GameState,
    GameStatus,
    OddsSnapshot,
    RecordScope,
    RecordSnapshot,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamRecord,
)
from .errors import (
    ProviderDataError,
    ProviderTransportError,
    ProviderUnavailableError,
)
from .models import ScheduleGame, ScheduleTeam, ScoreboardBatch


ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
)
_PROVIDER = "espn"
_OPERATION = "scoreboard"
_RECORD_PATTERN = re.compile(r"^(?P<wins>\d+)-(?P<losses>\d+)(?:-(?P<ties>\d+))?$")
_SEASON_PHASES = {
    1: SeasonPhase.PRESEASON,
    2: SeasonPhase.REGULAR_SEASON,
    3: SeasonPhase.POSTSEASON,
}


def utc_now() -> datetime:
    """Return the current UTC time for source observation metadata."""
    return datetime.now(UTC)


class EspnScoreboardClient:
    """Fetch and normalize one ESPN scoreboard response."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite number greater than 0")
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    def fetch_scoreboard(
        self,
        season_week: SeasonWeek | None = None,
    ) -> ScoreboardBatch:
        """Return every normalized game in the requested ESPN response."""
        url = _scoreboard_url(season_week)
        observed_at = self._observation_time()

        try:
            response = requests.get(url, timeout=self._timeout_seconds)
        except requests.RequestException as exc:
            raise ProviderTransportError(
                "ESPN scoreboard request failed",
                provider=_PROVIDER,
                operation=_OPERATION,
            ) from exc

        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int):
            raise _data_error("ESPN scoreboard response has no valid status code")
        if status_code == 429 or status_code >= 500:
            raise ProviderUnavailableError(
                f"ESPN scoreboard returned HTTP {status_code}",
                provider=_PROVIDER,
                operation=_OPERATION,
            )
        if status_code != 200:
            raise _data_error(f"ESPN scoreboard returned HTTP {status_code}")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise _data_error("ESPN scoreboard response was not valid JSON") from exc

        try:
            return _normalize_scoreboard(payload, season_week, observed_at)
        except ProviderDataError:
            raise
        except (DomainValidationError, KeyError, TypeError, ValueError) as exc:
            raise _data_error("ESPN scoreboard response was malformed") from exc

    def _observation_time(self) -> datetime:
        try:
            observed_at = self._clock()
        except Exception as exc:
            raise _data_error("ESPN scoreboard observation clock failed") from exc
        if not isinstance(
            observed_at, datetime
        ) or observed_at.utcoffset() != timedelta(0):
            raise _data_error("ESPN scoreboard observation time must be UTC")
        return observed_at


def _scoreboard_url(season_week: SeasonWeek | None) -> str:
    if season_week is None:
        return ESPN_SCOREBOARD_URL
    phase_type = {
        SeasonPhase.PRESEASON: 1,
        SeasonPhase.REGULAR_SEASON: 2,
        SeasonPhase.POSTSEASON: 3,
    }[season_week.phase]
    query = urlencode(
        {
            "dates": season_week.season,
            "week": season_week.week,
            "seasontype": phase_type,
            "limit": 100,
        }
    )
    return f"{ESPN_SCOREBOARD_URL}?{query}"


def _normalize_scoreboard(
    payload: object,
    requested_week: SeasonWeek | None,
    observed_at: datetime,
) -> ScoreboardBatch:
    root = _mapping(payload, "scoreboard response")
    season = _mapping(root.get("season"), "season")
    season_year = _positive_int(season.get("year"), "season.year")
    season_type = _positive_int(season.get("type"), "season.type")
    try:
        phase = _SEASON_PHASES[season_type]
    except KeyError as exc:
        raise _data_error(f"unsupported ESPN season.type: {season_type}") from exc

    week = _mapping(root.get("week"), "week")
    week_number = _positive_int(week.get("number"), "week.number")
    season_week = SeasonWeek(season_year, phase, week_number)
    if requested_week is not None and season_week != requested_week:
        raise _data_error(
            "ESPN scoreboard envelope does not match the requested season week"
        )

    events = root.get("events")
    if not isinstance(events, list):
        raise _data_error("events must be a list")
    games = tuple(
        _normalize_event(event, season_week=season_week, observed_at=observed_at)
        for event in events
    )
    known_weeks = _normalize_known_weeks(root.get("leagues"), season_year)
    try:
        return ScoreboardBatch(
            observed_at=observed_at,
            season_week=season_week,
            games=games,
            known_weeks=known_weeks,
        )
    except DomainValidationError as exc:
        raise _data_error("normalized ESPN scoreboard was invalid") from exc


def _normalize_event(
    event: object,
    *,
    season_week: SeasonWeek,
    observed_at: datetime,
) -> ScheduleGame:
    source_event = _mapping(event, "event")
    event_id = _text(source_event.get("id"), "event.id")
    competitions = source_event.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1:
        raise _data_error("event.competitions must contain exactly one competition")
    competition = _mapping(competitions[0], "competition")

    competitors = competition.get("competitors")
    if not isinstance(competitors, list) or len(competitors) != 2:
        raise _data_error("competition.competitors must contain exactly two teams")
    by_side: dict[str, ScheduleTeam] = {}
    raw_scores: dict[str, object] = {}
    for competitor in competitors:
        source_competitor = _mapping(competitor, "competitor")
        side = _text(source_competitor.get("homeAway"), "competitor.homeAway")
        if side not in {"home", "away"} or side in by_side:
            raise _data_error("competition must contain one home and one away team")
        by_side[side] = _normalize_team(
            source_competitor,
            season_week=season_week,
            observed_at=observed_at,
        )
        raw_scores[side] = source_competitor.get("score")
    if set(by_side) != {"home", "away"}:
        raise _data_error("competition must contain one home and one away team")

    status = _normalize_status(
        source_event.get("status"),
        raw_scores,
    )
    kickoff_at = _optional_timestamp(source_event.get("date"), "event.date")
    broadcaster = _normalize_broadcast(competition.get("broadcasts"))
    odds = _normalize_odds(
        competition.get("odds"),
        observed_at=observed_at,
        game_started=status.has_started,
    )
    try:
        return ScheduleGame(
            game_id=GameId(event_id),
            kickoff_at=kickoff_at,
            home=by_side["home"],
            away=by_side["away"],
            status=status,
            broadcaster=broadcaster,
            odds=odds,
        )
    except DomainValidationError as exc:
        raise _data_error(
            f"event {event_id} did not form a valid schedule game"
        ) from exc


def _normalize_team(
    competitor: Mapping[str, Any],
    *,
    season_week: SeasonWeek,
    observed_at: datetime,
) -> ScheduleTeam:
    team = _mapping(competitor.get("team"), "competitor.team")
    team_id = _text(team.get("id"), "team.id")
    display_name = _first_text(
        team,
        ("fullDisplayName", "displayName", "shortDisplayName"),
        "team display name",
    )
    abbreviation = _text(team.get("abbreviation"), "team.abbreviation")
    record = _normalize_record(
        competitor.get("records"),
        season_week=season_week,
        observed_at=observed_at,
    )
    try:
        return ScheduleTeam(
            team_id=team_id,
            display_name=display_name,
            abbreviation=abbreviation,
            logo_key=team_id,
            record=record,
        )
    except DomainValidationError as exc:
        raise _data_error(f"team {team_id} was invalid") from exc


def _normalize_record(
    records: object,
    *,
    season_week: SeasonWeek,
    observed_at: datetime,
) -> RecordSnapshot | None:
    if records is None:
        return None
    if not isinstance(records, list):
        raise _data_error("competitor.records must be a list")

    valid_records: list[TeamRecord] = []
    for record in records:
        source_record = _mapping(record, "competitor record")
        if (
            source_record.get("name") != "overall"
            or source_record.get("type") != "total"
        ):
            continue
        summary = source_record.get("summary")
        if not isinstance(summary, str):
            continue
        match = _RECORD_PATTERN.fullmatch(summary.strip())
        if match is None:
            continue
        valid_records.append(
            TeamRecord(
                wins=int(match.group("wins")),
                losses=int(match.group("losses")),
                ties=int(match.group("ties") or 0),
            )
        )

    if not valid_records:
        return None
    if len(valid_records) > 1:
        raise _data_error("competitor has multiple valid overall records")
    scope = (
        RecordScope.PRESEASON
        if season_week.phase is SeasonPhase.PRESEASON
        else RecordScope.REGULAR_SEASON
    )
    try:
        return RecordSnapshot(
            record=valid_records[0],
            scope=scope,
            snapshot_at=observed_at,
        )
    except DomainValidationError as exc:
        raise _data_error("normalized team record was invalid") from exc


def _normalize_known_weeks(
    leagues: object,
    season: int,
) -> tuple[SeasonWeek, ...]:
    """Read the source calendar without assuming fixed phase lengths."""
    if leagues is None:
        return ()
    if not isinstance(leagues, list) or len(leagues) != 1:
        raise _data_error("leagues must contain exactly one league")
    league = _mapping(leagues[0], "league")
    league_season = _mapping(league.get("season"), "league.season")
    if _positive_int(league_season.get("year"), "league.season.year") != season:
        raise _data_error("league season does not match scoreboard season")
    calendar = league.get("calendar")
    if not isinstance(calendar, list):
        raise _data_error("league.calendar must be a list")

    weeks: list[SeasonWeek] = []
    for phase_value in calendar:
        source_phase = _mapping(phase_value, "calendar phase")
        phase_number = _positive_int(source_phase.get("value"), "calendar phase.value")
        phase = _SEASON_PHASES.get(phase_number)
        if phase is None:
            continue
        entries = source_phase.get("entries")
        if not isinstance(entries, list):
            raise _data_error("supported calendar phase entries must be a list")
        for entry_value in entries:
            entry = _mapping(entry_value, "calendar entry")
            week = _positive_int(entry.get("value"), "calendar entry.value")
            weeks.append(SeasonWeek(season, phase, week))

    phase_rank = {
        SeasonPhase.PRESEASON: 0,
        SeasonPhase.REGULAR_SEASON: 1,
        SeasonPhase.POSTSEASON: 2,
    }
    return tuple(sorted(weeks, key=lambda item: (phase_rank[item.phase], item.week)))


def _normalize_status(status: object, raw_scores: Mapping[str, object]) -> GameStatus:
    source_status = _mapping(status, "event.status")
    status_type = _mapping(source_status.get("type"), "event.status.type")
    name = _text(status_type.get("name"), "event.status.type.name")
    state = _text(status_type.get("state"), "event.status.type.state").lower()
    completed = status_type.get("completed")
    if not isinstance(completed, bool):
        raise _data_error("event.status.type.completed must be a boolean")
    if state not in {"pre", "in", "post"}:
        raise _data_error(f"unsupported ESPN status state: {state}")

    text_fields = []
    for field in ("detail", "shortDetail", "description"):
        value = status_type.get(field)
        if value is not None and not isinstance(value, str):
            raise _data_error(f"event.status.{field} must be text")
        if isinstance(value, str) and value.strip():
            text_fields.append(value.strip())
    detail = text_fields[0] if text_fields else None
    markers = " ".join([name, *text_fields]).lower()
    is_cancelled = "cancel" in markers
    is_postponed = "postpon" in markers
    is_delayed = "delay" in markers
    status_name = name.lower()
    is_scheduled = "schedul" in status_name or name == "STATUS_PREGAME"
    is_started = any(
        marker in status_name for marker in ("in_progress", "playing", "halftime")
    )
    is_finished = (
        any(marker in status_name for marker in ("final", "game_end", "completed"))
        or name == "STATUS_END"
    )

    if is_cancelled:
        return GameStatus(GameState.CANCELLED, detail=detail)

    period = _optional_nonnegative_int(source_status.get("period"), "status.period")
    clock = source_status.get("displayClock")
    if clock is not None and not isinstance(clock, str):
        raise _data_error("event.status.displayClock must be text")
    clock = clock.strip() if isinstance(clock, str) and clock.strip() else None

    if state == "pre":
        if completed:
            raise _data_error("pregame ESPN status cannot be completed")
        if is_postponed:
            return GameStatus(GameState.POSTPONED, detail=detail)
        if is_delayed:
            return GameStatus(GameState.DELAYED, detail=detail)
        if is_finished or is_started:
            raise _data_error(f"contradictory pregame ESPN status: {name}")
        if not is_scheduled:
            raise _data_error(f"unrecognized pregame ESPN status: {name}")
        return GameStatus(GameState.SCHEDULED, detail=detail)

    if state == "in":
        if completed or is_postponed or is_scheduled or is_finished:
            raise _data_error(f"contradictory in-game ESPN status: {name}")
        if period is None or period < 1:
            raise _data_error("in-game ESPN status requires a positive period")
        score = _normalize_started_score(raw_scores)
        if is_delayed:
            return GameStatus(
                GameState.DELAYED,
                detail=detail,
                period=period,
                clock=clock,
                score=score,
            )
        if not is_started and not is_delayed:
            raise _data_error(f"unrecognized in-game ESPN status: {name}")
        if is_finished or is_scheduled:
            raise _data_error(f"unrecognized in-game ESPN status: {name}")
        return GameStatus(
            GameState.IN_PROGRESS,
            detail=detail,
            period=period,
            clock=clock,
            score=score,
        )

    if is_postponed:
        if completed or is_cancelled:
            raise _data_error(f"contradictory postponed ESPN status: {name}")
        return GameStatus(GameState.POSTPONED, detail=detail)
    if is_delayed or is_scheduled or is_started:
        raise _data_error(f"contradictory postgame ESPN status: {name}")
    if not completed:
        raise _data_error(f"postgame ESPN status is not completed: {name}")
    if not is_finished:
        raise _data_error(f"unrecognized postgame ESPN status: {name}")
    return GameStatus(
        GameState.FINAL,
        detail=detail,
        period=period,
        clock=clock,
        score=_normalize_started_score(raw_scores),
    )


def _normalize_started_score(raw_scores: Mapping[str, object]) -> Score:
    home = _nonnegative_int(raw_scores.get("home"), "home score")
    away = _nonnegative_int(raw_scores.get("away"), "away score")
    try:
        return Score(home=home, away=away)
    except DomainValidationError as exc:
        raise _data_error("normalized score was invalid") from exc


def _normalize_broadcast(broadcasts: object) -> str | None:
    if broadcasts is None:
        return None
    if not isinstance(broadcasts, list):
        raise _data_error("competition.broadcasts must be a list")
    for broadcast in broadcasts:
        source_broadcast = _mapping(broadcast, "broadcast")
        names = source_broadcast.get("names", [])
        if not isinstance(names, list):
            raise _data_error("broadcast.names must be a list")
        for name in names:
            if not isinstance(name, str):
                raise _data_error("broadcast name must be text")
            if name.strip():
                return name.strip()
    return None


def _normalize_odds(
    odds: object,
    *,
    observed_at: datetime,
    game_started: bool,
) -> OddsSnapshot | None:
    if odds is None:
        return None
    if not isinstance(odds, list):
        raise _data_error("competition.odds must be a list")
    for odd in odds:
        source_odd = _mapping(odd, "odds")
        details = source_odd.get("details")
        if details is not None and not isinstance(details, str):
            raise _data_error("odds.details must be text")
        if game_started:
            continue
        if isinstance(details, str) and details.strip():
            try:
                return OddsSnapshot(details=details.strip(), updated_at=observed_at)
            except DomainValidationError as exc:
                raise _data_error("normalized odds were invalid") from exc
    return None


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _data_error(f"{name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _data_error(f"{name} must be non-empty text")
    return value.strip()


def _first_text(source: Mapping[str, Any], fields: tuple[str, ...], name: str) -> str:
    for field in fields:
        value = source.get(field)
        if value is not None and not isinstance(value, str):
            raise _data_error(f"{field} must be text")
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise _data_error(f"{name} must be present")


def _positive_int(value: object, name: str) -> int:
    result = _integer(value, name)
    if result < 1:
        raise _data_error(f"{name} must be an integer >= 1")
    return result


def _optional_nonnegative_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    result = _integer(value, name)
    if result < 0:
        raise _data_error(f"{name} must be an integer >= 0")
    return result


def _nonnegative_int(value: object, name: str) -> int:
    result = _integer(value, name)
    if result < 0:
        raise _data_error(f"{name} must be an integer >= 0")
    return result


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise _data_error(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value.strip())
    raise _data_error(f"{name} must be an integer")


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise _data_error(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _data_error(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise _data_error(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _optional_timestamp(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, name)


def _data_error(message: str) -> ProviderDataError:
    return ProviderDataError(message, provider=_PROVIDER, operation=_OPERATION)


__all__ = ["ESPN_SCOREBOARD_URL", "EspnScoreboardClient"]
