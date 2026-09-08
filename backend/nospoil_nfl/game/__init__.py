"""Lazy public game-domain API.

Keeping this package initializer inert lets the read Lambda import model and
codec leaf modules without loading write rules or update commands.
"""

from __future__ import annotations

from importlib import import_module


_MODEL_EXPORTS = {
    "DomainValidationError",
    "Game",
    "GameId",
    "GameOutcome",
    "GameRating",
    "GameState",
    "GameStatus",
    "OddsSnapshot",
    "RatingRetry",
    "RatingSource",
    "RatingState",
    "RecordScope",
    "RecordSnapshot",
    "Score",
    "SeasonPhase",
    "SeasonWeek",
    "TeamGameSnapshot",
    "TeamRecord",
}
_RULE_EXPORTS = {
    "can_transition_game_state",
    "can_transition_rating_state",
    "derive_final_outcome",
    "rating_input_for_outcome",
}
_UPDATE_EXPORTS = {
    "LiveFinalizationUpdate",
    "LiveStatusUpdate",
    "ScheduleUpdate",
    "TeamScheduleUpdate",
    "UNSET",
    "WriteResult",
}

__all__ = sorted(_MODEL_EXPORTS | _RULE_EXPORTS | _UPDATE_EXPORTS)


def __getattr__(name: str) -> object:
    if name in _MODEL_EXPORTS:
        module = import_module(".models", __name__)
    elif name in _RULE_EXPORTS:
        module = import_module(".rules", __name__)
    elif name in _UPDATE_EXPORTS:
        module = import_module(".updates", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(module, name)
    globals()[name] = value
    return value
