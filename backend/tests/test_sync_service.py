from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from backend.nospoil_nfl.game import (
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    OddsSnapshot,
    RatingRetry,
    RatingSource,
    RatingState,
    RecordScope,
    RecordSnapshot,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
    TeamRecord,
    UNSET,
    WriteResult,
)
from backend.nospoil_nfl.providers import ScheduleGame, ScheduleTeam, ScoreboardBatch
from backend.nospoil_nfl.sync import ScheduleSyncService, SyncEvent, SyncMode
from backend.nospoil_nfl.sync.records import TeamSide, prepare_team_records


NOW = datetime(2026, 9, 10, 17, 0, tzinfo=UTC)
OLD = NOW - timedelta(hours=1)
REGULAR_1 = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1)
REGULAR_2 = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 2)
REGULAR_3 = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 3)
REGULAR_4 = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 4)
POSTSEASON_1 = SeasonWeek(2026, SeasonPhase.POSTSEASON, 1)
PRESEASON_1 = SeasonWeek(2026, SeasonPhase.PRESEASON, 1)
KNOWN_WEEKS = (
    PRESEASON_1,
    REGULAR_1,
    REGULAR_2,
    REGULAR_3,
    REGULAR_4,
    POSTSEASON_1,
)


class FakeRepository:
    def __init__(self, games: tuple[Game, ...] = ()) -> None:
        self.games = {game.game_id: game for game in games}

    def create_if_absent(self, game: Game) -> bool:
        if game.game_id in self.games:
            return False
        self.games[game.game_id] = game
        return True

    def get(self, game_id: GameId) -> Game | None:
        return self.games.get(game_id)

    def list_week(self, season_week: SeasonWeek) -> list[Game]:
        games = [
            game for game in self.games.values() if game.season_week == season_week
        ]
        return sorted(
            games,
            key=lambda game: (
                game.kickoff_at is None,
                game.kickoff_at or datetime.max.replace(tzinfo=UTC),
                game.game_id,
            ),
        )

    def list_season(self, season: int) -> list[Game]:
        return [
            game for game in self.games.values() if game.season_week.season == season
        ]

    def apply_schedule(self, current: Game, update: object) -> WriteResult:
        stored = self.games[current.game_id]
        if stored != current or update.observed_at <= stored.schedule_checked_at:
            return WriteResult.STALE

        status = stored.status if update.status is None else update.status
        status_changed = status != stored.status
        home = _apply_team(stored.home, update.home)
        away = _apply_team(stored.away, update.away)
        broadcaster = (
            stored.broadcaster if update.broadcaster is UNSET else update.broadcaster
        )
        odds = stored.odds if update.odds is UNSET else update.odds
        self.games[current.game_id] = replace(
            stored,
            season_week=update.season_week,
            kickoff_at=update.kickoff_at,
            home=home,
            away=away,
            status=status,
            broadcaster=broadcaster,
            odds=odds,
            schedule_checked_at=update.observed_at,
            schedule_updated_at=update.observed_at,
            live_source_checked_at=(
                update.observed_at if status_changed else stored.live_source_checked_at
            ),
            live_state_updated_at=(
                update.observed_at if status_changed else stored.live_state_updated_at
            ),
        )
        return WriteResult.APPLIED

    def apply_live_status(self, current: Game, update: object) -> WriteResult:
        stored = self.games[current.game_id]
        if stored != current or (
            stored.live_source_checked_at is not None
            and update.observed_at <= stored.live_source_checked_at
        ):
            return WriteResult.STALE
        changed = stored.status != update.status
        self.games[current.game_id] = replace(
            stored,
            status=update.status,
            live_source_checked_at=update.observed_at,
            live_state_updated_at=(
                update.observed_at if changed else stored.live_state_updated_at
            ),
        )
        return WriteResult.APPLIED

    def apply_rating(self, current: Game, rating: GameRating) -> WriteResult:
        raise AssertionError("NS-007 must not calculate or save ratings")


class FakeScoreboard:
    def __init__(self, discovery: ScoreboardBatch) -> None:
        self.discovery = discovery
        self.calls: list[SeasonWeek | None] = []
        self.by_week: dict[SeasonWeek, ScoreboardBatch] = {}

    def fetch_scoreboard(
        self, season_week: SeasonWeek | None = None
    ) -> ScoreboardBatch:
        self.calls.append(season_week)
        if season_week is None:
            return self.discovery
        return self.by_week.get(season_week, batch(season_week))


