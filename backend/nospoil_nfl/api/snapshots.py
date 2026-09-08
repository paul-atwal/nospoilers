"""Provider-free projections and polling policy for read snapshots."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import json

from ..game.models import (
    Game,
    GameState,
    GameStatus,
    RecordSnapshot,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
)
from ..game.read_repository import GameReadRepository
from ..rating.confirmation import confirmation_work_remains, is_confirmation_supported
from .calendar import SeasonCalendar


Clock = Callable[[], datetime]


class SnapshotResult(dict[str, object]):
    """A JSON body that also carries the HTTP validator for the B3 adapter.

    It remains a normal dict so callers that only need the representation can
    use it directly.  ``etag`` is metadata for the transport and is not part
    of the body sent to clients.
    """

    def __init__(self, payload: Mapping[str, object], etag: str) -> None:
        super().__init__(payload)
        self.etag = etag

    @property
    def payload(self) -> dict[str, object]:
        return dict(self)

    @property
    def body(self) -> dict[str, object]:
        """Alias used by transport adapters when serializing the response."""
        return dict(self)


def _utc_now(clock: Clock | object) -> datetime:
    now = clock() if callable(clock) else clock.now()  # type: ignore[attr-defined]
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("clock must return a timezone-aware UTC datetime")
    return now


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("snapshot timestamps must be UTC")
    return value.isoformat().replace("+00:00", "Z")


def _week_payload(season_week: SeasonWeek) -> dict[str, object]:
    return {
        "season": season_week.season,
        "phase": season_week.phase.value,
        "week": season_week.week,
    }


def _record_payload(record: RecordSnapshot | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "wins": record.record.wins,
        "losses": record.record.losses,
        "ties": record.record.ties,
        "scope": record.scope.value,
        "snapshotAt": _timestamp(record.snapshot_at),
    }


def _team_payload(team: TeamGameSnapshot) -> dict[str, object]:
    return {
        "id": team.team_id,
        "displayName": team.display_name,
        "abbreviation": team.abbreviation,
        "logoKey": team.logo_key,
        "pregameRecord": _record_payload(team.pregame_record),
        "postgameRecord": _record_payload(team.postgame_record),
    }


def _score_payload(status: GameStatus) -> dict[str, int] | None:
    if status.score is None:
        return None
    return {"home": status.score.home, "away": status.score.away}


def _status_payload(status: GameStatus) -> dict[str, object]:
    return {
        "state": status.state.value,
        "detail": status.detail,
        "period": status.period,
        "clock": status.clock,
        "score": _score_payload(status),
    }


def _rating_payload(game: Game) -> dict[str, object]:
    rating = game.rating
    return {
        "state": rating.state.value,
        "score": rating.score,
        "source": rating.source.value if rating.source is not None else None,
        "modelVersion": rating.model_version,
        "calculatedAt": _timestamp(rating.calculated_at),
        "confirmedAt": _timestamp(rating.confirmed_at),
        "confirmationSupported": is_confirmation_supported(game.season_week),
        "confirmationWorkRemains": confirmation_work_remains(game),
    }


def _game_payload(game: Game) -> dict[str, object]:
    odds = None
    if game.odds is not None:
        odds = {
            "details": game.odds.details,
            "updatedAt": _timestamp(game.odds.updated_at),
        }
    return {
        "id": str(game.game_id),
        "espnId": game.espn_id,
        "seasonWeek": _week_payload(game.season_week),
        "kickoffAt": _timestamp(game.kickoff_at),
        "home": _team_payload(game.home),
        "away": _team_payload(game.away),
        "status": _status_payload(game.status),
        "broadcaster": game.broadcaster,
        "odds": odds,
        "rating": _rating_payload(game),
        "freshness": {
            "scheduleCheckedAt": _timestamp(game.schedule_checked_at),
            "scheduleUpdatedAt": _timestamp(game.schedule_updated_at),
            "liveSourceCheckedAt": _timestamp(game.live_source_checked_at),
            "liveStateUpdatedAt": _timestamp(game.live_state_updated_at),
        },
    }


def _represented_timestamps(game: Game) -> Iterator[datetime]:
    yield game.schedule_checked_at
    yield game.schedule_updated_at
    if game.live_source_checked_at is not None:
        yield game.live_source_checked_at
    if game.live_state_updated_at is not None:
        yield game.live_state_updated_at
    if game.rating.calculated_at is not None:
        yield game.rating.calculated_at
    if game.rating.confirmed_at is not None:
        yield game.rating.confirmed_at


def _kickoff_key(game: Game) -> tuple[int, datetime, str]:
    # The explicit leading flag prevents a fabricated/unknown kickoff from
    # ever sorting before a known one, regardless of repository order.
    if game.kickoff_at is None:
        return (1, datetime.max.replace(tzinfo=UTC), str(game.game_id))
    return (0, game.kickoff_at, str(game.game_id))


def _poll_class(game: Game, now: datetime) -> int | None:
    state = game.status.state
    if state is GameState.SCHEDULED:
        if game.kickoff_at is None:
            return 60
        remaining = game.kickoff_at - now
        if remaining > timedelta(hours=24):
            return 3600
        if remaining > timedelta(hours=2):
            return 900
        if remaining > timedelta(minutes=15):
            return 300
        return 60
    if state is GameState.IN_PROGRESS:
        return 30
    if state is GameState.DELAYED:
        return 30 if game.status.has_started else 60
    if state is GameState.POSTPONED:
        return 900
    if state is GameState.CANCELLED:
        return None
    # A pending ESPN rating is active work regardless of whether nflverse can
    # later confirm this competition.
    if game.rating.state.value == "pending":
        return 60
    if game.rating.state.value in {"provisional", "unavailable"}:
        return 3600 if is_confirmation_supported(game.season_week) else None
    return None


def canonical_etag(payload: Mapping[str, object]) -> str:
    """Return the quoted SHA-256 validator for a canonical JSON body."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f'"{hashlib.sha256(encoded).hexdigest()}"'


