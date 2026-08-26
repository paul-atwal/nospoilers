from backend.excitement_calculator import calculate_excitement_score
from backend.nospoil_nfl.rating import RatingInput, calculate_rating


def test_legacy_wrapper_matches_primary_calculator() -> None:
    wp_history = [0.75, 0.40, 0.10, 0.80, 0.0]
    expected_rating = calculate_rating(
        RatingInput(
            wp_history=tuple(wp_history),
            home_team_won=False,
        )
    )

    actual_rating = calculate_excitement_score(
        wp_history=wp_history,
        home_score=19,
        away_score=20,
        is_overtime=True,
        score_history=[(0, 0), (19, 20)],
    )

    assert actual_rating == expected_rating
