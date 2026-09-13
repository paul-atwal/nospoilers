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
    RatingRetry,
    RatingState,
    RecordScope,
    RecordSnapshot,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
    TeamRecord,
    WriteResult,
)
from backend.nospoil_nfl.game.updates import UNSET
from backend.nospoil_nfl.providers import ScheduleGame, ScheduleTeam, ScoreboardBatch
from backend.nospoil_nfl.sync.repair import (
    RepairConflictError,
    RepairError,
    RepairScope,
    ScheduleRepairService,
    _parse_args,
    _scope_from_args,
    main,
)

NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
DEFAULT_SCORE = Score(24, 17)


def game(
    game_id: str = "espn-1",
    *,
    season: int = 2020,
    phase: SeasonPhase = SeasonPhase.PRESEASON,
    week: int = 1,
    score: Score = DEFAULT_SCORE,
) -> Game:
    checked = NOW - timedelta(hours=1)
    frozen = RecordSnapshot(
        TeamRecord(3, 2),
        RecordScope.PRESEASON
        if phase is SeasonPhase.PRESEASON
        else RecordScope.REGULAR_SEASON,
        checked,
    )
    return Game(
        game_id=GameId(game_id),
        espn_id=game_id,
        nflverse_id="nflverse-1",
        season_week=SeasonWeek(season, phase, week),
        kickoff_at=NOW - timedelta(hours=3),
        home=TeamGameSnapshot("home", "Home", "H", "home", frozen, frozen),
        away=TeamGameSnapshot("away", "Away", "A", "away", frozen, frozen),
        status=GameStatus(GameState.FINAL, detail="old final", score=score),
        rating=GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=checked,
        schedule_updated_at=checked,
        live_source_checked_at=checked,
        live_state_updated_at=checked,
    )


class FakeRepository:
    def __init__(self, games: tuple[Game, ...]) -> None:
        self.games = {item.game_id: item for item in games}
        self.writes: list[object] = []
        self.stale = False

    def get(self, game_id: GameId) -> Game | None:
        return self.games.get(game_id)

    def list_week(self, season_week: SeasonWeek) -> list[Game]:
        return [item for item in self.games.values() if item.season_week == season_week]

    def apply_schedule(self, current: Game, update: object) -> WriteResult:
        self.writes.append(update)
        if self.stale:
            return WriteResult.STALE
        self.games[current.game_id] = replace(
            current,
            status=update.status,
            home=replace(
                current.home,
                postgame_record=(
                    current.home.postgame_record
                    if update.home.postgame_record is UNSET
                    else update.home.postgame_record
                ),
            ),
            away=replace(
                current.away,
                postgame_record=(
                    current.away.postgame_record
                    if update.away.postgame_record is UNSET
                    else update.away.postgame_record
                ),
            ),
            schedule_checked_at=update.observed_at,
            schedule_updated_at=update.observed_at,
        )
        return WriteResult.APPLIED


class FakeScoreboard:
    def __init__(self, batch: ScoreboardBatch) -> None:
        self.batch = batch
        self.calls: list[SeasonWeek] = []

    def fetch_scoreboard(self, season_week: SeasonWeek) -> ScoreboardBatch:
        self.calls.append(season_week)
        return self.batch


def source_for(
    current: Game,
    *,
    score: Score,
    team_ids: tuple[str, str] = ("home", "away"),
    home_record: RecordSnapshot | None = None,
    away_record: RecordSnapshot | None = None,
) -> ScoreboardBatch:
    source_week = current.season_week
    source = ScheduleGame(
        game_id=current.game_id,
        kickoff_at=current.kickoff_at,
        home=ScheduleTeam(team_ids[0], "Source Home", "SH", record=home_record),
        away=ScheduleTeam(team_ids[1], "Source Away", "SA", record=away_record),
        status=GameStatus(GameState.FINAL, detail="old final", score=score),
    )
    return ScoreboardBatch(NOW, source_week, (source,))


