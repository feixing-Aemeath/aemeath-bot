"""One observation shape for iPhone/Shortcuts uploads and Apple Health exports.

Both sources are reduced to rows of (kind, start, end, value, label, source, device_class):
UTC epoch seconds, a canonical unit per kind, a sleep stage or workout activity as label.
"""

import math
from dataclasses import dataclass
from datetime import datetime

QUANTITY_TYPES = {
    "HKQuantityTypeIdentifierHeartRate": "heart_rate",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv_sdnn",
    # iOS / watchOS 27 ("Recovery HRV" in the Health app); see docs/health-methods.md 5.2.
    "HKQuantityTypeIdentifierHeartRateVariabilityRMSSD": "hrv_rmssd",
    "HKQuantityTypeIdentifierRespiratoryRate": "respiratory_rate",
    "HKQuantityTypeIdentifierOxygenSaturation": "oxygen_saturation",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "wrist_temperature",
    "HKQuantityTypeIdentifierStepCount": "step_count",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_energy",
    "HKQuantityTypeIdentifierVO2Max": "vo2max",
}
# Daily check-ins for calibrating readiness (docs/readiness-calibration.md), uploaded from the
# App or Shortcuts: Apple Fitness's readiness (0-10) and how ready the owner feels (1-5).
CHECKIN_KINDS = ("apple_fitness_readiness", "perceived_readiness")
UPLOAD_ALIASES = {
    **{kind: kind for kind in QUANTITY_TYPES.values()},
    **{kind: kind for kind in CHECKIN_KINDS},
    "steps": "step_count",
    "workout_heart_rate_average": "workout_hr_avg",
    "workout_heart_rate_max": "workout_hr_max",
}
SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
SLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisInBed": "in_bed",
    "HKCategoryValueSleepAnalysisAsleep": "asleep",  # before iOS 16
    "HKCategoryValueSleepAnalysisAsleepUnspecified": "asleep",
    "HKCategoryValueSleepAnalysisAwake": "awake",
    "HKCategoryValueSleepAnalysisAsleepCore": "core",
    "HKCategoryValueSleepAnalysisAsleepDeep": "deep",
    "HKCategoryValueSleepAnalysisAsleepREM": "rem",
}
# HKCategoryValueSleepAnalysis raw values, used as the numeric value of sleep rows.
STAGE_CODES = {"in_bed": 0, "asleep": 1, "awake": 2, "core": 3, "deep": 4, "rem": 5}
SLEEP_ALIASES = {f"sleep_{stage}": stage for stage in STAGE_CODES}
ASLEEP_STAGES = frozenset({"asleep", "core", "deep", "rem"})
WORKOUT_PREFIX = "HKWorkoutActivityType"

PLAUSIBLE = {
    "heart_rate": (25, 240),
    "resting_heart_rate": (25, 150),
    "workout_hr_avg": (25, 240),
    "workout_hr_max": (25, 240),
    "hrv_sdnn": (1, 500),
    "hrv_rmssd": (1, 500),
    "apple_fitness_readiness": (0, 10),
    "perceived_readiness": (1, 5),
    "respiratory_rate": (3, 60),
    "oxygen_saturation": (50, 100),
    "wrist_temperature": (25, 45),
    "step_count": (0, 100_000),
    "active_energy": (0, 20_000),
    "vo2max": (5, 100),
    "workout": (0.5, 24 * 60),
}
PER_MINUTE = {"count/min", "bpm", "/min", "1/min", "beats/min", "breaths/min", "次/分", ""}
SCORE_UNITS = {"", "score", "分", "count"}
MAX_SPAN = 2 * 86400  # longer samples are corrupt; they would also widen every recompute
# Bumped whenever uploads that were ignored before become usable (version 2: RMSSD), so the
# stored uploads are read again once; observations are idempotent.
UPLOAD_NORMALIZER = 2


@dataclass(frozen=True, slots=True)
class Observation:
    kind: str
    start: int  # UTC epoch seconds
    end: int
    value: float
    label: str
    source: str
    device_class: str
    origin: str  # "export" or "upload"