def _apply_team(current: TeamGameSnapshot, update: object) -> TeamGameSnapshot:
    identity_changed = current.team_id != update.team_id

    def optional(saved: object, incoming: object) -> object:
        if incoming is UNSET:
            return None if identity_changed else saved
        return incoming

    return TeamGameSnapshot(
        team_id=update.team_id,
        display_name=update.display_name,
        abbreviation=update.abbreviation,
        logo_key=optional(current.logo_key, update.logo_key),
        pregame_record=optional(current.pregame_record, update.pregame_record),
        postgame_record=optional(current.postgame_record, update.postgame_record),
    )


def record(wins: int, losses: int, *, at: datetime = NOW) -> RecordSnapshot:
    return RecordSnapshot(
        TeamRecord(wins, losses),
        RecordScope.REGULAR_SEASON,
        at,
    )


def team(team_id: str, *, team_record: RecordSnapshot | None = None) -> ScheduleTeam:
    return ScheduleTeam(
        team_id=team_id,
        display_name=f"Team {team_id}",
        abbreviation=team_id,
        logo_key=team_id,
        record=team_record,
    )


def observed_game(
    game_id: str,
    *,
    home_id: str = "H",
    away_id: str = "A",
    kickoff: datetime | None = NOW,
    status: GameStatus = GameStatus(GameState.SCHEDULED),
    home_record: RecordSnapshot | None = None,
    away_record: RecordSnapshot | None = None,
    odds: OddsSnapshot | None = None,
) -> ScheduleGame:
    return ScheduleGame(
        game_id=GameId(game_id),
        kickoff_at=kickoff,
        home=team(home_id, team_record=home_record),
        away=team(away_id, team_record=away_record),
        status=status,
        broadcaster="NBC",
        odds=odds,
    )


def batch(
    week: SeasonWeek,
    *games: ScheduleGame,
    observed_at: datetime = NOW,
    known_weeks: tuple[SeasonWeek, ...] = KNOWN_WEEKS,
) -> ScoreboardBatch:
    return ScoreboardBatch(observed_at, week, tuple(games), known_weeks)


def saved_game(
    game_id: str,
    *,
    week: SeasonWeek = REGULAR_1,
    kickoff: datetime | None = NOW,
    status: GameStatus = GameStatus(GameState.SCHEDULED),
    checked_at: datetime = OLD,
    live_checked_at: datetime | None = None,
    home_record: RecordSnapshot | None = None,
    away_record: RecordSnapshot | None = None,
    odds: OddsSnapshot | None = None,
    rating: GameRating | None = None,
) -> Game:
    return Game(
        game_id=GameId(game_id),
        espn_id=game_id,
        nflverse_id=None,
        season_week=week,
        kickoff_at=kickoff,
        home=TeamGameSnapshot("H", "Team H", "H", "H", home_record),
        away=TeamGameSnapshot("A", "Team A", "A", "A", away_record),
        status=status,
        rating=rating or GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=checked_at,
        schedule_updated_at=checked_at,
        live_source_checked_at=live_checked_at,
        live_state_updated_at=live_checked_at,
        broadcaster="OLD",
        odds=odds,
    )


def event(mode: SyncMode, *, season: int | None = None) -> SyncEvent:
    return SyncEvent(mode, NOW, season)


@pytest.mark.parametrize(
    ("mode", "current", "season", "expected"),
    [
        (
            SyncMode.MANUAL_PRESEASON,
            PRESEASON_1,
            2026,
            [None, REGULAR_1, REGULAR_2, REGULAR_3, REGULAR_4, POSTSEASON_1],
        ),
        (SyncMode.DAILY_NEAR_TERM, REGULAR_2, None, [None, REGULAR_3, REGULAR_4]),
        (
            SyncMode.WEEKLY_REMAINING,
            REGULAR_2,
            None,
            [None, REGULAR_3, REGULAR_4, POSTSEASON_1],
        ),
    ],
)
def test_schedule_modes_select_source_defined_weeks(
    mode: SyncMode,
    current: SeasonWeek,
    season: int | None,
    expected: list[SeasonWeek | None],
) -> None:
    provider = FakeScoreboard(batch(current))

    result = ScheduleSyncService(FakeRepository(), provider).run(
        event(mode, season=season),
        now=NOW,
    )

    assert provider.calls == expected
    assert result.scoreboard_requests == len(expected)


