"""Per-day aggregation: one health_daily row, one night of sleep and the day's workouts.

Each metric carries a quality: "ok" values feed baselines and alert rules, "partial" values
(phone-only data, short wear time) are shown but excluded, "missing" means no value.
"""

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from plugins.health.cleaning import best_points, cumulative_total, dedup_workouts
from plugins.health.heart import log_mean, resting_heart_rate, robust_max, wear_hours
from plugins.health.intervals import overlap
from plugins.health.observations import CHECKIN_KINDS
from plugins.health.sleep import sleep_day_start, sleep_periods, summarize_night
from plugins.health.training import hr_histogram

DAYTIME_KINDS = ("heart_rate", "resting_heart_rate", "step_count", "active_energy", "vo2max")
NIGHT_KINDS = {
    "sleep",
    "hrv_sdnn",
    "hrv_rmssd",
    "respiratory_rate",
    "oxygen_saturation",
    "wrist_temperature",
}
# Observation kind, stored metric and the extras prefix of each HRV measure.
HRV_KINDS = (("hrv_sdnn", "hrv_ln_sdnn", "hrv"), ("hrv_rmssd", "hrv_ln_rmssd", "hrv_rmssd"))
READING_SECONDS = 300  # longer heart-rate samples are aggregates: wear time only


def readings(samples):
    """Instantaneous heart-rate readings; hourly averages would understate peaks and lows."""
    return [s for s in samples if s.end - s.start <= READING_SECONDS]


@dataclass
class WorkoutResult:
    id: str
    start: int
    end: int
    activity: str
    duration_minutes: float
    device_class: str
    hr_method: str  # samples / average / none
    hr_coverage: float | None
    avg_hr: float | None
    histogram: dict


@dataclass
class DayResult:
    day: object
    status: str  # complete / partial / missing
    issues: list
    wear_hours: int
    metrics: dict
    extras: dict
    night: object
    workouts: list


def day_bounds(day, zone):
    start = datetime.combine(day, time(), zone)
    end = datetime.combine(day + timedelta(days=1), time(), zone)
    return int(start.timestamp()), int(end.timestamp())


def sleep_day_bounds(day, zone, cutoff_hour):
    start = sleep_day_start(day, zone, cutoff_hour)
    end = sleep_day_start(day + timedelta(days=1), zone, cutoff_hour)
    return int(start.timestamp()), int(end.timestamp())


def fetch_windows(day, config):
    """Epoch windows to load per kind; wide enough for nights and workouts crossing midnight."""
    zone = ZoneInfo(config.timezone)
    day_start, day_end = day_bounds(day, zone)
    night_start, night_end = sleep_day_bounds(day, zone, config.sleep_day_cutoff_hour)
    hours = 3600
    vitals = (min(day_start, night_start), max(day_end, night_end) + 6 * hours)
    return {
        "sleep": (night_start - 18 * hours, night_end + 18 * hours),
        "heart_rate": vitals,
        "hrv_sdnn": vitals,
        "hrv_rmssd": vitals,
        "respiratory_rate": vitals,
        "oxygen_saturation": vitals,
        "wrist_temperature": (night_start, night_end),
        "resting_heart_rate": (day_start, day_end),
        "vo2max": (day_start, day_end),
        "step_count": (day_start, day_end),
        "active_energy": (day_start, day_end),
        "workout": (day_start, day_end),
        "workout_hr_avg": (day_start, day_end + 6 * hours),
        "workout_hr_max": (day_start, day_end + 6 * hours),
        **{kind: (day_start, day_end) for kind in CHECKIN_KINDS},
    }


