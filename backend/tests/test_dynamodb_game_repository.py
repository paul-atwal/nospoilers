from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
import subprocess
import time
from uuid import uuid4

import boto3
from botocore.exceptions import EndpointConnectionError
import pytest

from backend.nospoil_nfl.api import ReadSnapshotService, SeasonCalendar
from backend.nospoil_nfl.game import (
    DomainValidationError,
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    LiveFinalizationUpdate,
    LiveStatusUpdate,
    OddsSnapshot,
    RecordScope,
    RecordSnapshot,
    RatingRetry,
    RatingSource,
    RatingState,
    ScheduleUpdate,
    SeasonPhase,
    SeasonWeek,
    Score,
    TeamGameSnapshot,
    TeamScheduleUpdate,
    TeamRecord,
    UNSET,
    WriteResult,
)
from backend.nospoil_nfl.game.dynamodb_repository import DynamoGameRepository
from backend.nospoil_nfl.game.repository import (
    GameRepositoryDataError,
    GameRepositoryError,
    NflverseIdConflictError,
)
from backend.nospoil_nfl.providers import ScheduleGame, ScheduleTeam, ScoreboardBatch
from backend.nospoil_nfl.sync import ScheduleSyncService, SyncEvent, SyncMode


CHECKED_AT = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
_DYNAMODB_ENDPOINT_ENV = "NOSPOIL_DYNAMODB_LOCAL_ENDPOINT"


def make_team(team_id: str) -> TeamGameSnapshot:
    return TeamGameSnapshot(
        team_id=team_id,
        display_name=f"Team {team_id}",
        abbreviation=team_id.upper(),
        logo_key=None,
        pregame_record=None,
    )


def make_record(
    wins: int,
    losses: int,
    *,
    snapshot_at: datetime,
) -> RecordSnapshot:
    return RecordSnapshot(
        record=TeamRecord(wins=wins, losses=losses),
        scope=RecordScope.REGULAR_SEASON,
        snapshot_at=snapshot_at,
    )


def make_game(
    game_id: str,
    *,
    season_week: SeasonWeek = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1),
    kickoff_at: datetime | None = CHECKED_AT,
) -> Game:
    state = GameState.SCHEDULED if kickoff_at is not None else GameState.POSTPONED
    return Game(
        game_id=GameId(game_id),
        espn_id=game_id,
        nflverse_id=None,
        season_week=season_week,
        kickoff_at=kickoff_at,
        home=make_team(f"home-{game_id}"),
        away=make_team(f"away-{game_id}"),
        status=GameStatus(state=state),
        rating=GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=CHECKED_AT,
        schedule_updated_at=CHECKED_AT,
    )


def make_final_game(game_id: str) -> Game:
    return replace(
        make_game(game_id),
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
    )


def make_provisional_rating(*, score: float = 6.5) -> GameRating:
    return GameRating(
        state=RatingState.PROVISIONAL,
        retry=RatingRetry(),
        score=score,
        source=RatingSource.ESPN,
        model_version="rating-v1",
        input_hash="input-hash",
        calculated_at=CHECKED_AT,
    )


def make_unavailable_rating() -> GameRating:
    return GameRating(
        state=RatingState.UNAVAILABLE,
        retry=RatingRetry(attempt_count=1, last_error="espn_unavailable"),
    )


def make_confirmed_rating(*, score: float = 7.5) -> GameRating:
    return GameRating(
        state=RatingState.CONFIRMED,
        retry=RatingRetry(),
        score=score,
        source=RatingSource.NFLVERSE,
        model_version="rating-v1",
        input_hash="nflverse-input-hash",
        calculated_at=CHECKED_AT,
        confirmed_at=CHECKED_AT,
    )


def schedule_team(game_team: TeamGameSnapshot) -> TeamScheduleUpdate:
    return TeamScheduleUpdate(
        team_id=game_team.team_id,
        display_name=game_team.display_name,
        abbreviation=game_team.abbreviation,
    )


def schedule_update(
    game: Game,
    *,
    observed_at: datetime,
    kickoff_at: datetime | None | object = UNSET,
    status: GameStatus | None = None,
    home: TeamScheduleUpdate | None = None,
    away: TeamScheduleUpdate | None = None,
    broadcaster: str | None | object = UNSET,
    odds: OddsSnapshot | None | object = UNSET,
) -> ScheduleUpdate:
    return ScheduleUpdate(
        game_id=game.game_id,
        observed_at=observed_at,
        season_week=game.season_week,
        kickoff_at=game.kickoff_at if kickoff_at is UNSET else kickoff_at,
        home=schedule_team(game.home) if home is None else home,
        away=schedule_team(game.away) if away is None else away,
        status=status,
        broadcaster=broadcaster,
        odds=odds,
    )


def swapped_sides_update(game: Game, *, observed_at: datetime) -> ScheduleUpdate:
    return schedule_update(
        game,
        observed_at=observed_at,
        home=replace(
            schedule_team(game.away),
            logo_key=game.away.logo_key,
            pregame_record=game.away.pregame_record,
            postgame_record=game.away.postgame_record,
        ),
        away=replace(
            schedule_team(game.home),
            logo_key=game.home.logo_key,
            pregame_record=game.home.pregame_record,
            postgame_record=game.home.postgame_record,
        ),
    )


def live_status_update(
    game: Game,
    *,
    observed_at: datetime,
    status: GameStatus,
) -> LiveStatusUpdate:
    return LiveStatusUpdate(
        game_id=game.game_id,
        observed_at=observed_at,
        status=status,
    )


