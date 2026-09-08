"""Minimal errors shared by read and write repository implementations."""

class GameRepositoryError(RuntimeError):
    """Durable game storage failed."""


class GameRepositoryDataError(GameRepositoryError):
    """Stored data could not be decoded."""
