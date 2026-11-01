import pytest

from plugins.health.cleaning import best_points, cumulative_total, dedup_workouts
from plugins.health.observations import (
    classify_type,
    convert,
    device_class,
    normalize_upload,
    parse_time,
)
from tests.health_fixtures import PHONE, WATCH, at, obs

PRIORITY = ("watch", "phone", "other")


def test_time_parsing_keeps_the_recorded_offset():
    # The same instant written in Shanghai, Tokyo (travel) and UTC.
    assert parse_time("2026-10-12 23:30:00 +0800") == parse_time("2026-10-13 00:30:00 +0900")
    assert parse_time("2026-10-12 23:30:00 +0800") == parse_time("2026-10-12T15:30:00Z")
    with pytest.raises(ValueError):
        parse_time("2026-10-12 23:30:00")


@pytest.mark.parametrize(
    ("kind", "value", "unit", "expected"),
    [
        ("oxygen_saturation", 0.97, "%", 97.0),
        ("oxygen_saturation", 96.0, "%", 96.0),
        ("active_energy", 300, "Cal", 300.0),
        ("active_energy", 300, "kcal", 300.0),
        ("active_energy", 300_000, "cal", 300.0),
        ("active_energy", 418.4, "kJ", 100.0),
        ("wrist_temperature", 98.6, "degF", 37.0),
        ("heart_rate", 1.0, "count/s", 60.0),
        ("hrv_sdnn", 0.045, "s", 45.0),
        ("workout", 1800, "s", 30.0),
        ("heart_rate", 62, "furlongs", None),  # unknown unit
        ("heart_rate", 400, "count/min", None),  # implausible reading
        ("step_count", float("nan"), "count", None),
    ],
)
def test_unit_conversion_and_plausibility(kind, value, unit, expected):
    result = convert(kind, value, unit)
    assert result == pytest.approx(expected) if expected is not None else result is None


def test_device_class_prefers_the_hkdevice_model():
    watch = "<<HKDevice: 0x28>, name:Apple Watch, manufacturer:Apple Inc., model:Watch>"
    phone = "<<HKDevice: 0x29>, name:iPhone, manufacturer:Apple Inc., model:iPhone>"
    strap = "<<HKDevice: 0x30>, name:Polar H10, manufacturer:Polar, model:H10>"
    assert device_class("Strava", watch) == "watch"
    assert device_class("任意名字", phone) == "phone"
    assert device_class("Polar Beat", strap) == "other"
    assert device_class(WATCH) == "watch" and device_class(PHONE) == "phone"
    assert device_class("我的手表") == "watch" and device_class("AutoSleep") == "other"


def test_upload_types_map_to_the_same_kinds_as_the_export():
    assert classify_type("HKQuantityTypeIdentifierHeartRate", 60) == ("heart_rate", "")
    assert classify_type("steps", 10) == ("step_count", "")
    assert classify_type("HKCategoryTypeIdentifierSleepAnalysis", 4) == ("sleep", "deep")
    assert classify_type("HKCategoryTypeIdentifierSleepAnalysis", 9) == (None, "")
    assert classify_type("sleep_rem", 1) == ("sleep", "rem")
    assert classify_type("HKWorkoutActivityTypeCycling", 30) == ("workout", "Cycling")
    assert classify_type("workout:Running", 30) == ("workout", "Running")
    assert classify_type("blood_glucose", 5) == (None, "")
    sample = {
        "type": "HKCategoryTypeIdentifierSleepAnalysis",
        "value": 3,
        "unit": "",
        "start_at": "2026-10-11T15:30:00Z",
        "end_at": "2026-10-11T16:00:00Z",
        "source": WATCH,
    }
    assert normalize_upload(sample) == (
        "sleep", at("2026-10-11 23:30"), at("2026-10-12 00:00"), 3.0, "core", WATCH, "watch",
    )  # fmt: skip
    workout = {**sample, "type": "workout", "value": 0, "unit": ""}
    assert normalize_upload(workout)[3] == 30.0  # duration taken from the interval
    assert normalize_upload({**sample, "type": "unknown"}) is None
    assert normalize_upload({**sample, "end_at": "2026-10-11T15:00:00Z"}) is None
    assert normalize_upload({**sample, "start_at": "2020-01-01T00:00:00Z"}) is None  # years long


def test_the_same_reading_from_export_and_upload_counts_once():
    readings = [
        obs("heart_rate", "2026-10-12 10:00", value=62, source=WATCH, origin="export"),
        obs("heart_rate", "2026-10-12 10:00", value=62, source="Apple Watch", origin="upload"),
        obs("heart_rate", "2026-10-12 10:05", value=64),
        # A chest strap on the same day is ignored while the watch has readings.
        obs("heart_rate", "2026-10-12 10:06", value=90, source="Polar", device="other"),
    ]
    kept = best_points(readings, PRIORITY)
    assert [(r.value, r.origin) for r in kept] == [(62, "export"), (64, "export")]
    only_strap = best_points(readings[3:], PRIORITY)
    assert [r.value for r in only_strap] == [90]


def test_steps_from_watch_and_phone_are_not_double_counted():
    day = at("2026-10-12 00:00"), at("2026-10-13 00:00")
    samples = [
        obs("step_count", "2026-10-12 10:00", "2026-10-12 10:10", 100),
        # Same walk counted by the phone, shifted by five minutes: half of it is new time.
        obs("step_count", "2026-10-12 10:05", "2026-10-12 10:15", 60, source=PHONE, device="phone"),
        # Watch charging: only the phone counted this walk.
        obs("step_count", "2026-10-12 21:00", "2026-10-12 21:10", 50, source=PHONE, device="phone"),
    ]
    total, classes = cumulative_total(samples, *day, PRIORITY)
    assert total == pytest.approx(100 + 30 + 50)
    assert classes == {"watch", "phone"}
    assert cumulative_total([], *day, PRIORITY) == (None, set())


def test_daily_totals_are_used_alone_and_midnight_samples_are_split():
    day = at("2026-10-12 00:00"), at("2026-10-13 00:00")
    total = obs("step_count", "2026-10-12 00:00", "2026-10-13 00:00", 8000, source="iPhone App")
    fine = obs("step_count", "2026-10-12 10:00", "2026-10-12 10:10", 400)
    assert cumulative_total([fine, total], *day, PRIORITY)[0] == pytest.approx(8000)
    crossing = obs("step_count", "2026-10-12 23:50", "2026-10-13 00:10", 200)
    assert cumulative_total([crossing], *day, PRIORITY)[0] == pytest.approx(100)
    next_day = at("2026-10-13 00:00"), at("2026-10-14 00:00")
    assert cumulative_total([crossing], *next_day, PRIORITY)[0] == pytest.approx(100)


def test_workout_written_by_watch_and_third_party_app_is_kept_once():
    watch = obs("workout", "2026-10-12 18:00", "2026-10-12 18:40", 40, "Running")
    strava = obs(
        "workout", "2026-10-12 18:00:30", "2026-10-12 18:40", 39.5, "Running",
        source="Strava", device="phone",
    )  # fmt: skip
    later = obs("workout", "2026-10-12 20:00", "2026-10-12 20:30", 30, "Cycling")
    kept = dedup_workouts([strava, later, watch], PRIORITY)
    assert [(w.source, w.label) for w in kept] == [(WATCH, "Running"), (WATCH, "Cycling")]
