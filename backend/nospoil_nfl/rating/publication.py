"""Shared UTC publication hints; availability is always verified at the source."""

from datetime import UTC, datetime, timedelta


PUBLICATION_WINDOW = timedelta(hours=2)
FAST_PROBE_INTERVAL = timedelta(minutes=15)
QUIET_PROBE_INTERVAL = timedelta(hours=6)
# Weekday numbers are Python's Monday=0. These are publishing job start hints,
# not promises that an asset will exist or be complete at these times.
EXTRA_PUBLICATIONS = ((4, 5, 33), (6, 22, 3), (0, 0, 7), (0, 5, 33), (1, 5, 33))


def publication_starts(now: datetime) -> tuple[datetime, ...]:
    """Cover yesterday's window and the next publication, including rollover."""
    starts = []
    for offset in range(-1, 9):
        day = (now + timedelta(days=offset)).astimezone(UTC)
        if day.month not in {1, 2, 9, 10, 11, 12}:
            continue
        starts.append(day.replace(hour=9, minute=3, second=0, microsecond=0))
        starts.extend(
            day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            for weekday, hour, minute in EXTRA_PUBLICATIONS
            if day.weekday() == weekday
        )
    return tuple(sorted(starts))


def in_publication_window(now: datetime) -> bool:
    return any(
        start <= now < start + PUBLICATION_WINDOW for start in publication_starts(now)
    )


def next_probe_at(now: datetime) -> datetime:
    """Quiet retries cannot sleep across an expected publication window."""
    interval = (
        FAST_PROBE_INTERVAL if in_publication_window(now) else QUIET_PROBE_INTERVAL
    )
    return min(
        (now + interval, *(start for start in publication_starts(now) if start > now))
    )


def confirmation_poll_seconds(now: datetime) -> int:
    """Five minutes near publication; otherwise hourly, waking at the window."""
    if in_publication_window(now):
        return 300
    upcoming = [
        int((start - now).total_seconds())
        for start in publication_starts(now)
        if start > now
    ]
    return max(1, min((3600, *upcoming)))


def correction_cycle(now: datetime) -> datetime:
    """The latest Thursday publication, allowing delayed publication all week."""
    day = now.astimezone(UTC)
    cycle = (day - timedelta(days=(day.weekday() - 3) % 7)).replace(
        hour=9, minute=3, second=0, microsecond=0
    )
    return cycle if cycle <= now else cycle - timedelta(days=7)
