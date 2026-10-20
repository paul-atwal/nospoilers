"""Independent live-rating snapshot; completed ratings retain their lifecycle."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import re


@dataclass(frozen=True, slots=True)
class LiveRating:
    score: float
    model_version: str
    input_hash: str
    calculated_at: datetime
    source_updated_at: datetime
    expires_at: datetime
    period: int
    clock: str
    last_play_sequence: int
    observation_count: int

    def __post_init__(self) -> None:
        # Import here avoids a cycle when Game references this value.
        from .models import DomainValidationError

        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not math.isfinite(self.score)
            or not 0 <= self.score <= 10
        ):
            raise DomainValidationError("live rating score must be finite and bounded")
        if any(
            not isinstance(x, str) or not x.strip()
            for x in (self.model_version, self.input_hash)
        ):
            raise DomainValidationError("live rating provenance is required")
        for x in (self.calculated_at, self.source_updated_at, self.expires_at):
            if not isinstance(x, datetime) or x.utcoffset() != timedelta(0):
                raise DomainValidationError("live rating timestamps must be UTC")
        if self.expires_at <= self.calculated_at:
            raise DomainValidationError("live rating must expire after calculation")
        for value, minimum in (
            (self.period, 1),
            (self.last_play_sequence, 0),
            (self.observation_count, 2),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise DomainValidationError("invalid live rating progress")
        if not isinstance(self.clock, str) or not re.fullmatch(
            r"\d{1,2}:[0-5]\d", self.clock
        ):
            raise DomainValidationError("invalid live rating clock")
        minutes, seconds = map(int, self.clock.split(":"))
        if minutes * 60 + seconds > 900:
            raise DomainValidationError("live rating clock exceeds a period")
