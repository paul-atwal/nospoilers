from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import io
import json
import logging

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
from backend.nospoil_nfl.game.repository import NflverseIdConflictError
from backend.nospoil_nfl.providers import (
    NflverseGameId,
    NflversePlay,
    NflversePlaySeason,
    NflverseScheduleGame,
    NflverseScheduleSeason,
    ProviderUnavailableError,
)
from backend.nospoil_nfl.rating import RatingInput, hash_rating_input
from backend.nospoil_nfl.rating.reconciliation import (
    NflverseReconciliationService,
)


NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
WEEK = SeasonWeek(2026, SeasonPhase.REGULAR_SEASON, 1)
FINAL_SCORE = Score(home=24, away=17)


class FakeRepository:
    def __init__(self, games: tuple[Game, ...]) -> None:
        self.games = {game.game_id: game for game in games}
        self.get_overrides: dict[GameId, list[Game | None]] = {}
        self.list_overrides: list[list[Game]] = []

    def list_season(self, season: int) -> list[Game]:
        if self.list_overrides:
            return self.list_overrides.pop(0)
        return [game for game in self.games.values() if game.season_week.season == season]

    def get(self, game_id: GameId) -> Game | None:
        override = self.get_overrides.get(game_id)
        if override:
            return override.pop(0)
        return self.games.get(game_id)

    def apply_nflverse_mapping(self, current: Game, nflverse_id: str) -> WriteResult:
        stored = self.games.get(current.game_id)
        if stored != current:
            if stored is not None and stored.nflverse_id == nflverse_id:
                return WriteResult.STALE
            if stored is not None and stored.nflverse_id is not None:
                raise NflverseIdConflictError("mapping conflict")
            return WriteResult.STALE
        if current.nflverse_id is not None:
            if current.nflverse_id == nflverse_id:
                return WriteResult.STALE
            raise NflverseIdConflictError("mapping conflict")
        self.games[current.game_id] = replace(current, nflverse_id=nflverse_id)
        return WriteResult.APPLIED

    def apply_confirmation_retry(self, current: Game, retry: RatingRetry) -> WriteResult:
        stored = self.games.get(current.game_id)
        if (
            stored is None
            or stored.rating != current.rating
            or stored.status != current.status
            or stored.confirmation_retry != current.confirmation_retry
            or stored.rating.state is RatingState.CONFIRMED
        ):
            return WriteResult.STALE
        self.games[current.game_id] = replace(current, confirmation_retry=retry)
        return WriteResult.APPLIED

    def apply_confirmed_rating(self, current: Game, rating: GameRating) -> WriteResult:
        stored = self.games.get(current.game_id)
        if stored != current or current.nflverse_id is None:
            return WriteResult.STALE
        self.games[current.game_id] = replace(
            current,
            rating=rating,
            confirmation_retry=RatingRetry(),
        )
        return WriteResult.APPLIED


class FakeScheduleProvider:
    def __init__(self, schedule: NflverseScheduleSeason | BaseException) -> None:
        self.schedule = schedule
        self.calls: list[int] = []

    def load_schedule(self, season: int) -> NflverseScheduleSeason:
        self.calls.append(season)
        if isinstance(self.schedule, BaseException):
            raise self.schedule
        return self.schedule


class FakePlayProvider:
    def __init__(self, plays: NflversePlaySeason | BaseException) -> None:
        self.plays = plays
        self.calls: list[int] = []

    def load_plays(self, season: int) -> NflversePlaySeason:
        self.calls.append(season)
        if isinstance(self.plays, BaseException):
            raise self.plays
        return self.plays


def make_game(
    game_id: str,
    *,
    final_at: datetime = NOW - timedelta(hours=7),
    rating: GameRating | None = None,
    season: int = WEEK.season,
    phase: SeasonPhase = SeasonPhase.REGULAR_SEASON,
    week: int = 1,
) -> Game:
    season_week = SeasonWeek(season, phase, week)
    return Game(
        game_id=GameId(game_id),
        espn_id=f"espn-{game_id}",
        nflverse_id=None,
        season_week=season_week,
        kickoff_at=final_at - timedelta(hours=3),
        home=TeamGameSnapshot("home", "Home", "H", None, None),
        away=TeamGameSnapshot("away", "Away", "A", None, None),
        status=GameStatus(GameState.FINAL, score=FINAL_SCORE),
        rating=rating or GameRating(RatingState.PENDING, RatingRetry()),
        schedule_checked_at=final_at,
        schedule_updated_at=final_at,
        live_source_checked_at=final_at,
        live_state_updated_at=final_at,
    )


