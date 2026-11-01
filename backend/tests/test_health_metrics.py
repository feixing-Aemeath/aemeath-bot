import math
import re
from datetime import date, timedelta

import pytest

from plugins.health.config import HealthConfig
from plugins.health.heart import log_mean, resting_heart_rate, robust_max, wear_hours
from plugins.health.stats import level, mad, median, quantile, robust_z
from plugins.health.summary import Analyzer
from plugins.health.training import hr_histogram, training_load, trimp
from tests.health_fixtures import at, entry, obs, rows_with

DAY = date(2026, 10, 12)
DIAGNOSTIC = re.compile("诊断|疾病|患有|感染|病毒|心律失常|焦虑|抑郁|就医|药")


def test_robust_statistics():
    assert median([3, 1, 2]) == 2 and median([4, 1, 2, 3]) == 2.5
    assert mad([1, 2, 3, 4, 100]) == 1  # one outlier barely moves it
    assert quantile([10, 20, 30, 40, 50], 0.1) == pytest.approx(14)
    assert robust_z(60, 55, 2, 1.0) == pytest.approx(5 / (1.4826 * 2))
    assert robust_z(56, 55, 0, 1.0) == 1  # the floor replaces a zero MAD
    assert [level(z, 2, 3) for z in (0.5, 2.1, -2.5, 3.2, -4)] == [
        "normal",
        "above",
        "below",
        "far_above",
        "far_below",
    ]
    with pytest.raises(ValueError):
        median([])


def test_resting_heart_rate_prefers_apple_then_sleeping_heart_rate():
    apple = [obs("resting_heart_rate", "2026-10-12 00:05", value=v) for v in (55, 57, 56)]
    sleeping = [
        obs("heart_rate", at("2026-10-12 00:00") + i * 1200, value=50 + i) for i in range(12)
    ]
    assert resting_heart_rate(apple, sleeping) == (56, "apple", "watch")
    value, method, _ = resting_heart_rate([], sleeping)
    assert method == "sleep_hr" and value == pytest.approx(quantile(range(50, 62), 0.1))
    # Too few readings, or readings within a short window, give no estimate.
    assert resting_heart_rate([], sleeping[:9]) == (None, None, None)
    short = [obs("heart_rate", at("2026-10-12 00:00") + i * 60, value=50) for i in range(12)]
    assert resting_heart_rate([], short) == (None, None, None)


def test_hrv_is_averaged_on_the_log_scale():
    samples = [obs("hrv_sdnn", "2026-10-12 02:00", value=v) for v in (20, 80)]
    assert log_mean(samples) == pytest.approx(math.log(40))  # geometric mean 40 ms, not 50


def test_wear_hours_and_spike_resistant_maximum():
    day = at("2026-10-12 00:00"), at("2026-10-13 00:00")
    samples = [
        obs("heart_rate", "2026-10-12 00:10"),
        obs("heart_rate", "2026-10-12 00:50"),
        obs("heart_rate", "2026-10-12 05:30"),
        obs("heart_rate", "2026-10-12 10:30", "2026-10-12 12:10"),  # an hourly aggregate
        obs("heart_rate", "2026-10-11 23:50"),  # other day
    ]
    assert wear_hours(samples, *day) == 5
    assert robust_max([60, 61, 230, 62, 150, 152, 151]) == 151
    assert robust_max([60, 230]) is None


def test_workout_heart_rate_histogram_and_trimp():
    start = at("2026-10-12 18:00")
    readings = [obs("heart_rate", start, value=100), obs("heart_rate", start + 10, value=120)]
    histogram, coverage = hr_histogram(readings, start, start + 20)
    assert histogram == {100: 5, 120: 15} and coverage == 1
    sparse, sparse_coverage = hr_histogram(readings[:1], start, start + 600)
    assert sparse == {100: 600} and sparse_coverage == pytest.approx(30 / 600)
    assert hr_histogram([], start, start + 600) == ({}, 0.0)
    # Banister: minutes * x * 0.64 * e^(1.92 x); 30 min at HR 150, rest 50, max 190 -> x = 5/7.
    reserve = 100 / 140
    assert trimp({150: 1800}, 50, 190, "male") == pytest.approx(
        30 * reserve * 0.64 * math.exp(1.92 * reserve)
    )
    assert trimp({150: 1800}, 50, 190, "female") == pytest.approx(
        30 * reserve * 0.86 * math.exp(1.67 * reserve)
    )
    assert trimp({45: 600}, 50, 190, "male") == 0  # below resting HR
    assert trimp({200: 60}, 50, 190, "male") == pytest.approx(0.64 * math.exp(1.92))  # capped


