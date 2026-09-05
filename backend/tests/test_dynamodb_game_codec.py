from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from backend.nospoil_nfl.game import (
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    RatingRetry,
    RatingState,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
)
from backend.nospoil_nfl.game.dynamodb_codec import _DynamoGameCodec
from backend.nospoil_nfl.game.repository import GameRepositoryDataError


CHECKED_AT = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


def make_team(team_id: str) -> TeamGameSnapshot:
    return TeamGameSnapshot(
        team_id=team_id,
        display_name=f"Team {team_id}",
        abbreviation=team_id.upper(),
        logo_key=None,
        pregame_record=None,
    )


def make_game(
    game_id: str,
    *,
    kickoff_at: datetime | None = CHECKED_AT,
) -> Game:
    state = GameState.SCHEDULED if kickoff_at is not None else GameState.POSTPONED
    return Game(
        game_id=GameId(game_id),
        espn_id=game_id,
        nflverse_id=None,
        season_week=SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1),
        kickoff_at=kickoff_at,
        home=make_team(f"home-{game_id}"),
        away=make_team(f"away-{game_id}"),
        status=GameStatus(state=state),
        rating=GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=CHECKED_AT,
        schedule_updated_at=CHECKED_AT,
    )


def test_codec_preserves_unknown_pregame_records() -> None:
    game = make_game("401000001")
    codec = _DynamoGameCodec()

    item = codec.encode(game)

    assert "pregame_record" not in item["home"]
    assert "pregame_record" not in item["away"]
    assert codec.decode(item) == game


def test_codec_defaults_confirmation_retry_for_existing_items() -> None:
    game = make_game("401000001")
    codec = _DynamoGameCodec()
    item = codec.encode(game)
    del item["confirmation_retry"]

    decoded = codec.decode(item)

    assert decoded.confirmation_retry == RatingRetry()
    assert decoded == game


def test_codec_uses_kickoff_then_game_id_for_schedule_sorting() -> None:
    codec = _DynamoGameCodec()
    games = [
        make_game("401000003", kickoff_at=CHECKED_AT),
        make_game("401000002", kickoff_at=CHECKED_AT),
        make_game("401000001", kickoff_at=datetime(2026, 9, 10, 17, 0, tzinfo=UTC)),
        make_game("401000004", kickoff_at=None),
    ]

    items = [codec.encode(game) for game in games]

    assert [item["game_id"] for item in sorted(items, key=lambda item: item["schedule_key"])] == [
        "401000001",
        "401000002",
        "401000003",
        "401000004",
    ]
    assert "kickoff_at" not in items[-1]


def test_codec_rejects_a_non_espn_primary_game_id() -> None:
    codec = _DynamoGameCodec()
    game = replace(make_game("401000001"), game_id=GameId("internal-game-id"))

    with pytest.raises(GameRepositoryDataError, match="game_id must match espn_id"):
        codec.encode(game)


def test_codec_rejects_an_index_key_that_no_longer_matches_the_game() -> None:
    codec = _DynamoGameCodec()
    item = codec.encode(make_game("401000001"))
    item["schedule_key"] = "1#01#9999-12-31T23:59:59.999999Z#401000001"

    with pytest.raises(GameRepositoryDataError, match="schedule_key does not match"):
        codec.decode(item)