def provisional_rating(*, calculated_at: datetime = NOW - timedelta(hours=7)) -> GameRating:
    return GameRating(
        RatingState.PROVISIONAL,
        RatingRetry(),
        score=5.5,
        source=RatingSource.ESPN,
        model_version="rating-v1",
        input_hash="espn-input",
        calculated_at=calculated_at,
    )


def confirmed_rating(*, input_hash: str = "old-input", model_version: str = "rating-v1") -> GameRating:
    return GameRating(
        RatingState.CONFIRMED,
        RatingRetry(),
        score=5.5,
        source=RatingSource.NFLVERSE,
        model_version=model_version,
        input_hash=input_hash,
        calculated_at=NOW - timedelta(hours=1),
        confirmed_at=NOW - timedelta(hours=1),
    )


def source_for(
    games: tuple[Game, ...],
    *,
    missing: set[str] = set(),
    empty_plays: set[str] = set(),
    schedule_score: Score | None = FINAL_SCORE,
) -> tuple[FakeScheduleProvider, FakePlayProvider]:
    season_week = games[0].season_week
    schedule_games = tuple(
        NflverseScheduleGame(
            season_week=season_week,
            nflverse_game_id=NflverseGameId(f"nv-{game.game_id}"),
            espn_id=None if str(game.game_id) in missing else game.espn_id,
            final_score=schedule_score,
        )
        for game in games
    )
    plays = tuple(
        play
        for game in games
        if str(game.game_id) not in missing and str(game.game_id) not in empty_plays
        for play in (
            NflversePlay(
                NflverseGameId(f"nv-{game.game_id}"),
                2,
                1,
                0.7,
                FINAL_SCORE,
            ),
            NflversePlay(
                NflverseGameId(f"nv-{game.game_id}"),
                1,
                1,
                0.5,
                None,
            ),
        )
    )
    return (
        FakeScheduleProvider(NflverseScheduleSeason(season_week.season, schedule_games)),
        FakePlayProvider(NflversePlaySeason(season_week.season, plays)),
    )


def service(
    repository: FakeRepository,
    schedule_provider: FakeScheduleProvider,
    play_provider: FakePlayProvider,
    *,
    model_version: str = "rating-v1",
    calculator: object | None = None,
) -> NflverseReconciliationService:
    return NflverseReconciliationService(
        repository,
        schedule_provider,
        play_provider,
        model_version=model_version,
        calculator=calculator or (lambda rating_input: 8.0),
    )


def test_no_due_games_do_not_download() -> None:
    game = make_game("one", final_at=NOW - timedelta(hours=1))
    repository = FakeRepository((game,))
    schedule, plays = source_for((game,))

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.selected == 0
    assert result.downloads == 0
    assert schedule.calls == []
    assert plays.calls == []


def test_due_games_load_one_season_and_confirm_using_sorted_verified_plays() -> None:
    first = make_game("one")
    second = make_game("two", rating=provisional_rating())
    repository = FakeRepository((first, second))
    schedule, plays = source_for((first, second))
    inputs: list[RatingInput] = []

    result = service(
        repository,
        schedule,
        plays,
        calculator=lambda rating_input: inputs.append(rating_input) or 8.0,
    ).run_due(2026, now=NOW)

    assert result.selected == 2
    assert result.downloads == 1
    assert schedule.calls == [2026]
    assert plays.calls == [2026]
    assert result.confirmed_updates == 2
    assert inputs == [RatingInput((0.5, 0.7), True)] * 2
    assert repository.games[GameId("one")].nflverse_id == "nv-one"
    assert repository.games[GameId("two")].rating.state is RatingState.CONFIRMED