def test_training_load_windows_and_reliability():
    daily = {DAY - timedelta(days=offset): (100.0 if offset % 2 else 0.0) for offset in range(40)}
    load = training_load(daily, DAY, min(daily))
    assert load["acute_7d"] == 300 and load["chronic_28d"] == 1400
    assert load["acwr"] == pytest.approx((300 / 7) / (1400 / 28))
    assert load["acute_reliable"] and load["chronic_reliable"]
    # Unknown days count as zero but make the window unreliable when frequent.
    gaps = {**daily, **{DAY - timedelta(days=offset): None for offset in range(3)}}
    assert not training_load(gaps, DAY, min(daily))["acute_reliable"]
    # Less than 28 days of history: no chronic load, so no ratio.
    recent = {d: v for d, v in daily.items() if d > DAY - timedelta(days=10)}
    short = training_load(recent, DAY, min(recent))
    assert not short["chronic_reliable"] and short["acwr"] is None


def analyzer(rows, config=None, today=DAY):
    return Analyzer(rows, {}, config or HealthConfig(), today)


def test_baseline_excludes_today_partial_days_and_other_methods():
    values = [55, 56, 54, 55, 57, 56, 55, 54, 56, 55] * 3 + [70]
    rows = rows_with("resting_hr", values, method="apple")
    rows[DAY - timedelta(days=3)]["metrics"]["resting_hr"] = entry(80, "partial", "apple")
    rows[DAY - timedelta(days=4)]["metrics"]["resting_hr"] = entry(45, "ok", "sleep_hr")
    base = analyzer(rows).baseline("resting_hr", DAY, 30, "apple")
    assert base["n"] == 28 and base["median"] == 55 and base["reliable"]
    deviation = analyzer(rows).deviation("resting_hr", DAY, 30)
    assert deviation["delta"] == 15 and deviation["level"] == "far_above"
    # A sleeping-HR day is compared only with sleeping-HR days (here: one, too few).
    other = analyzer(rows).baseline("resting_hr", DAY, 30, "sleep_hr")
    assert other["n"] == 1 and other["median"] == 45 and not other["reliable"]


def test_too_little_history_gives_no_verdict():
    rows = rows_with("resting_hr", [55, 56, 54, 70], method="apple")
    deviation = analyzer(rows).deviation("resting_hr", DAY, 7)
    assert deviation["n"] == 3 and not deviation["reliable"]
    assert deviation["z"] is None and deviation["level"] == "baseline_insufficient"
    assert analyzer(rows_with("resting_hr", [62], method="apple")).deviation(
        "resting_hr", DAY, 7
    ) == {"n": 0, "reliable": False, "z": None, "level": "no_baseline"}
    assert analyzer(rows).alerts(DAY) == []


def test_daily_trimp_is_reported_but_not_scored():
    rows = rows_with("trimp", [0, 0, 100, 0, 0, 0, 0, 0, 120, 0, 0, 0, 0, 0, 0, 0, 90])
    deviation = analyzer(rows, today=DAY + timedelta(days=1)).deviation("trimp", DAY, 30)
    assert deviation["level"] == "not_scored" and deviation["z"] is None
    with pytest.raises(ValueError):
        HealthConfig.from_dict({"alerts": [{"id": "x", "metric": "trimp", "direction": "above"}]})


def resting(values):
    return rows_with("resting_hr", values, method="apple")