def live_finalization_update(
    game: Game,
    *,
    observed_at: datetime,
    status: GameStatus,
    home_pregame_record: RecordSnapshot | None = None,
    home_postgame_record: RecordSnapshot | None = None,
    away_pregame_record: RecordSnapshot | None = None,
    away_postgame_record: RecordSnapshot | None = None,
    home_team_id: str | None = None,
    away_team_id: str | None = None,
) -> LiveFinalizationUpdate:
    return LiveFinalizationUpdate(
        game_id=game.game_id,
        observed_at=observed_at,
        status=status,
        home_team_id=game.home.team_id if home_team_id is None else home_team_id,
        away_team_id=game.away.team_id if away_team_id is None else away_team_id,
        home_pregame_record=home_pregame_record,
        home_postgame_record=home_postgame_record,
        away_pregame_record=away_pregame_record,
        away_postgame_record=away_postgame_record,
    )


def live_observation(
    game: Game,
    *,
    observed_at: datetime,
    include_records: bool = True,
) -> ScoreboardBatch:
    final = GameStatus(GameState.FINAL, score=Score(home=24, away=17))
    return ScoreboardBatch(
        observed_at=observed_at,
        season_week=game.season_week,
        games=(
            ScheduleGame(
                game_id=game.game_id,
                kickoff_at=game.kickoff_at,
                home=ScheduleTeam(
                    team_id=game.home.team_id,
                    display_name=game.home.display_name,
                    abbreviation=game.home.abbreviation,
                    record=(
                        make_record(2, 0, snapshot_at=observed_at)
                        if include_records
                        else None
                    ),
                ),
                away=ScheduleTeam(
                    team_id=game.away.team_id,
                    display_name=game.away.display_name,
                    abbreviation=game.away.abbreviation,
                    record=(
                        make_record(0, 2, snapshot_at=observed_at)
                        if include_records
                        else None
                    ),
                ),
                status=final,
            ),
        ),
    )