def test_missing_mapping_and_empty_plays_have_distinct_retries() -> None:
    missing = make_game("missing")
    empty = replace(
        make_game("empty"),
        confirmation_retry=RatingRetry(
            attempt_count=1,
            next_attempt_at=NOW - timedelta(hours=1),
            last_error="nflverse_plays_not_ready",
        ),
    )
    repository = FakeRepository((missing, empty))
    schedule, plays = source_for((missing, empty), missing={"missing"}, empty_plays={"empty"})

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.retries == 2
    assert repository.games[GameId("missing")].confirmation_retry.last_error == "nflverse_mapping_missing"
    assert repository.games[GameId("empty")].confirmation_retry.last_error == "nflverse_plays_not_ready"
    assert repository.games[GameId("missing")].confirmation_retry.next_attempt_at == NOW + timedelta(hours=6)
    assert repository.games[GameId("empty")].confirmation_retry.next_attempt_at == NOW + timedelta(hours=12)


def test_source_failure_retries_unconfirmed_and_preserves_confirmed() -> None:
    pending = make_game("pending")
    confirmed = make_game("confirmed", rating=confirmed_rating())
    repository = FakeRepository((pending, confirmed))
    failure = ProviderUnavailableError("down", provider="nflverse", operation="plays")
    schedule = FakeScheduleProvider(failure)
    plays = FakePlayProvider(failure)

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.source_failure is True
    assert result.retries == 1
    assert repository.games[GameId("pending")].confirmation_retry.attempt_count == 1
    assert repository.games[GameId("confirmed")].rating == confirmed.rating
    assert repository.games[GameId("confirmed")].confirmation_retry == RatingRetry()


def test_source_failure_retries_supported_games_only() -> None:
    supported = make_game("supported")
    unsupported = make_game("preseason", phase=SeasonPhase.PRESEASON)
    repository = FakeRepository((supported, unsupported))
    failure = ProviderUnavailableError(
        "down",
        provider="nflverse",
        operation="plays",
    )
    schedule = FakeScheduleProvider(failure)
    plays = FakePlayProvider(failure)

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.selected == 1
    assert result.source_failure is True
    assert result.retries == 1
    assert repository.games[supported.game_id].confirmation_retry.attempt_count == 1
    assert repository.games[unsupported.game_id] == unsupported


def test_strong_reread_skips_a_newer_confirmed_rating() -> None:
    pending = make_game("one")
    current_confirmed = replace(pending, rating=confirmed_rating())
    repository = FakeRepository((current_confirmed,))
    repository.list_overrides = [[pending]]
    repository.get_overrides[pending.game_id] = [current_confirmed]
    schedule, plays = source_for((pending,))

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.downloads == 1
    assert schedule.calls == [2026]
    assert result.confirmed_updates == 0
    assert result.stale_writes == 0
    assert result.overdue == 0
    assert repository.games[pending.game_id].rating == current_confirmed.rating


def test_correction_skips_same_hash_and_updates_changed_model() -> None:
    game = make_game("one", rating=confirmed_rating(input_hash=hash_rating_input(RatingInput((0.5, 0.7), True))))
    repository = FakeRepository((game,))
    schedule, plays = source_for((game,))
    calculator_calls: list[RatingInput] = []
    same = service(
        repository,
        schedule,
        plays,
        calculator=lambda rating_input: calculator_calls.append(rating_input) or 8.0,
    )

    unchanged = same.run_correction(2026, now=NOW)

    assert unchanged.unchanged == 1
    assert unchanged.confirmed_updates == 0
    assert calculator_calls == [RatingInput((0.5, 0.7), True)]

    schedule, plays = source_for((game,))
    changed = service(repository, schedule, plays, model_version="rating-v2").run_correction(
        2026, now=NOW
    )

    assert changed.confirmed_updates == 1
    assert repository.games[game.game_id].rating.model_version == "rating-v2"


