"""Create and seed the disposable DynamoDB Local table used by browser QA."""

from __future__ import annotations

from datetime import UTC, datetime
import os

import boto3
from botocore.exceptions import ClientError

from backend.nospoil_nfl.game import (
    Game, GameId, GameRating, GameState, GameStatus, OddsSnapshot,
    RatingRetry, RatingSource, RatingState, RecordScope, RecordSnapshot,
    Score, SeasonPhase, SeasonWeek, TeamGameSnapshot, TeamRecord,
)
from backend.nospoil_nfl.game.dynamodb_repository import DynamoGameRepository


ENDPOINT = os.environ.get("NOSPOIL_DYNAMODB_LOCAL_ENDPOINT", "http://127.0.0.1:8000")
TABLE_NAME = os.environ.get("NOSPOIL_GAMES_TABLE", "nospoil-games")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
OBSERVED_AT = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
WEEK = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1)


def resource():
    return boto3.resource(
        "dynamodb", endpoint_url=ENDPOINT, region_name=REGION,
        aws_access_key_id="local", aws_secret_access_key="local",
    )


def ensure_table(dynamodb):
    table = dynamodb.Table(TABLE_NAME)
    try:
        table.load()
        return table
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "game_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "game_id", "AttributeType": "S"},
            {"AttributeName": "season_key", "AttributeType": "S"},
            {"AttributeName": "schedule_key", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "season-schedule-index",
            "KeySchema": [
                {"AttributeName": "season_key", "KeyType": "HASH"},
                {"AttributeName": "schedule_key", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
            "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        }],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    table.wait_until_exists()
    return table


def record(wins: int, losses: int, at: datetime) -> RecordSnapshot:
    return RecordSnapshot(TeamRecord(wins, losses), RecordScope.REGULAR_SEASON, at)


def team(team_id: str, name: str, abbreviation: str) -> TeamGameSnapshot:
    return TeamGameSnapshot(
        team_id=team_id, display_name=name, abbreviation=abbreviation, logo_key=team_id,
        pregame_record=record(0, 0, OBSERVED_AT),
    )


def games() -> tuple[Game, ...]:
    scheduled = Game(
        game_id=GameId("browser-local-scheduled"), espn_id="browser-local-scheduled",
        nflverse_id=None, season_week=WEEK,
        kickoff_at=datetime(2026, 9, 11, 0, 20, tzinfo=UTC),
        home=team("26", "Seattle Seahawks", "SEA"),
        away=team("17", "New England Patriots", "NE"),
        status=GameStatus(GameState.SCHEDULED, detail="Scheduled"),
        rating=GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=OBSERVED_AT, schedule_updated_at=OBSERVED_AT,
        broadcaster="NBC", odds=OddsSnapshot("SEA -3.5", OBSERVED_AT),
    )
    final_at = datetime(2026, 9, 8, 19, 0, tzinfo=UTC)
    final = Game(
        game_id=GameId("browser-local-final"), espn_id="browser-local-final",
        nflverse_id="2026_01_NE_SEA", season_week=WEEK,
        kickoff_at=datetime(2026, 9, 7, 0, 20, tzinfo=UTC),
        home=TeamGameSnapshot(
            team_id="26", display_name="Seattle Seahawks", abbreviation="SEA", logo_key="26",
            pregame_record=record(0, 0, OBSERVED_AT), postgame_record=record(1, 0, final_at),
        ),
        away=TeamGameSnapshot(
            team_id="17", display_name="New England Patriots", abbreviation="NE", logo_key="17",
            pregame_record=record(0, 0, OBSERVED_AT), postgame_record=record(0, 1, final_at),
        ),
        status=GameStatus(GameState.FINAL, detail="Final", score=Score(home=24, away=17)),
        rating=GameRating(
            RatingState.CONFIRMED, RatingRetry(), score=8.4, source=RatingSource.NFLVERSE,
            model_version="rating-v1", input_hash="browser-local-fixture",
            calculated_at=final_at, confirmed_at=final_at,
        ),
        schedule_checked_at=final_at, schedule_updated_at=OBSERVED_AT,
        live_source_checked_at=final_at, live_state_updated_at=final_at,
        broadcaster="NBC",
    )
    return scheduled, final


if __name__ == "__main__":
    repository = DynamoGameRepository(ensure_table(resource()))
    created = sum(repository.create_if_absent(game) for game in games())
    print(f"seeded {created} new games; week now has {len(repository.list_week(WEEK))} games")