HISTORY = [55, 56, 54, 55, 57, 56, 55, 54, 56, 55] * 3


def test_resting_hr_alert_needs_consecutive_days():
    rows = resting(HISTORY + [63, 64, 65])
    alerts = analyzer(rows).alerts(DAY)
    assert [(a["rule"], a["consecutive_days"]) for a in alerts] == [("resting_hr_high", 3)]
    message = alerts[0]["message"]
    assert message.endswith("建议关注。") and "65.0 bpm" in message and "55.0 bpm" in message
    assert not DIAGNOSTIC.search(message)
    assert analyzer(rows).alerts(DAY - timedelta(days=1)) == []  # only two days so far
    # A day without data breaks the run.
    broken = resting(HISTORY + [63, None, 64, 65])
    assert analyzer(broken).alerts(DAY) == []
    # Partial (not comparable) values never count.
    partial = resting(HISTORY + [63, 64, 65])
    partial[DAY - timedelta(days=1)]["metrics"]["resting_hr"]["quality"] = "partial"
    assert analyzer(partial).alerts(DAY) == []


def test_alert_rules_and_thresholds_come_from_config():
    rows = resting(HISTORY + [63, 64, 65])
    strict = HealthConfig.from_dict(
        {"alerts": [{"id": "rhr", "metric": "resting_hr", "direction": "above", "z": 9}]}
    )
    assert analyzer(rows, strict).alerts(DAY) == []
    quick = HealthConfig.from_dict(
        {"alerts": [{"id": "rhr", "metric": "resting_hr", "direction": "above", "days": 2}]}
    )
    assert analyzer(rows, quick).alerts(DAY - timedelta(days=1))[0]["rule"] == "rhr"
    disabled = HealthConfig.from_dict(
        {"alerts": [{"id": "rhr", "metric": "resting_hr", "direction": "above", "enabled": False}]}
    )
    assert analyzer(rows, disabled).alerts(DAY) == []
    # MAD of HISTORY is 1 bpm, so +2 bpm is z = 1.35 and +3 bpm is z = 2.02.
    looser = HealthConfig.from_dict({"baseline": {"notable_z": 1.0, "strong_z": 1.5}})
    for value, expected in ((57, "above"), (58, "far_above")):
        deviation = analyzer(resting(HISTORY + [value]), looser).deviation("resting_hr", DAY, 30)
        assert deviation["level"] == expected
        assert analyzer(resting(HISTORY + [value])).deviation("resting_hr", DAY, 30)["level"] == (
            "normal" if value == 57 else "above"
        )


def test_values_on_the_median_never_count_as_a_deviation():
    rule = {"id": "any", "metric": "resting_hr", "direction": "either", "z": 0, "days": 1}
    config = HealthConfig.from_dict({"alerts": [rule]})
    assert analyzer(resting(HISTORY + [55]), config).alerts(DAY) == []
    fired = analyzer(resting(HISTORY + [56]), config).alerts(DAY)
    assert [a["direction"] for a in fired] == ["above"]


def test_hrv_and_schedule_alerts_and_wording():
    hrv = [math.log(v) for v in [45, 47, 44, 46, 45, 48, 43, 45, 46, 44] * 3 + [30, 31, 29]]
    rows = rows_with("hrv_ln_sdnn", hrv, method="sleep")
    alert = analyzer(rows).alerts(DAY)[0]
    assert alert["rule"] == "hrv_low" and "明显低于" in alert["message"]
    assert "SDNN 29 ms" in alert["message"]
    onset = [300 + (i % 5) * 5 for i in range(30)] + [400, 410, 395]  # ~23:10 then ~00:40
    late = analyzer(rows_with("sleep_onset", onset)).alerts(DAY)[0]
    assert late["rule"] == "sleep_onset_shift" and late["direction"] == "above"
    assert "入睡时间已连续 3 天明显晚于" in late["message"] and "00:35" in late["message"]
    for message in (alert["message"], late["message"]):
        assert message.endswith("建议关注。") and not DIAGNOSTIC.search(message)