def test_broad_correction_excludes_unsupported_confirmed_games() -> None:
    supported = make_game("supported", rating=confirmed_rating())
    unsupported = make_game(
        "preseason",
        phase=SeasonPhase.PRESEASON,
        rating=confirmed_rating(),
    )
    repository = FakeRepository((supported, unsupported))
    schedule, plays = source_for((supported,))

    result = service(repository, schedule, plays).run_correction(2026, now=NOW)

    assert result.selected == 1
    assert result.downloads == 1
    assert result.confirmed_updates == 1
    assert repository.games[unsupported.game_id] == unsupported


def test_targeted_correction_revalidates_multiple_rating_states_in_one_download() -> (
    None
):
    pending = make_game("pending", final_at=NOW - timedelta(hours=1))
    confirmed = make_game("confirmed", rating=confirmed_rating())
    repository = FakeRepository((pending, confirmed))
    schedule, plays = source_for((pending, confirmed))

    result = service(repository, schedule, plays).run_correction(
        2026,
        now=NOW,
        game_ids=(pending.game_id, confirmed.game_id),
    )

    assert result.selected == 2
    assert result.downloads == 1
    assert result.confirmed_updates == 2
    assert schedule.calls == [2026]
    assert repository.games[pending.game_id].rating.state is RatingState.CONFIRMED
    assert repository.games[confirmed.game_id].rating.state is RatingState.CONFIRMED


def test_multi_target_correction_fails_before_download_when_one_id_is_missing() -> None:
    current = make_game("present")
    repository = FakeRepository((current,))
    schedule, plays = source_for((current,))

    result = service(repository, schedule, plays).run_correction(
        2026,
        now=NOW,
        game_ids=(current.game_id, GameId("missing")),
    )

    assert result.failures == 1
    assert result.manual_correction_failure is True
    assert result.downloads == 0
    assert schedule.calls == []


def test_validation_mismatch_retries_without_replacing_rating() -> None:
    game = make_game("one", rating=provisional_rating())
    repository = FakeRepository((game,))
    schedule, plays = source_for((game,), schedule_score=Score(home=24, away=16))

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.retries == 1
    assert repository.games[game.game_id].rating == game.rating
    assert repository.games[game.game_id].confirmation_retry.last_error == "nflverse_schedule_score_mismatch"


def test_overdue_uses_initial_eligibility_not_current_retry_due() -> None:
    unavailable = GameRating(
        RatingState.UNAVAILABLE,
        RatingRetry(attempt_count=4, last_error="espn_unavailable"),
    )
    game = replace(
        make_game("one", final_at=NOW - timedelta(hours=25), rating=unavailable),
        confirmation_retry=RatingRetry(
            attempt_count=2,
            next_attempt_at=NOW - timedelta(minutes=30),
            last_error="nflverse_plays_not_ready",
        ),
    )
    repository = FakeRepository((game,))
    schedule, plays = source_for((game,))

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.overdue == 1
    assert result.confirmed_updates == 1
    assert repository.games[game.game_id].rating.state is RatingState.CONFIRMED


def test_overdue_future_retry_is_reported_without_source_download() -> None:
    game = replace(
        make_game("one", final_at=NOW - timedelta(hours=25)),
        confirmation_retry=RatingRetry(
            attempt_count=1,
            next_attempt_at=NOW + timedelta(hours=1),
            last_error="nflverse_plays_not_ready",
        ),
    )
    repository = FakeRepository((game,))
    schedule, plays = source_for((game,))

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.overdue == 1
    assert result.selected == 0
    assert result.downloads == 0
    assert schedule.calls == []
    assert plays.calls == []


def test_correction_source_failure_marks_manual_attention_and_keeps_rating() -> None:
    game = make_game("one", rating=confirmed_rating())
    repository = FakeRepository((game,))
    failure = ProviderUnavailableError("down", provider="nflverse", operation="schedule")
    schedule = FakeScheduleProvider(failure)
    plays = FakePlayProvider(failure)

    result = service(repository, schedule, plays).run_correction(2026, now=NOW)

    assert result.manual_correction_failure is True
    assert repository.games[game.game_id].rating == game.rating


