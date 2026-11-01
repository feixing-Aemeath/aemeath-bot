"""Banister TRIMP from workout heart rate, and 7-day / 28-day training load."""

import math
from collections import defaultdict
from datetime import timedelta

from plugins.health.config import TRIMP_WEIGHTING
from plugins.health.intervals import Coverage

READING_REACH = 30  # seconds on each side of a reading that count as covered


def hr_histogram(samples, start, end):
    """Seconds spent at each whole bpm during a workout, and the share of time near a reading.

    Between two readings half of the gap is attributed to each (the trapezoidal rule applied to
    the TRIMP weighting); the first and last readings are held to the workout edges.
    """
    points = sorted(((s.start + s.end) // 2, s.value) for s in samples)
    points = [(moment, value) for moment, value in points if start <= moment <= end]
    if not points or end <= start:
        return {}, 0.0
    histogram = defaultdict(float)

    def add(bpm, seconds):
        if seconds > 0:
            histogram[round(bpm)] += seconds

    add(points[0][1], points[0][0] - start)
    for (first_time, first_bpm), (second_time, second_bpm) in zip(points, points[1:], strict=False):
        half = (second_time - first_time) / 2
        add(first_bpm, half)
        add(second_bpm, half)
    add(points[-1][1], end - points[-1][0])
    covered = Coverage()
    for moment, _ in points:
        covered.add(max(start, moment - READING_REACH), min(end, moment + READING_REACH))
    return dict(histogram), covered.total / (end - start)


def trimp(histogram, hr_rest, hr_max, weighting):
    """TRIMP = sum(minutes * x * k1 * exp(k2 * x)), x = (HR - HRrest) / (HRmax - HRrest)."""
    k1, k2 = TRIMP_WEIGHTING[weighting]
    total = 0.0
    for bpm, seconds in histogram.items():
        reserve = min(1.0, max(0.0, (bpm - hr_rest) / (hr_max - hr_rest)))
        total += seconds / 60 * reserve * k1 * math.exp(k2 * reserve)
    return total


def training_load(daily_trimp, day, first_day):
    """Rolling 7-day (acute) and 28-day (chronic) TRIMP sums ending on `day`.

    `daily_trimp` maps dates to TRIMP (0 for a worn day without workouts, None when unknown).
    Unknown days count as 0 but are reported, and make the window unreliable when frequent.
    """

    def window(days):
        values = [daily_trimp.get(day - timedelta(days=offset)) for offset in range(days)]
        return sum(v or 0.0 for v in values), sum(v is None for v in values)

    acute, acute_missing = window(7)
    chronic, chronic_missing = window(28)
    acute_reliable = acute_missing <= 2
    chronic_reliable = (
        first_day is not None and first_day <= day - timedelta(days=27) and chronic_missing <= 7
    )
    ratio = None
    if acute_reliable and chronic_reliable and chronic > 0:
        ratio = (acute / 7) / (chronic / 28)
    return {
        "acute_7d": acute,
        "acute_missing_days": acute_missing,
        "acute_reliable": acute_reliable,
        "chronic_28d": chronic,
        "chronic_missing_days": chronic_missing,
        "chronic_reliable": chronic_reliable,
        "acwr": ratio,
    }
