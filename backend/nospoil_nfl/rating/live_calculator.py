"""Winner-free, ESPN-calibrated excitement observed during live play.

Overtime continues accumulating observations at the final regulation calibration
stage. It has no assumed end time and never invokes the final-winner formula.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path

from ..providers.espn_live import LiveObservation
from .calculator import (
    COMEBACK_MEAN,
    COMEBACK_MULTIPLIER,
    K_FACTOR,
    VOLATILITY_MEAN,
    VOLATILITY_STD,
    calculate_wp_volatility_normalized,
)

CALIBRATION = json.loads(Path(__file__).with_name("live_calibration.json").read_text())
MODEL_VERSION = CALIBRATION["model_version"]
MIN_TRANSITIONS = 10
MIN_REGULATION_SECONDS = 300


def live_raw_score(wp: Sequence[float]) -> float:
    """Credit only recoveries already observed, without an eventual winner."""
    p = wp[-1]
    comeback = 7.5 * (
        p * max(0, min(0.5, p) - min(wp)) + (1 - p) * max(0, max(wp) - max(0.5, p))
    )
    return (
        5
        - COMEBACK_MEAN * COMEBACK_MULTIPLIER
        + (calculate_wp_volatility_normalized(wp) - VOLATILITY_MEAN)
        / VOLATILITY_STD
        * K_FACTOR
        + comeback * COMEBACK_MULTIPLIER
    )


def _interpolate(minute: float, values: Sequence[float]) -> float:
    minutes = CALIBRATION["minutes"]
    index = min(len(minutes) - 2, max(0, bisect_right(minutes, minute) - 1))
    weight = (min(minutes[-1], max(minutes[0], minute)) - minutes[index]) / (
        minutes[index + 1] - minutes[index]
    )
    return values[index] + weight * (values[index + 1] - values[index])


def calculate_live_rating(observations: tuple[LiveObservation, ...]) -> float | None:
    """Return no number until enough timed play has been observed."""
    transitions = len(observations) - 1
    elapsed = max((x.regulation_elapsed_seconds for x in observations), default=0)
    if transitions < MIN_TRANSITIONS or elapsed < MIN_REGULATION_SECONDS:
        return None
    minute = elapsed / 60
    raw = live_raw_score([x.home_win_probability for x in observations])
    mean = _interpolate(minute, CALIBRATION["means"])
    std = _interpolate(minute, CALIBRATION["stds"])
    weight = transitions / (transitions + CALIBRATION["prior"] * (1 - minute / 60) ** 2)
    adjusted = (
        CALIBRATION["target_mean"]
        + weight * (raw - mean) / std * CALIBRATION["target_std"]
    )
    return round(max(0, min(10, adjusted)), 1)


def hash_live_input(observations: tuple[LiveObservation, ...]) -> str:
    # A corrected source prefix must recalculate, even at the same last play.
    values = [
        (x.play_id, x.sequence, x.home_win_probability, x.period, x.clock)
        for x in observations
    ]
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