def test_manual_import_rejects_unverified_season() -> None:
    provider = FakeScoreboard(batch(PRESEASON_1))

    with pytest.raises(ValueError, match="does not match"):
        ScheduleSyncService(FakeRepository(), provider).run(
            event(SyncMode.MANUAL_PRESEASON, season=2027),
            now=NOW,
        )


def test_live_tick_makes_no_source_call_when_no_saved_game_is_due() -> None:
    future = saved_game("future", kickoff=NOW + timedelta(days=2))
    provider = FakeScoreboard(batch(REGULAR_1))

    result = ScheduleSyncService(FakeRepository((future,)), provider).run(
        event(SyncMode.LIVE_TICK, season=2026),
        now=NOW,
    )

    assert provider.calls == []
    assert result.scoreboard_requests == 0


def test_live_tick_uses_one_scoreboard_for_several_due_games() -> None:
    first = saved_game("one")
    second = replace(
        saved_game("two"),
        home=TeamGameSnapshot("H2", "Team H2", "H2", "H2", None),
        away=TeamGameSnapshot("A2", "Team A2", "A2", "A2", None),
    )
    live = GameStatus(GameState.IN_PROGRESS, period=2, clock="7:11", score=Score(7, 3))
    provider = FakeScoreboard(
        batch(
            REGULAR_1,
            observed_game("one", status=live),
            observed_game("two", home_id="H2", away_id="A2", status=live),
        )
    )
    repository = FakeRepository((first, second))

    result = ScheduleSyncService(repository, provider).run(
        event(SyncMode.LIVE_TICK, season=2026),
        now=NOW,
    )

    assert provider.calls == [None]
    assert result.scoreboard_requests == 1
    assert result.games_updated == 2
    assert repository.get(GameId("one")).status == live
    assert repository.get(GameId("two")).status == live


def test_daily_sync_creates_game_and_applies_flex_change_to_existing_game() -> None:
    existing = saved_game("existing", kickoff=NOW + timedelta(hours=3))
    changed_kickoff = NOW + timedelta(hours=1)
    provider = FakeScoreboard(
        batch(
            REGULAR_1,
            observed_game("new", kickoff=NOW + timedelta(hours=2)),
            observed_game("existing", kickoff=changed_kickoff),
        )
    )
    repository = FakeRepository((existing,))

    result = ScheduleSyncService(repository, provider).run(
        event(SyncMode.DAILY_NEAR_TERM),
        now=NOW,
    )

    assert result.games_created == 1
    assert result.games_updated == 1
    assert repository.get(GameId("new")).home.logo_key == "H"
    assert repository.get(GameId("existing")).kickoff_at == changed_kickoff


def test_duplicate_schedule_observation_preserves_stale_write_result() -> None:
    current = saved_game("same", checked_at=NOW)
    provider = FakeScoreboard(batch(REGULAR_1, observed_game("same")))

    result = ScheduleSyncService(FakeRepository((current,)), provider).run(
        event(SyncMode.DAILY_NEAR_TERM),
        now=NOW,
    )

    assert result.games_updated == 0
    assert result.stale_writes == 1


def test_newer_schedule_cannot_regress_a_final_game() -> None:
    final = GameStatus(GameState.FINAL, score=Score(24, 17))
    current = saved_game("final", status=final)
    provider = FakeScoreboard(batch(REGULAR_1, observed_game("final")))
    repository = FakeRepository((current,))

    result = ScheduleSyncService(repository, provider).run(
        event(SyncMode.DAILY_NEAR_TERM),
        now=NOW,
    )

    assert result.rejected_transitions == 1
    assert repository.get(GameId("final")).status == final


def test_future_week_record_excludes_a_final_current_week_result() -> None:
    final = observed_game(
        "played",
        home_id="H",
        away_id="X",
        status=GameStatus(GameState.FINAL, score=Score(21, 14)),
        home_record=record(2, 0),
    )
    unfinished = observed_game("unfinished", home_id="Y", away_id="Z")
    provider = FakeScoreboard(batch(REGULAR_2, final, unfinished))
    provider.by_week[REGULAR_3] = batch(
        REGULAR_3,
        observed_game("future", home_record=record(2, 0)),
    )
    repository = FakeRepository()

    ScheduleSyncService(repository, provider).run(
        event(SyncMode.DAILY_NEAR_TERM),
        now=NOW,
    )

    future = repository.get(GameId("future"))
    assert future.home.pregame_record.record == TeamRecord(1, 0)


