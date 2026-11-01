"""Model-facing JSON: per-day aggregates, baselines, deviations and alert flags only.

Nothing here exposes individual readings, sleep stage records, source names (they usually
contain the owner's name) or identifiers. Baselines and alerts are computed on read, so
threshold or rule changes apply immediately without recomputing stored rows.
"""

import math
from collections import Counter
from datetime import timedelta

from plugins.health.metrics import HRV_MEASURES, HRV_MS, HRV_NAMES, METRICS, SLEEP_METRICS
from plugins.health.observations import CHECKIN_KINDS
from plugins.health.readiness import NOTE as READINESS_NOTE
from plugins.health.readiness import readiness
from plugins.health.sleep import sleep_regularity_index
from plugins.health.stats import level, mad, median, robust_z, stdev
from plugins.health.training import training_load

NOTICE = (
    "以下是可穿戴设备按天汇总的数据与本人历史基线的比较，只描述数据和趋势，不是医学诊断；"
    "预警只表示建议关注。"
)
ACWR_NOTE = "急慢性负荷比（ACWR）仅供参考：学界对它能否预测受伤风险存在争议，不宜据此做决定。"
# Totals of the current day are still growing, so they never count as complete.
IN_PROGRESS_METRICS = {"steps", "active_energy", "trimp"}
WINDOWS = (7, 30)


def clock(minutes, cutoff_hour):
    """Minutes after the sleep-day cutoff as a local HH:MM."""
    total = round(cutoff_hour * 60 + minutes)
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