def workout_id(workout):
    key = f"{workout.origin}|{workout.source}|{workout.start}|{workout.end}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def compute_day(day, observations, config):
    zone = ZoneInfo(config.timezone)
    priority, top = config.source_priority, config.source_priority[0]
    by_kind = defaultdict(list)
    for observation in observations:
        by_kind[observation.kind].append(observation)
    day_start, day_end = day_bounds(day, zone)
    night_start, night_end = sleep_day_bounds(day, zone, config.sleep_day_cutoff_hour)

    def starting(kind, start, end):
        return [o for o in by_kind[kind] if start <= o.start < end]

    def overlapping(kind, start, end):
        return [
            o
            for o in by_kind[kind]
            if (o.end > start and o.start < end) or (o.end == o.start and start <= o.start < end)
        ]

    periods = sleep_periods(by_kind["sleep"], priority, config.sleep_night_gap_minutes)
    night = summarize_night(periods, day, zone, config)

    def during_night(kind):
        if night is None:
            return []
        samples = starting(kind, night.onset, night.wake)
        return best_points(readings(samples) if kind == "heart_rate" else samples, priority)

    worn = wear_hours(
        [o for o in overlapping("heart_rate", day_start, day_end) if o.device_class == top],
        day_start,
        day_end,
    )
    # Night-time vitals belong to their night (possibly tomorrow's), not to this day.
    top_present = any(
        o.device_class == top
        for kind in DAYTIME_KINDS
        for o in overlapping(kind, day_start, day_end)
    ) or (night is not None and night.device_class == top)
    if worn >= config.min_wear_hours:
        wear = "complete"
    elif worn:
        wear = "partial"
    else:
        # No heart-rate readings at all: an aggregate-only upload ("unknown") or no watch.
        wear = "unknown" if top_present else "missing"

    metrics, extras = {}, {}

    def put(metric, value, device_class=None, method=None, quality=None):
        if value is None:
            entry = {"value": None, "quality": "missing"}
        else:
            entry = {
                "value": value,
                "quality": quality or ("ok" if device_class == top else "partial"),
            }
        if method:
            entry["method"] = method
        metrics[metric] = entry

    rhr, method, rhr_class = resting_heart_rate(
        best_points(starting("resting_heart_rate", day_start, day_end), priority),
        during_night("heart_rate"),
    )
    put("resting_hr", rhr, rhr_class, method)

    def nightly(kind):
        samples = during_night(kind)
        if samples:
            return samples, "sleep"
        samples = best_points(starting(kind, day_start, day_end), priority)
        return samples, ("day" if samples else None)

    # SDNN and RMSSD are both kept; each gets its own baseline (docs/health-methods.md 5.2).
    for kind, metric, prefix in HRV_KINDS:
        hrv, context = nightly(kind)
        if hrv:
            value = log_mean(hrv)
            put(metric, value, hrv[0].device_class, context)
            extras[f"{kind}_ms"] = math.exp(value)
            extras[f"{prefix}_samples"] = len(hrv)
        else:
            put(metric, None)
    for kind in ("respiratory_rate", "oxygen_saturation"):
        samples, context = nightly(kind)
        mean = sum(s.value for s in samples) / len(samples) if samples else None
        put(kind, mean, samples[0].device_class if samples else None, context)
    for kind, start, end in (
        ("wrist_temperature", night_start, night_end),
        ("vo2max", day_start, day_end),
    ):
        samples = best_points([o for o in by_kind[kind] if start <= o.end < end], priority)
        mean = sum(s.value for s in samples) / len(samples) if samples else None
        put(kind, mean, samples[0].device_class if samples else None)

    # Manual check-ins belong to the calendar day they were made for; a later entry for the
    # same day replaces an earlier one (a corrected value).
    checkin = {}
    for kind in CHECKIN_KINDS:
        entries = starting(kind, day_start, day_end)
        if entries:
            checkin[kind] = max(entries, key=lambda o: (o.start, o.end)).value
    if checkin:
        extras["checkin"] = checkin

    for kind, metric in (("step_count", "steps"), ("active_energy", "active_energy")):
        total, classes = cumulative_total(
            overlapping(kind, day_start, day_end), day_start, day_end, priority
        )
        comparable = wear in {"complete", "unknown"} and top in classes
        put(metric, total, quality="ok" if comparable else "partial")

    workouts = []
    for workout in dedup_workouts(starting("workout", day_start, day_end), priority):
        heart = best_points(
            readings(o for o in by_kind["heart_rate"] if workout.start <= o.start <= workout.end),
            priority,
        )
        histogram, coverage, hr_method = {}, None, "none"
        if heart:
            histogram, coverage = hr_histogram(heart, workout.start, workout.end)
            hr_method = "samples"
        else:
            averages = [
                o
                for o in by_kind["workout_hr_avg"]
                if overlap(o.start, o.end, workout.start, workout.end)
                >= 0.5 * max(1, workout.end - workout.start)
            ]
            if averages:
                histogram = {round(averages[0].value): float(workout.end - workout.start)}
                hr_method = "average"
        seconds = sum(histogram.values())
        workouts.append(
            WorkoutResult(
                id=workout_id(workout),
                start=workout.start,
                end=workout.end,
                activity=workout.label,
                duration_minutes=workout.value,
                device_class=workout.device_class,
                hr_method=hr_method,
                hr_coverage=coverage,
                avg_hr=sum(b * s for b, s in histogram.items()) / seconds if seconds else None,
                histogram=histogram,
            )
        )
    # TRIMP needs HRmax and a resting HR from earlier days; the engine fills in the value.
    if wear in {"complete", "unknown"}:
        trimp_quality = "ok"  # a worn day without workouts is a known zero
    elif wear == "partial" or workouts:
        trimp_quality = "partial"
    else:
        trimp_quality = "missing"
    metrics["trimp"] = {"value": None, "quality": trimp_quality}

    if night is not None:
        staged = night.has_stages
        for metric, value in (
            ("sleep_minutes", night.total_minutes),  # stages and times: main sleep only
            ("deep_minutes", night.deep_minutes if staged else None),
            ("core_minutes", night.core_minutes if staged else None),
            ("rem_minutes", night.rem_minutes if staged else None),
            ("awake_minutes", night.awake_minutes if staged else None),
            ("sleep_onset", night.onset_minutes),
            ("wake_time", night.wake_minutes),
        ):
            put(metric, value, quality=night.quality)
    else:
        for metric in (
            "sleep_minutes",
            "deep_minutes",
            "core_minutes",
            "rem_minutes",
            "awake_minutes",
            "sleep_onset",
            "wake_time",
        ):
            put(metric, None)

    peak = robust_max(
        [
            s.value
            for s in best_points(readings(starting("heart_rate", day_start, day_end)), priority)
        ]
    )
    if peak is None:
        maxima = [o.value for o in starting("workout_hr_max", day_start, day_end)]
        peak = max(maxima) if maxima else None
    extras.update(
        hr_max=peak,
        workouts=len(workouts),
        workout_minutes=sum(w.duration_minutes for w in workouts),
        naps=night.naps if night else 0,
        nap_minutes=night.nap_minutes if night else 0.0,
        segments=night.segments if night else 0,
    )

    issues = []
    if wear == "missing":
        issues.append("watch_not_worn")
    elif wear == "partial":
        issues.append("low_wear_hours")
    if night is None:
        issues.append("no_sleep_record")
    elif night.quality != "ok":
        issues.append("sleep_record_incomplete")
    status = "missing" if wear == "missing" else ("partial" if issues else "complete")
    return DayResult(day, status, issues, worn, metrics, extras, night, workouts)
