"""Lazy public rating API for runtime-specific dependency isolation."""

from __future__ import annotations

from importlib import import_module


_INPUT_EXPORTS = {"RatingInput", "hash_rating_input"}
_CALCULATOR_EXPORTS = {"calculate_rating"}
_CONFIRMATION_EXPORTS = {
    "ConfirmationSupport",
    "confirmation_support",
    "confirmation_work_remains",
    "is_confirmation_supported",
}

__all__ = sorted(_INPUT_EXPORTS | _CALCULATOR_EXPORTS | _CONFIRMATION_EXPORTS)


def __getattr__(name: str) -> object:
    if name in _INPUT_EXPORTS:
        module = import_module(".input", __name__)
    elif name in _CALCULATOR_EXPORTS:
        module = import_module(".calculator", __name__)
    elif name in _CONFIRMATION_EXPORTS:
        module = import_module(".confirmation", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(module, name)
    globals()[name] = value
    return value
