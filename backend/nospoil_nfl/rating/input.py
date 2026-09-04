"""Immutable input for the excitement-rating formula."""

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True, slots=True)
class RatingInput:
    """Values used directly by the excitement-rating formula."""

    wp_history: tuple[float, ...]
    home_team_won: bool


def hash_rating_input(rating_input: RatingInput) -> str:
    """Return a stable SHA-256 fingerprint of the shared formula input."""
    if not isinstance(rating_input, RatingInput):
        raise TypeError("rating_input must be a RatingInput")

    canonical = json.dumps(
        {
            "home_team_won": rating_input.home_team_won,
            "wp_history": rating_input.wp_history,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