def test_game_scope_supports_prior_season_and_preserves_owned_fields() -> None:
    current = game(score=Score(24, 17))
    repository = FakeRepository((current,))
    scoreboard = FakeScoreboard(source_for(current, score=Score(27, 24)))

    result = ScheduleRepairService(repository, scoreboard).run(
        RepairScope(game_id=current.game_id)
    )

    assert result.repaired == 1
    assert result.season == 2020
    assert result.game_ids == (current.game_id,)
    assert result.reconciliation_game_ids == ()
    assert scoreboard.calls == [current.season_week]
    update = repository.writes[0]
    assert update.kickoff_at == current.kickoff_at
    assert update.season_week == current.season_week
    assert update.home.pregame_record is UNSET
    assert update.away.postgame_record is UNSET
    assert update.broadcaster is UNSET
    assert update.odds is UNSET
    saved = repository.games[current.game_id]
    assert saved.status.score == Score(27, 24)
    assert saved.home.pregame_record == current.home.pregame_record
    assert saved.rating == current.rating


def test_week_scope_requires_exact_source_set_before_any_write() -> None:
    first = game("one")
    second = game("two")
    repository = FakeRepository((first, second))
    scoreboard = FakeScoreboard(source_for(first, score=Score(30, 20)))

    with pytest.raises(RepairError, match="exactly match durable week"):
        ScheduleRepairService(repository, scoreboard).run(
            RepairScope(season_week=first.season_week)
        )

    assert repository.writes == []


def test_team_identity_and_non_final_source_fail_closed() -> None:
    current = game()
    repository = FakeRepository((current,))
    scoreboard = FakeScoreboard(
        source_for(current, score=Score(27, 24), team_ids=("wrong", "away"))
    )

    with pytest.raises(RepairError, match="team identity"):
        ScheduleRepairService(repository, scoreboard).run(
            RepairScope(game_id=current.game_id)
        )
    assert repository.writes == []

    source = ScheduleGame(
        game_id=current.game_id,
        kickoff_at=current.kickoff_at,
        home=ScheduleTeam("home", "Home", "H"),
        away=ScheduleTeam("away", "Away", "A"),
        status=GameStatus(GameState.IN_PROGRESS, period=1, score=Score(3, 0)),
    )
    repository = FakeRepository((current,))
    scoreboard = FakeScoreboard(ScoreboardBatch(NOW, current.season_week, (source,)))
    with pytest.raises(RepairError, match="not final"):
        ScheduleRepairService(repository, scoreboard).run(
            RepairScope(game_id=current.game_id)
        )
    assert repository.writes == []


def test_unchanged_final_is_a_noop_and_stale_write_is_failure() -> None:
    current = game()
    repository = FakeRepository((current,))
    scoreboard = FakeScoreboard(source_for(current, score=current.status.score))
    result = ScheduleRepairService(repository, scoreboard).run(
        RepairScope(game_id=current.game_id)
    )
    assert result.unchanged == 1
    assert repository.writes == []

    repository = FakeRepository((current,))
    repository.stale = True
    scoreboard = FakeScoreboard(source_for(current, score=Score(27, 24)))
    with pytest.raises(RepairConflictError):
        ScheduleRepairService(repository, scoreboard).run(
            RepairScope(game_id=current.game_id)
        )


def test_final_repair_derives_stale_postgame_records_and_is_idempotent() -> None:
    pregame = RecordSnapshot(TeamRecord(0, 0), RecordScope.REGULAR_SEASON, NOW)
    current = replace(
        game(
            "401872658",
            season=2026,
            phase=SeasonPhase.REGULAR_SEASON,
            score=Score(20, 13),
        ),
        home=TeamGameSnapshot("pit", "Steelers", "PIT", "pit", pregame, pregame),
        away=TeamGameSnapshot("atl", "Falcons", "ATL", "atl", pregame, pregame),
    )
    repository = FakeRepository((current,))
    scoreboard = FakeScoreboard(
        source_for(
            current,
            score=Score(20, 13),
            team_ids=("pit", "atl"),
            home_record=pregame,
            away_record=pregame,
        )
    )

    result = ScheduleRepairService(repository, scoreboard).run(
        RepairScope(game_id=current.game_id)
    )

    assert result.repaired == 1
    repaired = repository.games[current.game_id]
    assert repaired.home.pregame_record == pregame
    assert repaired.away.pregame_record == pregame
    assert repaired.home.postgame_record is not None
    assert repaired.home.postgame_record.record == TeamRecord(1, 0)
    assert repaired.away.postgame_record is not None
    assert repaired.away.postgame_record.record == TeamRecord(0, 1)
    assert repaired.status.score == Score(20, 13)

    rerun = ScheduleRepairService(repository, scoreboard).run(
        RepairScope(game_id=current.game_id)
    )
    assert rerun.repaired == 0
    assert rerun.unchanged == 1
    assert len(repository.writes) == 1