def convert(kind, value, unit):
    """Value in the canonical unit of `kind`, or None for unknown units and implausible values."""
    raw = (unit or "").strip()
    lowered = raw.lower()
    if kind in {"heart_rate", "resting_heart_rate", "respiratory_rate"} or kind.startswith(
        "workout_hr"
    ):
        factor = 1 if lowered in PER_MINUTE else 60 if lowered in {"count/s", "hz"} else None
        converted = None if factor is None else value * factor
    elif kind in {"hrv_sdnn", "hrv_rmssd"}:
        converted = {"ms": value, "": value, "s": value * 1000}.get(lowered)
    elif kind == "oxygen_saturation":
        # HealthKit stores a fraction with unit "%"; some clients send 0-100.
        converted = (value * 100 if value <= 1 else value) if lowered in {"%", ""} else None
    elif kind == "wrist_temperature":
        if lowered in {"degc", "°c", "℃", ""}:
            converted = value
        elif lowered in {"degf", "°f", "℉"}:
            converted = (value - 32) * 5 / 9
        else:
            converted = None
    elif kind == "step_count":
        converted = value if lowered in {"count", "steps", "步", ""} else None
    elif kind == "active_energy":
        # HealthKit: "Cal"/"kcal" are kilocalories, lower-case "cal" is a small calorie.
        factors = {"Cal": 1, "kcal": 1, "千卡": 1, "": 1, "cal": 0.001, "kJ": 1 / 4.184}
        converted = value * factors[raw] if raw in factors else None
    elif kind == "vo2max":
        compact = lowered.replace(" ", "")
        converted = value if compact == "" or ("ml" in compact and "kg" in compact) else None
    elif kind == "apple_fitness_readiness":
        converted = value if lowered in SCORE_UNITS else None
    elif kind == "perceived_readiness":
        # A 1-5 rating: whole numbers only, so a mistyped 3.7 is not silently kept.
        converted = value if lowered in SCORE_UNITS and float(value).is_integer() else None
    elif kind == "workout":
        factors = {"min": 1, "": 1, "s": 1 / 60, "h": 60, "hr": 60}
        converted = value * factors[lowered] if lowered in factors else None
    else:
        return None
    if converted is None or not math.isfinite(converted):
        return None
    low, high = PLAUSIBLE[kind]
    return float(converted) if low <= converted <= high else None


def device_class(source_name, device=""):
    """watch / phone / other, from the HKDevice model when present, else the source name."""
    if device:
        index = device.find("model:")
        if index >= 0:
            model = device[index + 6 :].split(",", 1)[0].strip(" >").lower()
            if "watch" in model:
                return "watch"
            if "iphone" in model:
                return "phone"
            return "other"
    name = (source_name or "").lower()
    if "watch" in name or "手表" in name:
        return "watch"
    if "iphone" in name or "phone" in name or "手机" in name:
        return "phone"
    return "other"


def parse_time(text):
    """ISO 8601 or Apple export format ("2026-10-12 23:41:07 +0800") to UTC epoch seconds."""
    if len(text) == 25 and text[19] == " ":
        text = text[:19] + text[20:]
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("timestamp without offset")
    return round(moment.timestamp())


def classify_type(type_name, value):
    """Map an upload/export type (and a sleep category value) to (kind, label)."""
    if type_name in QUANTITY_TYPES:
        return QUANTITY_TYPES[type_name], ""
    if type_name in UPLOAD_ALIASES:
        return UPLOAD_ALIASES[type_name], ""
    if type_name == SLEEP_TYPE:
        if isinstance(value, str):
            stage = SLEEP_VALUES.get(value)
        else:
            stage = {code: stage for stage, code in STAGE_CODES.items()}.get(
                int(value) if float(value).is_integer() else -1
            )
        return ("sleep", stage) if stage else (None, "")
    if type_name in SLEEP_ALIASES:
        return "sleep", SLEEP_ALIASES[type_name]
    if type_name.startswith(WORKOUT_PREFIX):
        return "workout", type_name[len(WORKOUT_PREFIX) :] or "Other"
    if type_name in {"HKWorkoutTypeIdentifier", "workout"}:
        return "workout", "Other"
    if type_name.startswith("workout:"):
        return "workout", type_name.split(":", 1)[1] or "Other"
    return None, ""


def normalize_upload(sample):
    """A stored HealthSample (dict) to an observation row, or None when it is not usable."""
    kind, label = classify_type(sample["type"], sample["value"])
    if kind is None:
        return None
    start, end = parse_time(sample["start_at"]), parse_time(sample["end_at"])
    if not 0 <= end - start <= MAX_SPAN:
        return None
    if kind == "sleep":
        value = float(STAGE_CODES[label])
    elif kind == "workout":
        value = convert("workout", sample["value"], sample["unit"]) if sample["unit"] else None
        value = value if value is not None else convert("workout", (end - start) / 60, "min")
    else:
        value = convert(kind, sample["value"], sample["unit"])
    if value is None:
        return None
    source = sample.get("source") or "unknown"
    return (kind, start, end, value, label, source, device_class(source))
