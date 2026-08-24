from typing import Literal, TypedDict


Score = tuple[int, int]
RatingSource = Literal["ESPN", "nflfastR"]


class RatingInput(TypedDict):
    wp_history: list[float]
    score_history: list[Score]
    home_score: int
    away_score: int
    is_overtime: bool


class GameRatingInput(RatingInput):
    game_id: str
    source: RatingSource