def test_final_repair_keeps_advanced_espn_records_authoritative() -> None:
    pregame = RecordSnapshot(TeamRecord(0, 0), RecordScope.REGULAR_SEASON, NOW)
    current = replace(
        game(season=2026, phase=SeasonPhase.REGULAR_SEASON, score=Score(20, 13)),
        home=replace(game().home, pregame_record=pregame, postgame_record=pregame),
        away=replace(game().away, pregame_record=pregame, postgame_record=pregame),
    )
    home_source = replace(pregame, record=TeamRecord(1, 0))
    away_source = replace(pregame, record=TeamRecord(0, 1))
    repository = FakeRepository((current,))
    scoreboard = FakeScoreboard(
        source_for(
            current,
            score=Score(20, 13),
            home_record=home_source,
            away_record=away_source,
        )
    )

    ScheduleRepairService(repository, scoreboard).run(
        RepairScope(game_id=current.game_id)
    )

    repaired = repository.games[current.game_id]
    assert repaired.home.postgame_record == home_source
    assert repaired.away.postgame_record == away_source
    assert repaired.home.pregame_record == pregame
    assert repaired.away.pregame_record == pregame


def test_scope_parser_requires_exact_game_or_week_scope() -> None:
    assert _scope_from_args(_parse_args(["--game-id", "abc"])) == RepairScope(
        game_id=GameId("abc")
    )
    assert _scope_from_args(
        _parse_args(["--season", "2020", "--phase", "preseason", "--week", "1"])
    ) == RepairScope(season_week=SeasonWeek(2020, SeasonPhase.PRESEASON, 1))
    with pytest.raises(SystemExit):
        _parse_args(["--game-id", "abc", "--season", "2020"])
    with pytest.raises(SystemExit):
        _parse_args(["--season", "2020", "--phase", "preseason"])


def test_main_runs_with_injected_clients_and_reports_scope(
    monkeypatch, tmp_path, capsys
) -> None:
    current = game()
    monkeypatch.setenv("NOSPOIL_GAMES_TABLE", "staging-games")
    output_path = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    batch = source_for(current, score=Score(27, 24))
    repositories: list[tuple[str, str]] = []

    def repository_factory(table: str, index: str) -> FakeRepository:
        repositories.append((table, index))
        return FakeRepository((current,))

    assert (
        main(
            ["--season", "2020", "--phase", "preseason", "--week", "1"],
            repository_factory=repository_factory,
            scoreboard_factory=lambda *, timeout_seconds: FakeScoreboard(batch),
        )
        == 0
    )
    assert repositories == [("staging-games", "season-schedule-index")]
    assert '"ok":true' in capsys.readouterr().out
    assert output_path.read_text().splitlines() == [
        "season=2020",
        "reconciliation_game_ids=[]",
    ]


def test_supported_week_returns_exact_reconciliation_game_ids() -> None:
    first = game("one", phase=SeasonPhase.REGULAR_SEASON)
    second = game("two", phase=SeasonPhase.REGULAR_SEASON)
    batch = ScoreboardBatch(
        NOW,
        first.season_week,
        (
            source_for(first, score=Score(27, 24)).games[0],
            source_for(second, score=Score(30, 20)).games[0],
        ),
    )

    result = ScheduleRepairService(
        FakeRepository((first, second)), FakeScoreboard(batch)
    ).run(RepairScope(season_week=first.season_week))

    assert result.season == 2020
    assert result.reconciliation_game_ids == (first.game_id, second.game_id)
