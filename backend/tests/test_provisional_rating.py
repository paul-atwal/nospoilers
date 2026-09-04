from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.nospoil_nfl.game import (
    Game,
    GameId,
    GameRating,
    GameState,
    GameStatus,
    RatingRetry,
    RatingSource,
    RatingState,
    Score,
    SeasonPhase,
    SeasonWeek,
    TeamGameSnapshot,
    WriteResult,
)
from backend.nospoil_nfl.providers import (
    GameSummary,
    ProviderUnavailableError,
)
from backend.nospoil_nfl.rating import RatingInput, hash_rating_input
from backend.nospoil_nfl.rating.provisional import ProvisionalRatingService


NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self, games: tuple[Game, ...]) -> None:
        self.games = {game.game_id: game for game in games}
        self.get_calls: list[GameId] = []
        self.applied: list[tuple[GameId, GameRating]] = []
        self.stale = False

    def get(self, game_id: GameId) -> Game | None:
        self.get_calls.append(game_id)
        return self.games.get(game_id)

    def apply_rating(self, current: Game, rating: GameRating) -> WriteResult:
        if self.stale:
            return WriteResult.STALE
        if self.games[current.game_id] != current:
            return WriteResult.STALE
        self.games[current.game_id] = replace(current, rating=rating)
        self.applied.append((current.game_id, rating))
        return WriteResult.APPLIED


class FakeSummaryProvider:
    def __init__(self, responses: object | list[object]) -> None:
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls: list[GameId] = []

    def fetch_summary(self, game_id: GameId) -> GameSummary:
        self.calls.append(game_id)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def make_game(
    game_id: str,
    *,
    final_at: datetime = NOW - timedelta(minutes=5),
    rating: GameRating | None = None,
) -> Game:
    return Game(
        game_id=GameId(game_id),
        espn_id=f"espn-{game_id}",
        nflverse_id=None,
        season_week=SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1),
        kickoff_at=final_at - timedelta(hours=3),
        home=TeamGameSnapshot("home", "Home", "H", None, None),
        away=TeamGameSnapshot("away", "Away", "A", None, None),
        status=GameStatus(GameState.FINAL, score=Score(24, 17)),
        rating=rating or GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=final_at,
        schedule_updated_at=final_at,
        live_state_updated_at=final_at,
        live_source_checked_at=final_at,
    )


def summary(game: Game) -> GameSummary:
    return GameSummary(
        game_id=GameId(game.espn_id),
        win_probability_history=(0.5, 0.7, 0.4),
        final_score=game.status.score,  # type: ignore[arg-type]
        is_overtime=False,
    )


def test_hash_rating_input_is_canonical() -> None:
    rating_input = RatingInput((0.5, 0.7, 0.4), True)

    assert hash_rating_input(rating_input) == (
        "eb57b6f60bdf6adb81b4c574aab57509d3f2a89c6ba5cab42b337263704c4557"
    )


def test_success_reads_current_game_and_stores_provisional_rating() -> None:
    game = make_game("one")
    repository = FakeRepository((game,))
    provider = FakeSummaryProvider(summary(game))

    ProvisionalRatingService(repository, provider).rate_due(
        [GameId("one")], now=NOW
    )

    assert provider.calls == [GameId("espn-one")]
    stored = repository.games[GameId("one")].rating
    assert stored.state is RatingState.PROVISIONAL
    assert stored.source is RatingSource.ESPN
    assert stored.model_version == "rating-v1"
    assert stored.input_hash == hash_rating_input(RatingInput((0.5, 0.7, 0.4), True))
    assert stored.calculated_at == NOW
    assert stored.retry == RatingRetry()


def test_retry_policy_is_bounded_and_exhausts_on_fourth_failure() -> None:
    game = make_game("one")
    repository = FakeRepository((game,))
    failure = ProviderUnavailableError(
        "temporary", provider="espn", operation="summary"
    )
    provider = FakeSummaryProvider([failure, failure, failure, failure])
    service = ProvisionalRatingService(repository, provider)

    for attempt, delay in enumerate((0, 1, 4, 14), start=1):
        service.rate_due(
            [GameId("one")], now=NOW + timedelta(minutes=delay)
        )
        stored = repository.games[GameId("one")].rating
        if attempt < 4:
            assert stored.state is RatingState.PENDING
            assert stored.retry.attempt_count == attempt
            assert stored.retry.next_attempt_at == NOW + timedelta(
                minutes=delay + (1, 3, 10)[attempt - 1]
            )
        else:
            assert stored.state is RatingState.UNAVAILABLE
            assert stored.retry.attempt_count == 4
            assert stored.retry.next_attempt_at is None

    service.rate_due([GameId("one")], now=NOW + timedelta(hours=1))
    assert provider.calls == [GameId("espn-one")] * 4


def test_due_order_is_fair_and_capped_at_two_source_attempts() -> None:
    early = make_game("early", final_at=NOW - timedelta(minutes=20))
    middle = make_game("middle", final_at=NOW - timedelta(minutes=10))
    late = make_game("late", final_at=NOW - timedelta(minutes=1))
    repository = FakeRepository((late, middle, early))
    provider = FakeSummaryProvider([summary(early), summary(middle)])

    ProvisionalRatingService(repository, provider).rate_due(
        [GameId("late"), GameId("middle"), GameId("early")], now=NOW
    )

    assert provider.calls == [GameId("espn-early"), GameId("espn-middle")]
    assert repository.games[GameId("late")].rating.state is RatingState.PENDING


def test_current_durable_revalidation_skips_confirmed_game() -> None:
    pending = make_game("one")
    confirmed = replace(
        pending,
        rating=GameRating(
            RatingState.CONFIRMED,
            RatingRetry(),
            score=6.5,
            source=RatingSource.NFLVERSE,
            model_version="rating-v1",
            input_hash="confirmed",
            calculated_at=NOW,
            confirmed_at=NOW,
        ),
    )
    repository = FakeRepository((confirmed,))
    provider = FakeSummaryProvider(summary(pending))

    ProvisionalRatingService(repository, provider).rate_due(
        [GameId("one")], now=NOW
    )

    assert provider.calls == []
    assert repository.games[GameId("one")].rating.state is RatingState.CONFIRMED


def test_stale_conditional_write_keeps_durable_rating_unchanged() -> None:
    game = make_game("one")
    repository = FakeRepository((game,))
    repository.stale = True
    provider = FakeSummaryProvider(summary(game))

    ProvisionalRatingService(repository, provider).rate_due(
        [GameId("one")], now=NOW
    )

    assert repository.games[GameId("one")].rating == game.rating
    assert repository.applied == []