class ReadSnapshotService:
    """Read complete weeks/seasons with one repository call per operation."""

    def __init__(
        self,
        repository: GameReadRepository,
        calendar: SeasonCalendar,
        clock: Clock | object,
    ) -> None:
        self.repository = repository
        self.calendar = calendar
        self.clock = clock

    def bootstrap(self) -> SnapshotResult:
        now = _utc_now(self.clock)
        payload = self.calendar.bootstrap(now)
        # Calendar metadata is part of the stable representation.  The source
        # URL and verification date remain code metadata rather than API keys.
        return SnapshotResult(payload, canonical_etag(payload))

    def week_snapshot(
        self,
        season: int | SeasonWeek,
        phase: SeasonPhase | str | None = None,
        week: int | None = None,
    ) -> SnapshotResult:
        season_week = self._resolve_week(season, phase, week)
        games = self.repository.list_week(season_week)
        now = _utc_now(self.clock)
        games = sorted(games, key=_kickoff_key)
        return self._snapshot(
            games,
            season_week=season_week,
            now=now,
            is_season=False,
        )

    def season_snapshot(self, season: int) -> SnapshotResult:
        self.calendar.validate_season(season)
        games = self.repository.list_season(season)
        now = _utc_now(self.clock)
        # Rated games are the Best-of-Season section; score ties and all
        # unrated games use the same deterministic kickoff/game-ID ordering.
        games = sorted(
            games,
            key=lambda game: (
                0 if game.rating.score is not None else 1,
                -(game.rating.score or 0.0) if game.rating.score is not None else 0.0,
                *_kickoff_key(game),
            ),
        )
        return self._snapshot(games, season=season, now=now, is_season=True)

    def _resolve_week(
        self,
        season: int | SeasonWeek,
        phase: SeasonPhase | str | None,
        week: int | None,
    ) -> SeasonWeek:
        if isinstance(season, SeasonWeek):
            if phase is not None or week is not None:
                raise ValueError("SeasonWeek cannot be combined with phase/week")
            return self.calendar.validate_week(
                season.season, season.phase, season.week
            )
        if phase is None or week is None:
            raise ValueError("season, phase, and week are required")
        return self.calendar.validate_week(season, phase, week)

    def _snapshot(
        self,
        games: list[Game],
        *,
        now: datetime,
        season_week: SeasonWeek | None = None,
        season: int | None = None,
        is_season: bool,
    ) -> SnapshotResult:
        body: dict[str, object] = {
            "season": season if season is not None else season_week.season,  # type: ignore[union-attr]
            **({"week": _week_payload(season_week)} if not is_season else {}),
            "snapshotAsOf": (
                _timestamp(max(ts for game in games for ts in _represented_timestamps(game)))
                if games
                else None
            ),
            "pollAfterSeconds": self._poll_after(
                games, now, season_week=season_week, season=season
            ),
            "games": [_game_payload(game) for game in games],
        }
        # Include selection state and every game's stable class in validator
        # material without leaking transport internals into the JSON body.
        current_week = self.calendar.current_week(now)
        validator_material = {
            "body": body,
            "calendarVersion": self.calendar.calendar_version,
            "selectedCalendarWeek": _week_payload(current_week),
            "pollClasses": [_poll_class(game, now) for game in games],
        }
        return SnapshotResult(body, canonical_etag(validator_material))

    def _poll_after(
        self,
        games: list[Game],
        now: datetime,
        *,
        season_week: SeasonWeek | None,
        season: int | None,
    ) -> int | None:
        if season is not None and season != self.calendar.active_season:
            return None
        if season_week is not None and not self.calendar.is_current_or_future(
            season_week, now
        ):
            return None
        if games:
            values = [value for game in games if (value := _poll_class(game, now)) is not None]
            return min(values) if values else None
        if season is not None:
            return 300 if season == self.calendar.active_season else None
        assert season_week is not None
        return 300 if self.calendar.is_current_or_future(season_week, now) else None


__all__ = ["ReadSnapshotService", "SnapshotResult", "canonical_etag"]
