"""Readiness 0-10: the four parts, their weights from the config, and missing parts."""

from datetime import date, timedelta

import pytest

from plugins.health.config import READINESS_WEIGHTS, HealthConfig, ReadinessConfig
from plugins.health.readiness import (
    load_points,
    readiness,
    sleep_points,
    z_score_points,
)
from plugins.health.reports import build_input, check_report
from plugins.health.summary import Analyzer
from tests.health_fixtures import entry, rows_with
from tests.test_health_engine import END, build

DAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    engine, _ = build(tmp_path_factory.mktemp("readiness"), rmssd_days=30)
    return engine


def merged(*row_sets):
    rows = {}
    for row_set in row_sets:
        for day, row in row_set.items():
            target = rows.setdefault(day, {**row, "metrics": {}})
            target["metrics"].update(row["metrics"])
    return rows


def night(minutes, device="watch", naps=0):
    """A stored sleep_sessions row as read_nights returns it."""
    return {
        "asleep_minutes": minutes,
        "nap_minutes": naps,
        "total_minutes": minutes + naps,
        "device_class": device,
        "quality": "ok",
    }


def analyzer(rows, nights=None, **readiness_config):
    config = HealthConfig.from_dict({"readiness": readiness_config} if readiness_config else {})
    return Analyzer(rows, nights or {}, config, DAY)


def test_point_scales():
    config = ReadinessConfig()
    for z, points in ((0, 7.5), (1, 10), (2, 10), (-1, 5), (-3, 0), (-5, 0)):
        assert z_score_points(z, config) == pytest.approx(points)
    for minutes, points in ((420, 10), (480, 10), (360, 7.5), (300, 5), (180, 0), (60, 0)):
        assert sleep_points(minutes, config) == pytest.approx(points)
    for acwr, points in ((0.6, 10), (1.0, 10), (1.3, 7), (1.5, 5), (2.0, 0), (2.4, 0)):
        assert load_points(acwr, config) == pytest.approx(points)


def test_resting_hr_scores_lower_when_above_baseline():
    # 30 flat days of 55 bpm: the MAD floor of 1 bpm sets the scale, so 58 bpm is z = 3.
    for today, points in ((55, 7.5), (54, 10.0), (56, 5.0), (58, 0.0)):
        result = readiness(analyzer(rows_with("resting_hr", [55] * 30 + [today])), DAY)
        part = result["components"]["resting_hr"]
        assert part["score"] == points and part["z"] == today - 55
        assert result["score"] is None  # one part only: below min_components (2)


def test_weighted_mean_over_the_parts_a_day_has():
    hrv = rows_with("hrv_ln_rmssd", [4.0] * 30 + [4.0], method="sleep")  # z = 0 -> 7.5
    rhr = rows_with("resting_hr", [55] * 30 + [56])  # z = 1 against -> 5.0
    nights = {DAY: night(300)}  # 5 hours -> 5.0
    result = readiness(analyzer(merged(hrv, rhr), nights), DAY)
    parts = result["components"]
    assert (parts["hrv"]["score"], parts["resting_hr"]["score"], parts["sleep"]["score"]) == (
        7.5,
        5.0,
        5.0,
    )
    assert parts["hrv"]["metric"] == "hrv_ln_rmssd"
    assert parts["training_load"] == {
        "score": None,
        "reason": "chronic_unreliable",
        "weight": 0.15,
    }
    assert result["used"] == ["hrv", "resting_hr", "sleep"]
    # Weights renormalized over the three parts present: (0.35·7.5 + 0.2·5 + 0.3·5) / 0.85.
    assert result["score"] == round((0.35 * 7.5 + 0.2 * 5 + 0.3 * 5) / 0.85, 1) == 6.0

    custom = analyzer(merged(hrv, rhr), nights, weights={"hrv": 1, "resting_hr": 0, "sleep": 1})
    result = readiness(custom, DAY)
    assert result["used"] == ["hrv", "sleep"]  # a zero weight leaves resting HR out
    assert result["score"] == 6.2  # (7.5 + 5) / 2 = 6.25, rounded half to even
    strict = readiness(analyzer(merged(hrv, rhr), nights, min_components=4), DAY)
    assert strict["score"] is None and strict["min_components"] == 4


def test_nightly_hrv_falls_back_to_sdnn_and_needs_a_baseline():
    rmssd_day = rows_with("hrv_ln_rmssd", [4.0] * 30 + [4.0], method="day")
    sdnn_night = rows_with("hrv_ln_sdnn", [3.8] * 30 + [3.8 - 0.05], method="sleep")  # z = -1
    part = readiness(analyzer(merged(rmssd_day, sdnn_night)), DAY)["components"]["hrv"]
    assert part["metric"] == "hrv_ln_sdnn" and part["score"] == 5.0
    short = rows_with("hrv_ln_rmssd", [4.0] * 5 + [4.0], method="sleep")
    part = readiness(analyzer(short), DAY)["components"]["hrv"]
    assert part == {
        "score": None,
        "reason": "baseline_insufficient",
        "metric": "hrv_ln_rmssd",
        "weight": 0.35,
    }
    assert readiness(analyzer({}), DAY)["components"]["hrv"]["reason"] == "no_nightly_hrv"


