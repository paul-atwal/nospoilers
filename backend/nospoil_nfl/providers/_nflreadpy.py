"""Shared, bounded access to nflreadpy's process-global configuration."""

from __future__ import annotations

import math
from threading import Lock
from typing import Literal

import requests

from .errors import (
    ProviderDataError,
    ProviderError,
    ProviderTransportError,
    ProviderUnavailableError,
)


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
        _raise_nflreadpy_error(exc, function_name=function_name, operation=operation)


def _raise_nflreadpy_error(
    exc: Exception,
    *,
    function_name: str,
    operation: str,
) -> None:
    """Map nflreadpy's preserved source failures to provider semantics."""
    exception_chain = _exception_chain(exc)
    message = f"nflreadpy {function_name} load failed"

    for source_error in exception_chain:
        response = getattr(source_error, "response", None)
        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            continue
        if status_code == 404 or status_code == 429 or status_code >= 500:
            raise ProviderUnavailableError(
                message,
                provider="nflverse",
                operation=operation,
            ) from exc
        if 400 <= status_code < 500:
            raise ProviderDataError(
                message,
                provider="nflverse",
                operation=operation,
            ) from exc

    if any(
        isinstance(
            source_error,
            (ConnectionError, requests.Timeout, requests.ConnectionError),
        )
        for source_error in exception_chain
    ):
        raise ProviderTransportError(
            message,
            provider="nflverse",
            operation=operation,
        ) from exc
    if isinstance(exc, ValueError):
        raise ProviderDataError(
            message,
            provider="nflverse",
            operation=operation,
        ) from exc
    raise ProviderTransportError(
        message,
        provider="nflverse",
        operation=operation,
    ) from exc


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    """Return an exception's cause/context chain without revisiting errors."""
    chain: list[BaseException] = []
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return tuple(chain)


__all__ = ["NflreadpyLoadFunction", "load_nflreadpy"]