def test_complete_current_week_keeps_future_source_record() -> None:
    final = observed_game(
        "played",
        home_id="H",
        away_id="X",
        status=GameStatus(GameState.FINAL, score=Score(21, 14)),
        home_record=record(2, 0),
    )
    provider = FakeScoreboard(batch(REGULAR_2, final))
    provider.by_week[REGULAR_3] = batch(
        REGULAR_3,
        observed_game("future", home_record=record(2, 0)),
    )
    repository = FakeRepository()

    ScheduleSyncService(repository, provider).run(
        event(SyncMode.DAILY_NEAR_TERM),
        now=NOW,
    )

    future = repository.get(GameId("future"))
    assert future.home.pregame_record.record == TeamRecord(2, 0)


def test_final_record_freezes_saved_pregame_and_adds_postgame() -> None:
    pregame = record(1, 0, at=OLD)
    current = saved_game("final", home_record=pregame, away_record=record(0, 1, at=OLD))
    final_status = GameStatus(GameState.FINAL, score=Score(21, 14))
    provider = FakeScoreboard(
        batch(
            REGULAR_1,
            observed_game(
                "final",
                status=final_status,
                home_record=record(2, 0),
                away_record=record(0, 2),
            ),
        )
    )
    repository = FakeRepository((current,))

    result = ScheduleSyncService(repository, provider).run(
        event(SyncMode.DAILY_NEAR_TERM),
        now=NOW,
    )

    stored = repository.get(GameId("final"))
    assert stored.home.pregame_record == pregame
    assert stored.home.postgame_record.record == TeamRecord(2, 0)
    assert result.provisional_rating_game_ids == (GameId("final"),)


def test_missing_records_and_started_odds_preserve_saved_values() -> None:
    known_record = record(1, 0, at=OLD)
    known_odds = OddsSnapshot("H -3.5", OLD)
    current = saved_game(
        "live",
        status=GameStatus(
            GameState.IN_PROGRESS, period=1, clock="10:00", score=Score(0, 0)
        ),
        home_record=known_record,
        away_record=record(0, 1, at=OLD),
        odds=known_odds,
    )
    latest = GameStatus(
        GameState.IN_PROGRESS, period=2, clock="5:00", score=Score(7, 3)
    )
    provider = FakeScoreboard(
        batch(
            REGULAR_1,
            observed_game(
                "live",
                status=latest,
                odds=OddsSnapshot("H -7.5", NOW),
            ),
        )
    )
    repository = FakeRepository((current,))

    ScheduleSyncService(repository, provider).run(
        event(SyncMode.LIVE_TICK, season=2026),
        now=NOW,
    )

    stored = repository.get(GameId("live"))
    assert stored.home.pregame_record == known_record
    assert stored.odds == known_odds


def test_playoff_records_remain_static_after_final() -> None:
    final = GameStatus(GameState.FINAL, score=Score(31, 28))
    prepared = prepare_team_records(
        phase=SeasonPhase.POSTSEASON,
        source_record=record(12, 5),
        source_status=final,
        side=TeamSide.HOME,
        saved_team=None,
        saved_status=None,
    )

    assert prepared.pregame.record == TeamRecord(12, 5)
    assert prepared.postgame is None


def test_old_scheduled_event_is_ignored_before_source_or_storage_work() -> None:
    provider = FakeScoreboard(batch(REGULAR_1))
    stale_event = SyncEvent(
        SyncMode.LIVE_TICK,
        NOW - timedelta(minutes=3),
        2026,
    )

    result = ScheduleSyncService(FakeRepository(), provider).run(stale_event, now=NOW)

    assert result.ignored_old_event
    assert provider.calls == []


def test_final_handoff_is_not_requested_for_usable_provisional_rating() -> None:
    provisional = GameRating(
        state=RatingState.PROVISIONAL,
        retry=RatingRetry(),
        score=6.2,
        source=RatingSource.ESPN,
        model_version="v1",
        input_hash="hash",
        calculated_at=OLD,
    )
    current = saved_game(
        "rated",
        status=GameStatus(GameState.FINAL, score=Score(24, 17)),
        rating=provisional,
    )
    provider = FakeScoreboard(
        batch(
            REGULAR_1,
            observed_game(
                "rated",
                status=GameStatus(GameState.FINAL, score=Score(24, 17)),
                home_record=record(2, 0),
                away_record=record(0, 2),
            ),
        )
    )

    result = ScheduleSyncService(FakeRepository((current,)), provider).run(
        event(SyncMode.DAILY_NEAR_TERM),
        now=NOW,
    )

    assert result.provisional_rating_game_ids == ()