def test_sleep_part_uses_watch_nights_only():
    assert readiness(analyzer({}, {DAY: night(90)}), DAY)["components"]["sleep"]["score"] == 0
    phone = readiness(analyzer({}, {DAY: night(480, "phone")}), DAY)["components"]["sleep"]
    assert phone["reason"] == "not_top_device"
    assert readiness(analyzer({}), DAY)["components"]["sleep"]["reason"] == "no_night"


def test_sleep_part_counts_the_naps_of_the_sleep_day():
    # 4 hours of main sleep and a 2-hour nap score as 6 hours (7.5), not as 4 (2.5).
    part = readiness(analyzer({}, {DAY: night(240, naps=120)}), DAY)["components"]["sleep"]
    assert part["score"] == 7.5
    assert (part["sleep_minutes"], part["main_minutes"], part["target_minutes"]) == (360, 240, 420)


def test_training_part_from_the_acute_chronic_ratio():
    # Up to yesterday: 21 days of 50 TRIMP, then 7 days of 100, so acute 700 / 7 = 100 and
    # chronic 1750 / 28 = 62.5. Today's own big session (300) is not an input.
    rows = rows_with("trimp", [50] * 21 + [100] * 7 + [300])
    part = readiness(analyzer(rows), DAY)["components"]["training_load"]
    assert part["acwr"] == 1.6 and part["score"] == 4.0
    rows = {d: {**row, "metrics": {"trimp": entry(0)}} for d, row in rows.items()}
    part = readiness(analyzer(rows), DAY)["components"]["training_load"]
    assert part["reason"] == "no_chronic_load"


def test_stressed_days_score_lower(synthetic):
    days = synthetic.daily(END, 8, today=END)["days"]
    scores = {d["date"]: d["readiness"]["score"] for d in days}
    calm = [scores[(END - timedelta(days=k)).isoformat()] for k in range(3, 8)]
    stressed = [scores[(END - timedelta(days=k)).isoformat()] for k in range(3)]
    assert all(0 <= s <= 10 for s in calm + stressed)
    assert max(stressed) < min(calm)
    today = days[-1]["readiness"]
    assert today["used"] == ["hrv", "resting_hr", "sleep", "training_load"]
    assert today["components"]["hrv"]["metric"] == "hrv_ln_rmssd"
    expected = sum(
        today["components"][name]["score"] * weight for name, weight in READINESS_WEIGHTS.items()
    )
    assert today["score"] == round(expected / sum(READINESS_WEIGHTS.values()), 1)


def test_readiness_reaches_the_agent_and_may_be_cited(synthetic):
    bundle = build_input(synthetic, "morning", END, END)
    today = bundle["daily"]["days"][-1]
    score = today["readiness"]["score"]
    assert "不是医学判断" in bundle["daily"]["readiness_note"]
    body = f"## 身体恢复情况\n\n今天的综合准备度是 {score} 分，静息心率明显高于基线，建议关注。"
    assert check_report("晨间简报", body, bundle) == []
    errors = check_report("晨间简报", body.replace(str(score), "88.8"), bundle)
    assert [e["code"] for e in errors] == ["unsupported_numbers"]
    week = build_input(synthetic, "weekly", END, END)["week"]
    assert week["readiness"]["this_week"]["days"] == 6  # today is still in progress
    assert week["readiness"]["change"] is not None
    assert {"hrv_ln_sdnn", "hrv_ln_rmssd"} <= set(week["metrics"])
    assert week["metrics"]["hrv_ln_rmssd"]["label"] == "HRV（RMSSD）"


@pytest.mark.parametrize(
    "bad",
    [
        {"weights": {"hrv": -1}},
        {"weights": {"hrv": 0, "resting_hr": 0, "sleep": 0, "training_load": 0}},
        {"min_components": 5},
        {"z_full": -3, "z_zero": -3},
        {"sleep_target_hours": 3, "sleep_zero_hours": 4},
        {"acwr_full": 2, "acwr_zero": 1},
    ],
)
def test_invalid_readiness_config(bad):
    with pytest.raises(ValueError):
        HealthConfig.from_dict({"readiness": bad})


def test_partial_weights_keep_the_other_defaults():
    config = HealthConfig.from_dict({"readiness": {"weights": {"training_load": 0}}})
    assert config.readiness.weights == {**READINESS_WEIGHTS, "training_load": 0}
    assert HealthConfig().readiness.weights == READINESS_WEIGHTS
    # Readiness is computed on read: changing it never forces a rebuild of stored rows.
    assert config.derived_fingerprint() == HealthConfig().derived_fingerprint()
