"""Errors raised at external data provider boundaries."""

from __future__ import annotations


class ProviderError(RuntimeError):
    """Base error for a failed external data provider operation."""

    def __init__(self, message: str, *, provider: str, operation: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.operation = operation


class ProviderTransportError(ProviderError):
    """The provider could not complete the request transport."""


class ProviderUnavailableError(ProviderError):
    """The source is temporarily unable to provide the requested data."""


class ProviderDataError(ProviderError):
    """The source response is malformed, incomplete, or unsupported."""


__all__ = [
    "ProviderDataError",
    "ProviderError",
    "ProviderTransportError",
    "ProviderUnavailableError",
]
