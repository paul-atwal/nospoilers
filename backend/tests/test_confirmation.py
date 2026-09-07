from __future__ import annotations

import subprocess
import sys

import pytest

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.nospoil_nfl.game import (
    GameRating,
    GameState,
    GameStatus,
    RatingRetry,
    RatingState,
    SeasonPhase,
    SeasonWeek,
)
from backend.nospoil_nfl.rating import confirmation_support, confirmation_work_remains
from backend.tests.test_reconciliation import make_game, provisional_rating, confirmed_rating


@pytest.mark.parametrize(
    ("phase", "week", "supported"),
    [
        (SeasonPhase.REGULAR_SEASON, 1, True),
        (SeasonPhase.POSTSEASON, 1, True),
        (SeasonPhase.POSTSEASON, 2, True),
        (SeasonPhase.POSTSEASON, 3, True),
        (SeasonPhase.POSTSEASON, 5, True),
        (SeasonPhase.PRESEASON, 1, False),
        (SeasonPhase.POSTSEASON, 4, False),
        (SeasonPhase.POSTSEASON, 6, False),
    ],
)
def test_confirmation_support_matrix(phase, week, supported) -> None:
    assert confirmation_support(SeasonWeek(2026, phase, week)).supported is supported


def test_rating_imports_are_order_independent() -> None:
    for module in ("backend.nospoil_nfl.rating", "backend.nospoil_nfl.rating.calculator"):
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}; import backend.nospoil_nfl.rating"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "rating",
    [
        GameRating(RatingState.PENDING, RatingRetry()),
        provisional_rating(),
        GameRating(RatingState.UNAVAILABLE, RatingRetry(1, last_error="missing")),
        confirmed_rating(),
    ],
)
def test_confirmation_work_remains_requires_final_supported_unconfirmed(rating) -> None:
    game = make_game("work", rating=rating)
    expected = rating.state is not RatingState.CONFIRMED
    assert confirmation_work_remains(game) is expected
    if rating.state is RatingState.PENDING:
        scheduled = replace(game, status=GameStatus(GameState.SCHEDULED))
        assert confirmation_work_remains(scheduled) is False
    unsupported = replace(
        game,
        season_week=SeasonWeek(2026, SeasonPhase.PRESEASON, 1),
    )
    assert confirmation_work_remains(unsupported) is False
    if rating.state is not RatingState.CONFIRMED:
        delayed = replace(
            game,
            confirmation_retry=RatingRetry(
                1,
                datetime.now(UTC) + timedelta(days=3),
                "retry",
            ),
        )
        assert confirmation_work_remains(delayed) is expected