class Analyzer:
    def __init__(self, rows, nights, config, today):
        self.rows, self.nights, self.config, self.today = rows, nights, config, today
        self._baselines = {}

    def entry(self, metric, day):
        row = self.rows.get(day)
        entry = dict((row or {}).get("metrics", {}).get(metric) or {})
        entry.setdefault("value", None)
        entry.setdefault("quality", "missing")
        if day == self.today and metric in IN_PROGRESS_METRICS and entry["quality"] == "ok":
            entry["quality"] = "partial"
        return entry

    def baseline(self, metric, day, window, method=None):
        """Median and MAD of the `window` days before `day` (not including it), using only
        complete ("ok") values measured with the same method."""
        key = (metric, day, window, method)
        if key not in self._baselines:
            entries = [
                self.entry(metric, day - timedelta(days=offset)) for offset in range(1, window + 1)
            ]
            entries = [e for e in entries if e["quality"] == "ok" and e["value"] is not None]
            if METRICS[metric].method_aware:
                if method is None and entries:
                    method = Counter(e.get("method") for e in entries).most_common(1)[0][0]
                entries = [e for e in entries if e.get("method") == method]
            values = [e["value"] for e in entries]
            result = {
                "n": len(values),
                "reliable": len(values) >= self.config.baseline_min_days[window],
            }
            if METRICS[metric].method_aware:
                result["method"] = method
            if values:
                center = median(values)
                result.update(median=center, mad=mad(values, center))
            self._baselines[key] = result
        return self._baselines[key]

    def deviation(self, metric, day, window):
        entry = self.entry(metric, day)
        if entry["value"] is None:
            return None
        base = self.baseline(metric, day, window, entry.get("method"))
        result = {"n": base["n"], "reliable": base["reliable"]}
        if "median" not in base:
            return {**result, "z": None, "level": "no_baseline"}
        result.update(median=base["median"], mad=base["mad"], delta=entry["value"] - base["median"])
        if not METRICS[metric].scored:
            return {**result, "z": None, "level": "not_scored"}
        if not base["reliable"]:
            return {**result, "z": None, "level": "baseline_insufficient"}
        z = robust_z(entry["value"], base["median"], base["mad"], self.config.mad_floors[metric])
        return {**result, "z": z, "level": level(z, self.config.notable_z, self.config.strong_z)}

    def preferred_hrv(self, day, nightly=False):
        """The HRV measure `day` is judged by: RMSSD when it has a complete value there and a
        reliable 30-day baseline of its own, else SDNN under the same conditions, else the
        first measure with any value (shown, but nothing to compare with). So right after the
        switch to watchOS 27, days keep being judged by SDNN until RMSSD has enough history.
        `nightly` only considers values measured during the main sleep."""
        candidates = []
        for metric in HRV_MEASURES:
            entry = self.entry(metric, day)
            if entry["value"] is None or (nightly and entry.get("method") != "sleep"):
                continue
            candidates.append((metric, entry))
        for metric, entry in candidates:
            if entry["quality"] == "ok":
                if self.baseline(metric, day, 30, entry.get("method"))["reliable"]:
                    return metric
        return candidates[0][0] if candidates else None

    def rule_metric(self, rule, day):
        """The stored metric a rule looks at on `day` ("hrv" follows preferred_hrv)."""
        return self.preferred_hrv(day) if rule["metric"] == "hrv" else rule["metric"]

    def alerts(self, day):
        """Rules fire when a metric deviates in the same direction on N consecutive days."""
        found = []
        for rule in self.config.alert_rules:
            if not rule["enabled"]:
                continue
            window = 7 if rule["baseline"] == "7d" else 30
            directions = (
                ("above", "below") if rule["direction"] == "either" else (rule["direction"],)
            )
            for direction in directions:
                sign, streak = (1 if direction == "above" else -1), 0
                for offset in range(60):
                    current = day - timedelta(days=offset)
                    metric = self.rule_metric(rule, current)
                    if metric is None or self.entry(metric, current)["quality"] != "ok":
                        break  # a missing day ends the run: "consecutive" means consecutive
                    deviation = self.deviation(metric, current, window)
                    if deviation is None or deviation["z"] is None:
                        break
                    # The direction must hold strictly, so z = 0 never counts even when z is 0.
                    if (
                        sign * deviation["z"] <= 0
                        or sign * deviation["z"] < rule["z"]
                        or sign * deviation["delta"] < rule["min_delta"]
                    ):
                        break
                    streak += 1
                if streak >= rule["days"]:
                    found.append(self._alert(rule, direction, streak, day, window))
        return found

    def _alert(self, rule, direction, streak, day, window):
        key = self.rule_metric(rule, day)
        metric = METRICS[key]
        deviation = self.deviation(key, day, window)
        value = self.entry(key, day)["value"]
        word = metric.higher if direction == "above" else metric.lower
        message = (
            f"{metric.label}已连续 {streak} 天明显{word}本人近 {window} 天基线"
            f"（今日 {self.describe(key, value)}，"
            f"基线中位数 {self.describe(key, deviation['median'])}），建议关注。"
        )
        return {
            "rule": rule["id"],
            "metric": key,
            "direction": direction,
            "consecutive_days": streak,
            "baseline": rule["baseline"],
            "severity": "attention",
            "message": message,
        }

    def describe(self, metric, value):
        if METRICS[metric].clock:
            return clock(value, self.config.sleep_day_cutoff_hour)
        if metric in HRV_NAMES:
            return f"{HRV_NAMES[metric]} {math.exp(value):.0f} ms"
        return f"{value:.{METRICS[metric].decimals}f} {METRICS[metric].unit}"

    def format_value(self, metric, value):
        if value is None:
            return None
        if METRICS[metric].clock:
            return clock(value, self.config.sleep_day_cutoff_hour)
        return round(value, METRICS[metric].decimals)

    def metric_view(self, metric, day):
        entry = self.entry(metric, day)
        view = {
            "value": self.format_value(metric, entry["value"]),
            "unit": METRICS[metric].unit,
            "quality": entry["quality"],
        }
        if entry.get("method"):
            view["method"] = entry["method"]
        if metric in HRV_MS and entry["value"] is not None:
            view[HRV_MS[metric]] = round(math.exp(entry["value"]), 1)
        if entry["quality"] == "ok":
            for window in WINDOWS:
                view[f"vs_{window}d"] = self.deviation_view(metric, day, window)
        return view

    def deviation_view(self, metric, day, window):
        deviation = self.deviation(metric, day, window)
        decimals = METRICS[metric].decimals
        view = {"level": deviation["level"], "n": deviation["n"]}
        if "median" in deviation:
            view["median"] = self.format_value(metric, deviation["median"])
            # Clock deltas are minutes, positive meaning later.
            view["delta"] = (
                round(deviation["delta"], decimals) if decimals else round(deviation["delta"])
            )
        view["z"] = None if deviation["z"] is None else round(deviation["z"], 2)
        return view

    def training(self, day, hr_max):
        daily = {
            d: (row["metrics"].get("trimp", {}).get("value") if row else None)
            for d, row in self.rows.items()
        }
        load = training_load(daily, day, min(self.rows) if self.rows else None)
        return {
            "acute_7d_trimp": round(load["acute_7d"], 1),
            "acute_missing_days": load["acute_missing_days"],
            "acute_reliable": load["acute_reliable"],
            "chronic_28d_trimp": round(load["chronic_28d"], 1),
            "chronic_missing_days": load["chronic_missing_days"],
            "chronic_reliable": load["chronic_reliable"],
            "acwr_reference": None if load["acwr"] is None else round(load["acwr"], 2),
            "hr_max": hr_max,
            "note": ACWR_NOTE,
        }


