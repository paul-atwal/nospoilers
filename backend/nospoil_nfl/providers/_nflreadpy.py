"""Shared, bounded access to nflreadpy's process-global configuration."""

from __future__ import annotations

import math
from threading import Lock
from typing import Literal

from .errors import ProviderError, ProviderTransportError


_NFLREADPY_CONFIG_LOCK = Lock()
NflreadpyLoadFunction = Literal["load_pbp", "load_schedules"]


def load_nflreadpy(
    function_name: NflreadpyLoadFunction,
    seasons: list[int],
    *,
    timeout_seconds: float,
    operation: str,
) -> object:
    """Call one nflreadpy loader with an isolated, bounded timeout."""
    try:
        import nflreadpy as nfl
        from nflreadpy.config import get_config, update_config

        with _NFLREADPY_CONFIG_LOCK:
            previous_timeout = get_config().timeout
            update_config(timeout=max(1, math.ceil(timeout_seconds)))
            try:
                return getattr(nfl, function_name)(seasons)
            finally:
                update_config(timeout=previous_timeout)
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderTransportError(
            f"nflreadpy {function_name} load failed",
            provider="nflverse",
            operation=operation,
        ) from exc


__all__ = ["NflreadpyLoadFunction", "load_nflreadpy"]