def test_source_failure_skips_a_candidate_whose_retry_moved_after_selection() -> None:
    selected = make_game("one")
    moved = replace(
        selected,
        confirmation_retry=RatingRetry(
            attempt_count=1,
            next_attempt_at=NOW + timedelta(hours=1),
            last_error="nflverse_mapping_missing",
        ),
    )
    repository = FakeRepository((moved,))
    repository.list_overrides = [[selected]]
    failure = ProviderUnavailableError("down", provider="nflverse", operation="plays")
    schedule = FakeScheduleProvider(failure)
    plays = FakePlayProvider(failure)

    result = service(repository, schedule, plays).run_due(2026, now=NOW)

    assert result.source_failure is True
    assert result.retries == 0
    assert result.overdue == 0
    assert repository.games[selected.game_id].confirmation_retry == moved.confirmation_retry


def test_unsupported_games_are_not_due_or_overdue_and_preserve_rating() -> None:
    preseason = make_game(
        "pre",
        final_at=NOW - timedelta(hours=30),
        phase=SeasonPhase.PRESEASON,
        rating=provisional_rating(),
    )
    bowl = make_game(
        "bowl",
        final_at=NOW - timedelta(hours=30),
        phase=SeasonPhase.POSTSEASON,
        week=4,
        rating=GameRating(
            RatingState.UNAVAILABLE,
            RatingRetry(1, last_error="espn"),
        ),
    )
    repository = FakeRepository((preseason, bowl))
    schedule, plays = source_for((preseason,))
    result = service(repository, schedule, plays).run_due(2026, now=NOW)
    assert result.selected == result.downloads == result.overdue == result.retries == 0
    assert repository.games[preseason.game_id].rating == preseason.rating
    assert repository.games[bowl.game_id].rating == bowl.rating


def test_targeted_unsupported_correction_fails_before_download() -> None:
    game = make_game("pre", phase=SeasonPhase.PRESEASON)
    repository = FakeRepository((game,))
    schedule, plays = source_for((game,))
    stream = io.StringIO()
    logger = logging.getLogger("a3-targeted-unsupported")
    logger.handlers.clear()
    logger.addHandler(logging.StreamHandler(stream))
    logger.setLevel(logging.INFO)
    service_instance = NflverseReconciliationService(
        repository,
        schedule,
        plays,
        calculator=lambda value: 8.0,
        logger=logger,
    )

    result = service_instance.run_correction(2026, now=NOW, game_id=game.game_id)

    assert result.manual_correction_failure is True
    assert result.downloads == result.selected == 0
    assert schedule.calls == plays.calls == []
    event = json.loads(stream.getvalue())
    assert event["error_code"] == "unsupported_competition"
    assert event["reason"] == "preseason_unsupported"


def test_strong_reread_changing_to_unsupported_skips_work() -> None:
    selected = make_game("one")
    changed = replace(selected, season_week=SeasonWeek(2026, SeasonPhase.PRESEASON, 1))
    repository = FakeRepository((changed,))
    repository.list_overrides = [[selected]]
    repository.get_overrides[selected.game_id] = [changed]
    schedule, plays = source_for((selected,))
    result = service(repository, schedule, plays).run_due(2026, now=NOW)
    assert result.selected == 1
    assert result.retries == result.confirmed_updates == 0


def test_consumed_provider_failure_logs_structured_context_without_scores() -> None:
    game = make_game("failed")
    repository = FakeRepository((game,))
    failure = ProviderUnavailableError(
        "schedule timeout",
        provider="nflverse",
        operation="schedule",
    )
    schedule = FakeScheduleProvider(failure)
    plays = FakePlayProvider(failure)
    stream = io.StringIO()
    logger = logging.getLogger("a3-provider-failure")
    logger.handlers.clear()
    logger.addHandler(logging.StreamHandler(stream))
    logger.setLevel(logging.INFO)
    service_instance = NflverseReconciliationService(
        repository, schedule, plays, calculator=lambda value: 8.0, logger=logger
    )
    service_instance.run_due(2026, now=NOW)
    event = next(
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if '"event":"nflverse_download_failed"' in line
    )
    assert event["error_code"] == "nflverse_schedule_unavailable"
    assert event["exception_type"] == "ProviderUnavailableError"
    assert event["provider"] == "nflverse"
    assert event["operation"] == "schedule"
    assert "24" not in stream.getvalue() and "17" not in stream.getvalue()
