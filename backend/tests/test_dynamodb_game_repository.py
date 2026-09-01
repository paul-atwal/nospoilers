from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
import os
import subprocess
import time
from uuid import uuid4

import boto3
from botocore.exceptions import EndpointConnectionError
import pytest

from backend.nospoil_nfl.game import (
    DomainValidationError,
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
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
)


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


def test_get_wraps_a_dynamodb_transport_failure() -> None:
    class UnavailableTable:
        def get_item(self, **kwargs: object) -> object:
            raise EndpointConnectionError(endpoint_url="http://127.0.0.1:8000")

    repository = DynamoGameRepository(UnavailableTable())

    with pytest.raises(GameRepositoryError, match="could not read game"):
        repository.get(GameId("401000001"))