@pytest.fixture(scope="session")
def dynamodb_resource() -> Iterator[object]:
    endpoint = os.environ.get(_DYNAMODB_ENDPOINT_ENV)
    container_id: str | None = None

    if endpoint is None:
        start = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--publish",
                "127.0.0.1::8000",
                "amazon/dynamodb-local:latest",
                "-jar",
                "DynamoDBLocal.jar",
                "-inMemory",
                "-sharedDb",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        container_id = start.stdout.strip()
        port = subprocess.run(
            ["docker", "port", container_id, "8000/tcp"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        endpoint = f"http://{port}"

    resource = boto3.resource(
        "dynamodb",
        endpoint_url=endpoint,
        region_name="us-west-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                resource.meta.client.list_tables()
                break
            except EndpointConnectionError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("DynamoDB Local did not start within 15 seconds")
                time.sleep(0.1)
        yield resource
    finally:
        if container_id is not None:
            subprocess.run(
                ["docker", "stop", container_id],
                check=False,
                capture_output=True,
            )


@pytest.fixture
def game_table(dynamodb_resource: object) -> Iterator[object]:
    table_name = f"nospoil-games-{uuid4().hex}"
    resource = dynamodb_resource
    table = resource.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "game_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "game_id", "AttributeType": "S"},
            {"AttributeName": "season_key", "AttributeType": "S"},
            {"AttributeName": "schedule_key", "AttributeType": "S"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        GlobalSecondaryIndexes=[
            {
                "IndexName": "season-schedule-index",
                "KeySchema": [
                    {"AttributeName": "season_key", "KeyType": "HASH"},
                    {"AttributeName": "schedule_key", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 5,
                    "WriteCapacityUnits": 5,
                },
            }
        ],
    )
    table.wait_until_exists()
    try:
        yield table
    finally:
        table.delete()
        table.wait_until_not_exists()


def test_create_if_absent_keeps_the_first_complete_game(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000001")
    duplicate = replace(original, broadcaster="Replacement broadcaster")

    assert repository.create_if_absent(original) is True
    assert repository.create_if_absent(duplicate) is False
    assert repository.get(original.game_id) == original


def test_list_week_uses_kickoff_order_and_excludes_other_weeks(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    expected = [
        make_game("401000001", kickoff_at=datetime(2026, 9, 10, 17, 0, tzinfo=UTC)),
        make_game("401000002"),
        make_game("401000003"),
        make_game("401000004", kickoff_at=None),
    ]
    other_week = make_game(
        "401000005",
        season_week=SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 2),
    )

    for game in [expected[2], other_week, expected[3], expected[1], expected[0]]:
        assert repository.create_if_absent(game) is True

    assert repository.list_week(SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1)) == expected


def test_read_snapshot_consumes_all_paginated_week_results(game_table: object) -> None:
    """The read projection receives complete GSI pagination, not one page."""
    repository = DynamoGameRepository(game_table, query_page_size=2)
    expected = [
        make_game("401000001", kickoff_at=datetime(2026, 9, 10, 17, 0, tzinfo=UTC)),
        make_game("401000002", kickoff_at=datetime(2026, 9, 10, 18, 0, tzinfo=UTC)),
        make_game("401000003", kickoff_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC)),
        make_game("401000004", kickoff_at=None),
    ]
    for stored in reversed(expected):
        assert repository.create_if_absent(stored) is True

    response = ReadSnapshotService(
        repository,
        SeasonCalendar(),
        lambda: datetime(2026, 9, 10, 20, 0, tzinfo=UTC),
    ).week_snapshot(2026, "regular_season", 1)

    assert [item["id"] for item in response["games"]] == [
        "401000001",
        "401000002",
        "401000003",
        "401000004",
    ]


def test_list_season_follows_query_pages_in_phase_and_week_order(game_table: object) -> None:
    repository = DynamoGameRepository(game_table, query_page_size=2)
    expected = [
        make_game(
            "401000001",
            season_week=SeasonWeek(2026, SeasonPhase.PRESEASON, 1),
        ),
        make_game("401000002"),
        make_game(
            "401000003",
            season_week=SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 2),
        ),
        make_game(
            "401000004",
            season_week=SeasonWeek(2026, SeasonPhase.POSTSEASON, 1),
        ),
    ]

    for game in reversed(expected):
        assert repository.create_if_absent(game) is True

    assert repository.list_season(2026) == expected


def test_get_rejects_a_malformed_stored_item(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    game_table.put_item(Item={"game_id": "broken-game"})

    with pytest.raises(GameRepositoryDataError, match="schema_version"):
        repository.get(GameId("broken-game"))


def test_apply_schedule_changes_kickoff_and_week_query_order(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    first = make_game("401000001", kickoff_at=datetime(2026, 9, 10, 17, 0, tzinfo=UTC))
    corrected = make_game("401000002", kickoff_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC))
    assert repository.create_if_absent(first) is True
    assert repository.create_if_absent(corrected) is True

    current = repository.get(corrected.game_id)
    assert current is not None
    result = repository.apply_schedule(
        current,
        schedule_update(
            current,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            kickoff_at=datetime(2026, 9, 10, 16, 0, tzinfo=UTC),
        ),
    )

    assert result is WriteResult.APPLIED
    assert repository.get(corrected.game_id).kickoff_at == datetime(
        2026, 9, 10, 16, 0, tzinfo=UTC
    )
    assert [game.game_id for game in repository.list_week(first.season_week)] == [
        corrected.game_id,
        first.game_id,
    ]


def test_apply_schedule_rejects_a_write_that_lost_a_schedule_race(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000001")
    assert repository.create_if_absent(original) is True

    winner = schedule_update(
        original,
        observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        broadcaster="ESPN",
    )
    loser = schedule_update(
        original,
        observed_at=datetime(2026, 9, 10, 20, 0, tzinfo=UTC),
        broadcaster="ABC",
    )

    assert repository.apply_schedule(original, winner) is WriteResult.APPLIED
    assert repository.apply_schedule(original, loser) is WriteResult.STALE
    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.broadcaster == "ESPN"
    assert stored.schedule_checked_at == winner.observed_at


def test_apply_schedule_preserves_absent_values_and_removes_explicit_clears(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = replace(
        make_game("401000001"),
        nflverse_id="2026_01_SEA_DET",
        broadcaster="ESPN",
        odds=OddsSnapshot("SEA -3.5", CHECKED_AT),
        home=replace(make_team("home-401000001"), logo_key="sea-logo"),
    )
    assert repository.create_if_absent(original) is True

    assert repository.apply_schedule(
        original,
        schedule_update(
            original,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        ),
    ) is WriteResult.APPLIED
    after_absent = repository.get(original.game_id)
    assert after_absent is not None
    assert after_absent.home.logo_key == "sea-logo"
    assert after_absent.broadcaster == "ESPN"
    assert after_absent.odds == original.odds

    clear_home = TeamScheduleUpdate(
        team_id=after_absent.home.team_id,
        display_name=after_absent.home.display_name,
        abbreviation=after_absent.home.abbreviation,
        logo_key=None,
    )
    assert repository.apply_schedule(
        after_absent,
        schedule_update(
            after_absent,
            observed_at=datetime(2026, 9, 10, 20, 0, tzinfo=UTC),
            home=clear_home,
            broadcaster=None,
            odds=None,
        ),
    ) is WriteResult.APPLIED

    cleared = repository.get(original.game_id)
    assert cleared is not None
    assert cleared.home.logo_key is None
    assert cleared.broadcaster is None
    assert cleared.odds is None
    assert cleared.nflverse_id == "2026_01_SEA_DET"


def test_apply_schedule_can_postpone_a_scoreless_delayed_game(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    delayed = replace(
        make_game("401000001"),
        status=GameStatus(GameState.DELAYED),
    )
    assert repository.create_if_absent(delayed) is True

    assert repository.apply_schedule(
        delayed,
        schedule_update(
            delayed,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            kickoff_at=None,
            status=GameStatus(GameState.POSTPONED),
        ),
    ) is WriteResult.APPLIED

    stored = repository.get(delayed.game_id)
    assert stored is not None
    assert stored.status == GameStatus(GameState.POSTPONED)
    assert stored.kickoff_at is None
    assert stored.live_source_checked_at == datetime(2026, 9, 10, 19, 0, tzinfo=UTC)


@pytest.mark.parametrize("score", [Score(home=7, away=0), Score(home=0, away=0)])
def test_apply_schedule_rejects_scoreless_delay_after_play_started(
    game_table: object,
    score: Score,
) -> None:
    repository = DynamoGameRepository(game_table)
    started = replace(
        make_game("401000001"),
        status=GameStatus(GameState.IN_PROGRESS, period=1, score=score),
    )
    assert repository.create_if_absent(started) is True

    with pytest.raises(DomainValidationError, match="invalid status transition"):
        repository.apply_schedule(
            started,
            schedule_update(
                started,
                observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
                status=GameStatus(GameState.DELAYED),
            ),
        )

    assert repository.get(started.game_id) == started


@pytest.mark.parametrize("score", [Score(home=7, away=0), Score(home=0, away=0)])
def test_apply_live_status_rejects_scoreless_delay_after_play_started(
    game_table: object,
    score: Score,
) -> None:
    repository = DynamoGameRepository(game_table)
    started = replace(
        make_game("401000001"),
        status=GameStatus(GameState.IN_PROGRESS, period=1, score=score),
    )
    assert repository.create_if_absent(started) is True

    with pytest.raises(DomainValidationError, match="invalid status transition"):
        repository.apply_live_status(
            started,
            live_status_update(
                started,
                observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
                status=GameStatus(GameState.DELAYED),
            ),
        )

    assert repository.get(started.game_id) == started


def test_apply_live_status_advances_only_the_source_check_when_unchanged(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000001")
    assert repository.create_if_absent(original) is True

    assert repository.apply_live_status(
        original,
        live_status_update(
            original,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            status=original.status,
        ),
    ) is WriteResult.APPLIED

    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.status == original.status
    assert stored.live_source_checked_at == datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    assert stored.live_state_updated_at is None
    assert stored.schedule_checked_at == original.schedule_checked_at


def test_apply_live_status_rejects_a_write_that_lost_a_live_race(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000001")
    assert repository.create_if_absent(original) is True
    winner_status = GameStatus(
        GameState.IN_PROGRESS,
        period=1,
        clock="15:00",
        score=Score(home=7, away=0),
    )
    loser_status = GameStatus(
        GameState.IN_PROGRESS,
        period=1,
        clock="14:00",
        score=Score(home=0, away=0),
    )

    assert repository.apply_live_status(
        original,
        live_status_update(
            original,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            status=winner_status,
        ),
    ) is WriteResult.APPLIED
    assert repository.apply_live_status(
        original,
        live_status_update(
            original,
            observed_at=datetime(2026, 9, 10, 20, 0, tzinfo=UTC),
            status=loser_status,
        ),
    ) is WriteResult.STALE

    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.status == winner_status
    assert stored.live_source_checked_at == datetime(2026, 9, 10, 19, 0, tzinfo=UTC)


def test_apply_live_finalization_commits_status_and_records_together(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = replace(
        make_game("401000001"),
        nflverse_id="2026_01_SEA_DET",
        broadcaster="ESPN",
        odds=OddsSnapshot("SEA -3.5", CHECKED_AT),
        home=replace(make_team("home-401000001"), logo_key="home-logo"),
    )
    assert repository.create_if_absent(original) is True
    final_status = GameStatus(GameState.FINAL, score=Score(home=24, away=17))
    home_pregame = make_record(1, 0, snapshot_at=CHECKED_AT)
    home_postgame = make_record(2, 0, snapshot_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC))
    away_pregame = make_record(0, 1, snapshot_at=CHECKED_AT)
    away_postgame = make_record(0, 2, snapshot_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC))
    update = live_finalization_update(
        original,
        observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        status=final_status,
        home_pregame_record=home_pregame,
        home_postgame_record=home_postgame,
        away_pregame_record=away_pregame,
        away_postgame_record=away_postgame,
    )

    assert repository.apply_live_finalization(original, update) is WriteResult.APPLIED
    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.status == final_status
    assert stored.live_source_checked_at == update.observed_at
    assert stored.home.pregame_record == home_pregame
    assert stored.home.postgame_record == home_postgame
    assert stored.away.pregame_record == away_pregame
    assert stored.away.postgame_record == away_postgame
    assert stored.nflverse_id == original.nflverse_id
    assert stored.rating == original.rating
    assert stored.broadcaster == original.broadcaster
    assert stored.odds == original.odds

    assert repository.apply_live_finalization(original, update) is WriteResult.STALE


def test_live_finalization_rejects_team_ownership_mismatch(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000002")
    assert repository.create_if_absent(original) is True
    update = live_finalization_update(
        original,
        observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
        home_team_id="new-home",
    )

    assert repository.apply_live_finalization(original, update) is WriteResult.STALE
    assert repository.get(original.game_id) == original


def test_schedule_write_prepared_before_finalization_loses_status_race(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000003")
    assert repository.create_if_absent(original) is True
    final_status = GameStatus(GameState.FINAL, score=Score(home=24, away=17))
    home_postgame = make_record(
        2,
        0,
        snapshot_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
    )
    away_postgame = make_record(
        0,
        2,
        snapshot_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
    )
    stale_home = TeamScheduleUpdate(
        team_id=original.home.team_id,
        display_name=original.home.display_name,
        abbreviation=original.home.abbreviation,
        pregame_record=None,
        postgame_record=None,
    )
    stale_away = TeamScheduleUpdate(
        team_id=original.away.team_id,
        display_name=original.away.display_name,
        abbreviation=original.away.abbreviation,
        pregame_record=None,
        postgame_record=None,
    )
    stale_schedule = schedule_update(
        original,
        observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        home=stale_home,
        away=stale_away,
        broadcaster="stale schedule",
    )
    final_update = live_finalization_update(
        original,
        observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        status=final_status,
        home_postgame_record=home_postgame,
        away_postgame_record=away_postgame,
    )

    assert repository.apply_live_finalization(original, final_update) is WriteResult.APPLIED
    assert repository.apply_schedule(original, stale_schedule) is WriteResult.STALE
    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.status == final_status
    assert stored.home.postgame_record == home_postgame
    assert stored.away.postgame_record == away_postgame


def test_finalization_prepared_before_schedule_loses_schedule_race(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000004")
    assert repository.create_if_absent(original) is True
    stale_final = live_finalization_update(
        original,
        observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
    )
    schedule = schedule_update(
        original,
        observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
        broadcaster="new schedule",
    )

    assert repository.apply_schedule(original, schedule) is WriteResult.APPLIED
    assert repository.apply_live_finalization(original, stale_final) is WriteResult.STALE
    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.status.state is GameState.SCHEDULED
    assert stored.broadcaster == "new schedule"


def test_real_service_retries_after_precommit_transport_failure(
    game_table: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_game("401000005")
    assert repository.create_if_absent(current) is True
    observed_at = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    batch = live_observation(current, observed_at=observed_at)

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_scoreboard(self, season_week: SeasonWeek | None = None) -> ScoreboardBatch:
            self.calls += 1
            return batch

    provider = Provider()
    original_update_item = game_table.update_item
    failed = False

    def fail_before_commit(**kwargs: object) -> object:
        nonlocal failed
        if not failed:
            failed = True
            raise EndpointConnectionError(endpoint_url="injected-before-finalization")
        return original_update_item(**kwargs)

    monkeypatch.setattr(game_table, "update_item", fail_before_commit)
    service = ScheduleSyncService(repository, provider)
    with pytest.raises(GameRepositoryError):
        service.run(
            SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
            now=observed_at,
        )

    after_failure = repository.get(current.game_id)
    assert after_failure is not None
    assert after_failure.status.state is GameState.SCHEDULED
    assert after_failure.home.postgame_record is None

    monkeypatch.setattr(game_table, "update_item", original_update_item)
    result = service.run(
        SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
        now=observed_at,
    )
    repaired = repository.get(current.game_id)
    assert repaired is not None
    assert repaired.status.state is GameState.FINAL
    assert repaired.home.postgame_record is not None
    assert repaired.away.postgame_record is not None
    assert result.provisional_rating_game_ids == (current.game_id,)
    assert provider.calls == 2


def test_real_service_postcommit_failure_leaves_records_and_handoff(
    game_table: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_game("401000006")
    assert repository.create_if_absent(current) is True
    observed_at = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    batch = live_observation(current, observed_at=observed_at)

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_scoreboard(self, season_week: SeasonWeek | None = None) -> ScoreboardBatch:
            self.calls += 1
            return batch

    provider = Provider()
    original_update_item = game_table.update_item
    update_count = 0

    def fail_after_commit(**kwargs: object) -> object:
        nonlocal update_count
        update_count += 1
        if update_count == 2:
            raise EndpointConnectionError(endpoint_url="injected-after-finalization")
        return original_update_item(**kwargs)

    monkeypatch.setattr(game_table, "update_item", fail_after_commit)
    service = ScheduleSyncService(repository, provider)
    with pytest.raises(GameRepositoryError):
        service.run(
            SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
            now=observed_at,
        )

    committed = repository.get(current.game_id)
    assert committed is not None
    assert committed.status.state is GameState.FINAL
    assert committed.home.postgame_record is not None
    assert committed.away.postgame_record is not None

    monkeypatch.setattr(game_table, "update_item", original_update_item)
    provider.calls = 0
    result = service.run(
        SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
        now=observed_at,
    )
    assert provider.calls == 0
    assert result.provisional_rating_game_ids == (current.game_id,)


def test_real_service_keeps_postseason_records_static(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    postseason = SeasonWeek(2026, SeasonPhase.POSTSEASON, 1)
    pregame = make_record(12, 5, snapshot_at=CHECKED_AT)
    current = replace(
        make_game("401000007", season_week=postseason),
        home=replace(make_team("home-401000007"), pregame_record=pregame),
        away=replace(make_team("away-401000007"), pregame_record=make_record(9, 8, snapshot_at=CHECKED_AT)),
    )
    assert repository.create_if_absent(current) is True
    observed_at = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    batch = live_observation(current, observed_at=observed_at)

    class Provider:
        def fetch_scoreboard(self, season_week: SeasonWeek | None = None) -> ScoreboardBatch:
            return batch

    result = ScheduleSyncService(repository, Provider()).run(
        SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
        now=observed_at,
    )

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.status.state is GameState.FINAL
    assert stored.home.pregame_record == pregame
    assert stored.home.postgame_record is None
    assert stored.away.postgame_record is None
    assert result.provisional_rating_game_ids == (current.game_id,)


def test_real_service_derives_regular_postgame_records_when_source_omits_records(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    observed_at = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    home_pregame = make_record(1, 0, snapshot_at=CHECKED_AT)
    away_pregame = make_record(0, 1, snapshot_at=CHECKED_AT)
    current = replace(
        make_game("401000008"),
        home=replace(make_team("home-401000008"), pregame_record=home_pregame),
        away=replace(make_team("away-401000008"), pregame_record=away_pregame),
    )
    assert repository.create_if_absent(current) is True
    batch = live_observation(
        current,
        observed_at=observed_at,
        include_records=False,
    )

    class Provider:
        def fetch_scoreboard(self, season_week: SeasonWeek | None = None) -> ScoreboardBatch:
            return batch

    result = ScheduleSyncService(repository, Provider()).run(
        SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
        now=observed_at,
    )

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.status.state is GameState.FINAL
    assert stored.home.pregame_record == home_pregame
    assert stored.away.pregame_record == away_pregame
    assert stored.home.postgame_record is not None
    assert stored.home.postgame_record.record == TeamRecord(2, 0)
    assert stored.home.postgame_record.snapshot_at == observed_at
    assert stored.away.postgame_record is not None
    assert stored.away.postgame_record.record == TeamRecord(0, 2)
    assert stored.away.postgame_record.snapshot_at == observed_at
    assert result.provisional_rating_game_ids == (current.game_id,)


def test_nonfinal_live_reread_preserves_concurrent_final_records_and_rating(
    game_table: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_game("401000099")
    assert repository.create_if_absent(current) is True
    observed_at = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    batch = live_observation(
        current,
        observed_at=observed_at,
        include_records=False,
    )
    batch = replace(
        batch,
        games=(
            replace(
                batch.games[0],
                status=GameStatus(
                    GameState.IN_PROGRESS,
                    score=Score(home=0, away=0),
                    period=1,
                ),
            ),
        ),
    )
    final_update = live_finalization_update(
        current,
        observed_at=observed_at,
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
        home_pregame_record=make_record(1, 0, snapshot_at=observed_at),
        home_postgame_record=make_record(2, 0, snapshot_at=observed_at),
        away_pregame_record=make_record(0, 1, snapshot_at=observed_at),
        away_postgame_record=make_record(0, 2, snapshot_at=observed_at),
    )
    real_apply = repository.apply_live_status

    def finalize_then_apply(snapshot: Game, update: LiveStatusUpdate) -> WriteResult:
        assert repository.apply_live_finalization(snapshot, final_update) is WriteResult.APPLIED
        return real_apply(snapshot, update)

    monkeypatch.setattr(repository, "apply_live_status", finalize_then_apply)

    class Provider:
        def fetch_scoreboard(
            self, season_week: SeasonWeek | None = None
        ) -> ScoreboardBatch:
            return batch

    ScheduleSyncService(repository, Provider()).run(
        SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
        now=observed_at,
    )

    saved = repository.get(current.game_id)
    assert saved is not None
    assert saved.status == final_update.status
    assert saved.home.pregame_record == final_update.home_pregame_record
    assert saved.home.postgame_record == final_update.home_postgame_record
    assert saved.away.pregame_record == final_update.away_pregame_record
    assert saved.away.postgame_record == final_update.away_postgame_record
    assert saved.rating == current.rating


def test_finalization_followup_preserves_records_after_side_correction(
    game_table: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DynamoGameRepository(game_table)
    observed_at = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    correction_at = observed_at - timedelta(minutes=30)
    home_pregame = make_record(1, 0, snapshot_at=CHECKED_AT)
    away_pregame = make_record(0, 1, snapshot_at=CHECKED_AT)
    current = replace(
        make_game("401000100"),
        home=replace(make_team("home-401000100"), pregame_record=home_pregame),
        away=replace(make_team("away-401000100"), pregame_record=away_pregame),
    )
    assert repository.create_if_absent(current) is True
    batch = live_observation(
        current,
        observed_at=observed_at,
        include_records=False,
    )
    real_apply = repository.apply_live_finalization

    def finalize_then_correct(snapshot: Game, update: LiveFinalizationUpdate) -> WriteResult:
        result = real_apply(snapshot, update)
        assert result is WriteResult.APPLIED
        finalized = repository.get(current.game_id)
        assert finalized is not None
        assert repository.apply_schedule(
            finalized,
            swapped_sides_update(finalized, observed_at=correction_at),
        ) is WriteResult.APPLIED
        return result

    monkeypatch.setattr(repository, "apply_live_finalization", finalize_then_correct)

    class Provider:
        def fetch_scoreboard(
            self, season_week: SeasonWeek | None = None
        ) -> ScoreboardBatch:
            return batch

    ScheduleSyncService(repository, Provider()).run(
        SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
        now=observed_at,
    )

    saved = repository.get(current.game_id)
    assert saved is not None
    assert saved.status.state is GameState.FINAL
    assert saved.home.pregame_record == home_pregame
    assert saved.home.postgame_record == make_record(2, 0, snapshot_at=observed_at)
    assert saved.away.pregame_record == away_pregame
    assert saved.away.postgame_record == make_record(0, 2, snapshot_at=observed_at)
    assert saved.rating == current.rating


def test_stale_nonfinal_followup_preserves_present_records_after_side_correction(
    game_table: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_game("401000101")
    assert repository.create_if_absent(current) is True
    observed_at = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
    correction_at = observed_at - timedelta(minutes=30)
    batch = live_observation(current, observed_at=observed_at, include_records=True)
    batch = replace(
        batch,
        games=(
            replace(
                batch.games[0],
                status=GameStatus(
                    GameState.IN_PROGRESS,
                    score=Score(home=0, away=0),
                    period=1,
                ),
            ),
        ),
    )
    final_update = live_finalization_update(
        current,
        observed_at=observed_at,
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
        home_pregame_record=make_record(1, 0, snapshot_at=observed_at),
        home_postgame_record=make_record(2, 0, snapshot_at=observed_at),
        away_pregame_record=make_record(0, 1, snapshot_at=observed_at),
        away_postgame_record=make_record(0, 2, snapshot_at=observed_at),
    )
    real_apply = repository.apply_live_status

    def finalize_then_correct(snapshot: Game, update: LiveStatusUpdate) -> WriteResult:
        assert repository.apply_live_finalization(snapshot, final_update) is WriteResult.APPLIED
        finalized = repository.get(current.game_id)
        assert finalized is not None
        assert repository.apply_schedule(
            finalized,
            swapped_sides_update(finalized, observed_at=correction_at),
        ) is WriteResult.APPLIED
        return real_apply(snapshot, update)

    monkeypatch.setattr(repository, "apply_live_status", finalize_then_correct)

    class Provider:
        def fetch_scoreboard(
            self, season_week: SeasonWeek | None = None
        ) -> ScoreboardBatch:
            return batch

    ScheduleSyncService(repository, Provider()).run(
        SyncEvent(SyncMode.LIVE_TICK, observed_at, current.season_week.season),
        now=observed_at,
    )

    saved = repository.get(current.game_id)
    assert saved is not None
    assert saved.status.state is GameState.FINAL
    assert saved.home.pregame_record == final_update.home_pregame_record
    assert saved.home.postgame_record == final_update.home_postgame_record
    assert saved.away.pregame_record == final_update.away_pregame_record
    assert saved.away.postgame_record == final_update.away_postgame_record
    assert saved.rating == current.rating


def test_apply_live_status_cannot_replace_a_schedule_status_write(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = make_game("401000001")
    assert repository.create_if_absent(original) is True

    assert repository.apply_schedule(
        original,
        schedule_update(
            original,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            status=GameStatus(GameState.DELAYED),
        ),
    ) is WriteResult.APPLIED
    assert repository.apply_live_status(
        original,
        live_status_update(
            original,
            observed_at=datetime(2026, 9, 10, 20, 0, tzinfo=UTC),
            status=GameStatus(
                GameState.IN_PROGRESS,
                period=1,
                score=Score(home=0, away=0),
            ),
        ),
    ) is WriteResult.STALE

    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.status == GameStatus(GameState.DELAYED)
    assert stored.live_source_checked_at == datetime(2026, 9, 10, 19, 0, tzinfo=UTC)


def test_apply_live_status_rejects_a_final_game_regression(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    completed = replace(
        make_game("401000001"),
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
    )
    assert repository.create_if_absent(completed) is True

    with pytest.raises(DomainValidationError, match="invalid status transition"):
        repository.apply_live_status(
            completed,
            live_status_update(
                completed,
                observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
                status=GameStatus(
                    GameState.IN_PROGRESS,
                    period=4,
                    score=Score(home=24, away=17),
                ),
            ),
        )


def test_apply_live_status_cannot_finalize_without_records(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_game("401000001")
    assert repository.create_if_absent(current) is True

    with pytest.raises(DomainValidationError, match="apply_live_finalization"):
        repository.apply_live_status(
            current,
            live_status_update(
                current,
                observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
                status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
            ),
        )

    assert repository.get(current.game_id) == current


def test_apply_live_status_preserves_a_confirmed_rating_and_nflverse_id(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    confirmed = replace(
        make_game("401000001"),
        nflverse_id="2026_01_SEA_DET",
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
        rating=GameRating(
            state=RatingState.CONFIRMED,
            retry=RatingRetry(),
            score=7.5,
            source=RatingSource.NFLVERSE,
            model_version="rating-v1",
            input_hash="input-hash",
            calculated_at=CHECKED_AT,
            confirmed_at=CHECKED_AT,
        ),
    )
    assert repository.create_if_absent(confirmed) is True

    assert repository.apply_live_status(
        confirmed,
        live_status_update(
            confirmed,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            status=confirmed.status,
        ),
    ) is WriteResult.APPLIED

    stored = repository.get(confirmed.game_id)
    assert stored is not None
    assert stored.rating == confirmed.rating
    assert stored.nflverse_id == confirmed.nflverse_id


def test_apply_schedule_keeps_newer_nested_records_and_odds(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    current_home = replace(
        make_team("home-401000001"),
        pregame_record=make_record(
            4,
            2,
            snapshot_at=datetime(2026, 9, 10, 18, 30, tzinfo=UTC),
        ),
        postgame_record=make_record(
            5,
            2,
            snapshot_at=datetime(2026, 9, 10, 18, 45, tzinfo=UTC),
        ),
    )
    current = replace(
        make_game("401000001"),
        status=GameStatus(GameState.FINAL, score=Score(home=24, away=17)),
        home=current_home,
        odds=OddsSnapshot(
            "SEA -3.5",
            datetime(2026, 9, 10, 18, 45, tzinfo=UTC),
        ),
    )
    assert repository.create_if_absent(current) is True

    older_home = TeamScheduleUpdate(
        team_id=current_home.team_id,
        display_name=current_home.display_name,
        abbreviation=current_home.abbreviation,
        pregame_record=make_record(
            3,
            2,
            snapshot_at=datetime(2026, 9, 10, 18, 0, tzinfo=UTC),
        ),
        postgame_record=make_record(
            4,
            2,
            snapshot_at=datetime(2026, 9, 10, 18, 15, tzinfo=UTC),
        ),
    )
    assert repository.apply_schedule(
        current,
        schedule_update(
            current,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            home=older_home,
            odds=OddsSnapshot(
                "SEA -1.5",
                datetime(2026, 9, 10, 18, 0, tzinfo=UTC),
            ),
        ),
    ) is WriteResult.APPLIED

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.home.pregame_record == current_home.pregame_record
    assert stored.home.postgame_record == current_home.postgame_record
    assert stored.odds == current.odds


def test_apply_schedule_clears_unsupplied_prepared_data_after_team_correction(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    original = replace(
        make_game("401000001"),
        home=replace(
            make_team("home-401000001"),
            logo_key="old-team-logo",
            pregame_record=make_record(
                4,
                2,
                snapshot_at=datetime(2026, 9, 10, 18, 0, tzinfo=UTC),
            ),
        ),
    )
    assert repository.create_if_absent(original) is True
    corrected_home = TeamScheduleUpdate(
        team_id="corrected-home",
        display_name="Corrected home team",
        abbreviation="COR",
    )

    assert repository.apply_schedule(
        original,
        schedule_update(
            original,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            home=corrected_home,
        ),
    ) is WriteResult.APPLIED

    stored = repository.get(original.game_id)
    assert stored is not None
    assert stored.home.team_id == "corrected-home"
    assert stored.home.logo_key is None
    assert stored.home.pregame_record is None
    assert stored.home.postgame_record is None


def test_apply_rating_replaces_only_the_rating_map(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    current = replace(
        make_final_game("401000001"),
        nflverse_id="2026_01_SEA_DET",
        broadcaster="ESPN",
        odds=OddsSnapshot("SEA -3.5", CHECKED_AT),
    )
    rating = make_provisional_rating()
    assert repository.create_if_absent(current) is True

    assert repository.apply_rating(current, rating) is WriteResult.APPLIED

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.rating == rating
    assert replace(stored, rating=current.rating) == current


def test_apply_rating_rejects_a_confirmed_to_provisional_replacement(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    confirmed = replace(
        make_final_game("401000001"),
        rating=GameRating(
            state=RatingState.CONFIRMED,
            retry=RatingRetry(),
            score=7.5,
            source=RatingSource.NFLVERSE,
            model_version="rating-v1",
            input_hash="input-hash",
            calculated_at=CHECKED_AT,
            confirmed_at=CHECKED_AT,
        ),
    )
    assert repository.create_if_absent(confirmed) is True

    with pytest.raises(DomainValidationError, match="invalid rating transition"):
        repository.apply_rating(confirmed, make_provisional_rating())

    assert repository.get(confirmed.game_id) == confirmed


def test_apply_rating_rejects_a_writer_that_lost_a_rating_race(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_final_game("401000001")
    winner = make_provisional_rating(score=7.5)
    loser = make_provisional_rating(score=5.0)
    assert repository.create_if_absent(current) is True

    assert repository.apply_rating(current, winner) is WriteResult.APPLIED
    assert repository.apply_rating(current, loser) is WriteResult.STALE

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.rating == winner


def test_apply_rating_rejects_a_final_score_correction_after_the_read(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_final_game("401000001")
    assert repository.create_if_absent(current) is True

    assert repository.apply_live_status(
        current,
        live_status_update(
            current,
            observed_at=datetime(2026, 9, 10, 19, 0, tzinfo=UTC),
            status=GameStatus(GameState.FINAL, score=Score(home=24, away=20)),
        ),
    ) is WriteResult.APPLIED
    assert repository.apply_rating(current, make_provisional_rating()) is WriteResult.STALE

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.status.score == Score(home=24, away=20)
    assert stored.rating == current.rating


def test_apply_rating_updates_pending_retry_metadata_only(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_final_game("401000001")
    retry_rating = GameRating(
        state=RatingState.PENDING,
        retry=RatingRetry(
            attempt_count=1,
            next_attempt_at=datetime(2026, 9, 10, 19, 5, tzinfo=UTC),
            last_error="source was not ready",
        ),
    )
    assert repository.create_if_absent(current) is True

    assert repository.apply_rating(current, retry_rating) is WriteResult.APPLIED

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.rating == retry_rating
    assert replace(stored, rating=current.rating) == current


def test_apply_rating_rejects_a_non_final_game_before_updating_it(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_game("401000001")
    assert repository.create_if_absent(current) is True

    with pytest.raises(DomainValidationError, match="rating updates require a final game"):
        repository.apply_rating(current, GameRating(RatingState.PENDING, RatingRetry()))

    assert repository.get(current.game_id) == current


def test_apply_nflverse_mapping_is_safe_and_idempotent(game_table: object) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_final_game("401000001")
    assert repository.create_if_absent(current) is True

    assert repository.apply_nflverse_mapping(current, "2026_01_SEA_DET") is WriteResult.APPLIED
    mapped = replace(current, nflverse_id="2026_01_SEA_DET")
    assert repository.apply_nflverse_mapping(mapped, "2026_01_SEA_DET") is WriteResult.STALE

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.nflverse_id == "2026_01_SEA_DET"
    assert stored.rating == current.rating


def test_apply_nflverse_mapping_rejects_a_conflicting_id_without_overwrite(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = replace(make_final_game("401000001"), nflverse_id="2026_01_SEA_DET")
    assert repository.create_if_absent(current) is True

    with pytest.raises(NflverseIdConflictError):
        repository.apply_nflverse_mapping(current, "2026_01_XXX_YYY")

    assert repository.get(current.game_id) == current


def test_apply_nflverse_mapping_resolves_a_lost_write_race_with_a_strong_read(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_final_game("401000001")
    winner = replace(current, nflverse_id="2026_01_SEA_DET")
    assert repository.create_if_absent(current) is True

    assert repository.apply_nflverse_mapping(current, winner.nflverse_id or "") is WriteResult.APPLIED
    with pytest.raises(NflverseIdConflictError):
        repository.apply_nflverse_mapping(current, "2026_01_XXX_YYY")

    assert repository.get(current.game_id) == winner


def test_confirmation_retry_is_independent_and_conditionally_stale(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_final_game("401000001")
    retry = RatingRetry(
        attempt_count=1,
        next_attempt_at=CHECKED_AT.replace(hour=19),
        last_error="nflverse play data not published",
    )
    assert repository.create_if_absent(current) is True

    assert repository.apply_confirmation_retry(current, retry) is WriteResult.APPLIED
    assert repository.apply_confirmation_retry(current, RatingRetry()) is WriteResult.STALE

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.confirmation_retry == retry
    assert stored.rating == current.rating


def test_confirmation_retry_cas_does_not_treat_missing_nondefault_as_default(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = make_final_game("401000001")
    first_retry = RatingRetry(
        attempt_count=1,
        next_attempt_at=CHECKED_AT.replace(hour=19),
        last_error="nflverse play data not published",
    )
    next_retry = RatingRetry(
        attempt_count=2,
        next_attempt_at=CHECKED_AT.replace(hour=20),
        last_error="nflverse play data not published",
    )
    assert repository.create_if_absent(current) is True
    assert repository.apply_confirmation_retry(current, first_retry) is WriteResult.APPLIED

    game_table.update_item(
        Key={"game_id": str(current.game_id)},
        UpdateExpression="REMOVE confirmation_retry",
    )

    retrying = replace(current, confirmation_retry=first_retry)
    assert repository.apply_confirmation_retry(retrying, next_retry) is WriteResult.STALE

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.confirmation_retry == RatingRetry()
    assert stored.rating == current.rating


def test_unavailable_rating_can_be_confirmed_and_clears_confirmation_retry(
    game_table: object,
) -> None:
    repository = DynamoGameRepository(game_table)
    current = replace(make_final_game("401000001"), rating=make_unavailable_rating())
    assert repository.create_if_absent(current) is True
    assert repository.apply_nflverse_mapping(current, "2026_01_SEA_DET") is WriteResult.APPLIED
    mapped = replace(current, nflverse_id="2026_01_SEA_DET")
    retry = RatingRetry(
        attempt_count=1,
        next_attempt_at=CHECKED_AT.replace(hour=19),
        last_error="nflverse play data not published",
    )
    assert repository.apply_confirmation_retry(mapped, retry) is WriteResult.APPLIED
    retrying = replace(mapped, confirmation_retry=retry)

    confirmed = make_confirmed_rating()
    assert repository.apply_confirmed_rating(retrying, confirmed) is WriteResult.APPLIED

    stored = repository.get(current.game_id)
    assert stored is not None
    assert stored.rating == confirmed
    assert stored.confirmation_retry == RatingRetry()
    assert stored.nflverse_id == "2026_01_SEA_DET"


def test_get_wraps_a_dynamodb_transport_failure() -> None:
    class UnavailableTable:
        def get_item(self, **kwargs: object) -> object:
            raise EndpointConnectionError(endpoint_url="http://127.0.0.1:8000")

    repository = DynamoGameRepository(UnavailableTable())

    with pytest.raises(GameRepositoryError, match="could not read game"):
        repository.get(GameId("401000001"))