def date_range(day, days):
    return [day - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def envelope(kind, analyzer, day):
    return {
        "schema": f"assistant.health.{kind}/v1",
        "timezone": analyzer.config.timezone,
        "as_of": day.isoformat(),
        "notice": NOTICE,
    }


def daily_payload(analyzer, day, days, hr_max):
    entries = []
    for current in date_range(day, days):
        row = analyzer.rows.get(current)
        extras = row["extras"] if row else {}
        trimp = analyzer.entry("trimp", current)["value"]
        entries.append(
            {
                "date": current.isoformat(),
                "in_progress": current == analyzer.today,
                "data_status": row["status"] if row else "missing",
                "data_issues": row["issues"] if row else ["no_data"],
                "wear_hours": row["wear_hours"] if row else 0,
                "metrics": {m: analyzer.metric_view(m, current) for m in METRICS},
                "workouts": {
                    "count": extras.get("workouts", 0),
                    "minutes": round(extras.get("workout_minutes", 0)),
                    "trimp": None if trimp is None else round(trimp, 1),
                },
                # Which HRV measure this day is judged by (RMSSD or SDNN; never mixed).
                "hrv_preferred": analyzer.preferred_hrv(current),
                # What the owner logged for calibration (Apple Fitness readiness 0-10, own
                # rating 1-5); null when nothing was logged that day.
                "checkin": {kind: extras.get("checkin", {}).get(kind) for kind in CHECKIN_KINDS},
                "alerts": analyzer.alerts(current),
                "readiness": readiness(analyzer, current),
            }
        )
    return {
        **envelope("daily", analyzer, day),
        "days": entries,
        "training_load": analyzer.training(day, hr_max),
        "readiness_note": READINESS_NOTE,
    }


def sleep_payload(analyzer, day, days):
    cutoff = analyzer.config.sleep_day_cutoff_hour
    nights = []
    for current in date_range(day, days):
        night = analyzer.nights.get(current)
        if night is None:
            nights.append({"date": current.isoformat(), "quality": "missing"})
            continue
        view = {
            "date": current.isoformat(),
            "quality": night["quality"],
            "source": night["device_class"],
            "onset": clock(night["onset_minutes"], cutoff),
            "wake": clock(night["wake_minutes"], cutoff),
            # Main sleep and the whole sleep day (naps included); onset, wake and stages
            # describe the main sleep.
            "asleep_minutes": round(night["asleep_minutes"]),
            "total_minutes": round(night["total_minutes"]),
            "stages_minutes": {
                "deep": round(night["deep_minutes"]),
                "core": round(night["core_minutes"]),
                "rem": round(night["rem_minutes"]),
                "awake": round(night["awake_minutes"]),
                "unspecified": round(night["unspecified_minutes"]),
            }
            if night["has_stages"]
            else None,
            "segments": night["segments"],
            "naps": {"count": night["naps"], "minutes": round(night["nap_minutes"])},
        }
        if night["quality"] == "ok":
            for window in WINDOWS:
                view[f"vs_{window}d"] = {
                    metric: analyzer.deviation_view(metric, current, window)
                    for metric in ("sleep_minutes", "sleep_onset", "wake_time")
                    if analyzer.entry(metric, current)["value"] is not None
                }
        nights.append(view)
    return {
        **envelope("sleep", analyzer, day),
        "nights": nights,
        "regularity": regularity(analyzer, day),
    }


def regularity(analyzer, day):
    result = {}
    for window in WINDOWS:
        days = date_range(day, window)
        masks = []
        for current in days:
            night = analyzer.nights.get(current)
            masks.append(night["mask"] if night and night["quality"] == "ok" else None)
        sri, pairs = sleep_regularity_index(masks)
        reliable = pairs >= analyzer.config.sri_min_pairs[window]
        result[f"sri_{window}d"] = {
            "value": None if sri is None else round(sri, 1),
            "valid_day_pairs": pairs,
            "reliable": reliable,
        }
        for metric, name in (("sleep_onset", "onset"), ("wake_time", "wake")):
            values = [
                analyzer.entry(metric, current)["value"]
                for current in days
                if analyzer.entry(metric, current)["quality"] == "ok"
            ]
            spread = stdev(values)
            result[f"{name}_variability_{window}d"] = {
                "sd_minutes": None if spread is None else round(spread),
                "mad_minutes": round(mad(values)) if values else None,
                "n": len(values),
            }
    return result


def baselines_payload(analyzer, day):
    metrics = {}
    for metric in METRICS:
        method = analyzer.entry(metric, day).get("method")
        view = {"unit": METRICS[metric].unit}
        for window in WINDOWS:
            base = analyzer.baseline(metric, day, window, method)
            entry = {"n": base["n"], "reliable": base["reliable"]}
            if "method" in base:
                entry["method"] = base["method"]
            if "median" in base:
                entry["median"] = analyzer.format_value(metric, base["median"])
                entry["mad"] = round(base["mad"], max(METRICS[metric].decimals, 1))
            view[f"{window}d"] = entry
        metrics[metric] = view
    return {
        **envelope("baselines", analyzer, day),
        "windows": {
            f"{window}d": [
                (day - timedelta(days=window)).isoformat(),
                (day - timedelta(days=1)).isoformat(),
            ]
            for window in WINDOWS
        },
        "sleep_metrics": list(SLEEP_METRICS),
        "metrics": metrics,
    }
