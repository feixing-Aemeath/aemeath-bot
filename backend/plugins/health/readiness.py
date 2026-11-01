"""Readiness: one 0-10 number from four parts, each also scored 0-10.

    hrv            nightly HRV (RMSSD when the night can be judged by it, else SDNN) against
                   the owner's 30-day baseline of the same measure measured the same way
    resting_hr     resting heart rate against its 30-day baseline (lower is better)
    sleep          last night's total sleep against a fixed target (default 7 hours)
    training_load  acute:chronic TRIMP ratio up to the day before (higher recent load scores
                   lower); the day's own training is what the score is for, not an input

The total is the weighted mean of the parts a day has, with weights from the config
(normalized over those parts); with fewer than `min_components` parts there is no score.
This is an engineering combination, not a validated or medical measure; see
docs/health-methods.md, section 7, for the formulas and their limits.
"""

from datetime import timedelta

from plugins.health.training import training_load

NOTE = (
    "综合准备度是按配置的权重组合夜间 HRV、静息心率、睡眠和训练负荷的工程估计，"
    "没有经过验证，只和本人历史比较，不是医学判断。"
)
LABELS = {"hrv": "夜间 HRV", "resting_hr": "静息心率", "sleep": "睡眠", "training_load": "训练负荷"}


def clamp(value, low=0.0, high=10.0):
    return max(low, min(high, value))


def z_score_points(z, config):
    """Robust z with larger meaning better, as 0-10: z_zero -> 0, z_full -> 10, linear."""
    return clamp(10 * (z - config.z_zero) / (config.z_full - config.z_zero))


def sleep_points(minutes, config):
    target, zero = config.sleep_target_hours * 60, config.sleep_zero_hours * 60
    return clamp(10 * (minutes - zero) / (target - zero))


def load_points(acwr, config):
    return clamp(10 * (config.acwr_zero - acwr) / (config.acwr_zero - config.acwr_full))


def missing(reason):
    return {"score": None, "reason": reason}


def scored(deviation):
    """Why a deviation cannot be scored, or None when it can."""
    if deviation is None:
        return "missing"
    if deviation["z"] is None:
        return deviation["level"]  # baseline_insufficient / no_baseline
    return None


def hrv_part(analyzer, day):
    metric = analyzer.preferred_hrv(day, nightly=True)
    if metric is None:
        return missing("no_nightly_hrv")
    if analyzer.entry(metric, day)["quality"] != "ok":
        return missing("partial")
    deviation = analyzer.deviation(metric, day, 30)
    reason = scored(deviation)
    if reason:
        return {**missing(reason), "metric": metric}
    return {
        "score": round(z_score_points(deviation["z"], analyzer.config.readiness), 1),
        "metric": metric,
        "z": round(deviation["z"], 2),
    }


def resting_hr_part(analyzer, day):
    entry = analyzer.entry("resting_hr", day)
    if entry["value"] is None:
        return missing("missing")
    if entry["quality"] != "ok":
        return missing("partial")
    deviation = analyzer.deviation("resting_hr", day, 30)
    reason = scored(deviation)
    if reason:
        return {**missing(reason), "method": entry.get("method")}
    return {
        # A resting heart rate above the baseline is the unfavourable direction.
        "score": round(z_score_points(-deviation["z"], analyzer.config.readiness), 1),
        "method": entry.get("method"),
        "z": round(deviation["z"], 2),
    }


def sleep_part(analyzer, day):
    night = analyzer.nights.get(day)
    if night is None:
        return missing("no_night")
    # A short night from the watch counts (it is the point); a phone-only estimate does not.
    if night["device_class"] != analyzer.config.source_priority[0]:
        return missing("not_top_device")
    config = analyzer.config.readiness
    # The whole sleep day, naps included: a night split in two is not a short night.
    return {
        "score": round(sleep_points(night["total_minutes"], config), 1),
        "sleep_minutes": round(night["total_minutes"]),
        "main_minutes": round(night["asleep_minutes"]),
        "target_minutes": round(config.sleep_target_hours * 60),
    }


def training_part(analyzer, day):
    daily = {
        d: (row["metrics"].get("trimp", {}).get("value") if row else None)
        for d, row in analyzer.rows.items()
    }
    # Load up to yesterday: readiness describes the state going into the day, so it does not
    # change once the day's workout is recorded and matches a check-in made in the morning.
    first = min(analyzer.rows) if analyzer.rows else None
    load = training_load(daily, day - timedelta(days=1), first)
    if load["acwr"] is None:
        if not load["chronic_reliable"]:
            return missing("chronic_unreliable")
        if not load["acute_reliable"]:
            return missing("acute_unreliable")
        return missing("no_chronic_load")
    return {
        "score": round(load_points(load["acwr"], analyzer.config.readiness), 1),
        "acwr": round(load["acwr"], 2),
    }


PARTS = {
    "hrv": hrv_part,
    "resting_hr": resting_hr_part,
    "sleep": sleep_part,
    "training_load": training_part,
}


def readiness(analyzer, day):
    config = analyzer.config.readiness
    components = {}
    for name, part in PARTS.items():
        components[name] = {**part(analyzer, day), "weight": config.weights[name]}
    used = {name: c for name, c in components.items() if c["score"] is not None and c["weight"] > 0}
    weight = sum(c["weight"] for c in used.values())
    if len(used) < config.min_components or not weight:
        score = None
    else:
        score = round(sum(c["score"] * c["weight"] for c in used.values()) / weight, 1)
    return {
        "score": score,
        "components": components,
        "used": sorted(used, key=list(PARTS).index),
        "min_components": config.min_components,
    }
