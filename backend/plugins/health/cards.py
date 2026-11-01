"""Metric cards for the App: current alerts, today's readiness, last night's sleep, the stages
of the last 7 nights, sleep regularity, resting heart rate, HRV and training load.

Every number comes from the summaries the Agent reads (health_get_daily / health_get_sleep),
so the cards and the briefings never disagree; this module only picks the numbers and words
them, with the existing card types (stat, chart, list). Like the summaries, it describes data
and trends against the owner's own baseline and is not a diagnosis.
"""

import math
from datetime import date, timedelta

from plugins.health.metrics import HRV_MS, HRV_NAMES, METRICS
from plugins.health.readiness import LABELS as PART_LABELS
from plugins.health.reports import hours_minutes

# Resting HR and HRV fall back to the latest measured day within this many days, so the card
# still shows yesterday's value in the morning before today's has been recorded.
LATEST_DAYS = 3
NIGHTS = 7
TRENDS = {"far_above": "up", "above": "up", "normal": "flat", "below": "down", "far_below": "down"}
STAGES = (
    ("deep", "深睡"),
    ("core", "核心"),
    ("rem", "REM"),
    ("awake", "清醒"),
    ("unspecified", "未分期"),
)
NAMES = {"hrv_ln_sdnn": "HRV", "hrv_ln_rmssd": "HRV"}
# Why a readiness part has no score, in a few words.
PART_REASONS = {
    "no_nightly_hrv": "昨晚没有 HRV",
    "no_night": "没有睡眠记录",
    "not_top_device": "只有手机记录",
    "baseline_insufficient": "基线数据还不够",
    "no_baseline": "还没有基线",
    "partial": "数据不完整",
    "missing": "暂无",
    "chronic_unreliable": "28 天的数据还不够",
    "acute_unreliable": "近 7 天缺数据",
    "no_chronic_load": "近 28 天没有训练",
}
EMPTY = (
    "还没有健康数据。iPhone App 上传健康数据或导入 Apple 健康导出文件之后，"
    "这里会显示睡眠、静息心率、HRV 和训练负荷。"
)


def health_cards(engine, today):
    daily = engine.daily(today, LATEST_DAYS, today=today)
    sleep = engine.sleep(today, NIGHTS, today=today)
    return metric_cards(daily, sleep)


def metric_cards(daily, sleep):
    days, nights = daily["days"], sleep["nights"]
    if all(d["data_status"] == "missing" for d in days) and all(
        n["quality"] == "missing" for n in nights
    ):
        return [card("list", "健康数据", items=[EMPTY])]
    as_of = date.fromisoformat(daily["as_of"])
    cards = [
        alerts_card(days, as_of),
        readiness_card(days),
        last_night_card(nights, as_of),
        stages_card(nights),
        regularity_card(sleep["regularity"]),
        resting_hr_card(days, as_of),
        hrv_card(days, as_of),
        training_card(daily["training_load"]),
    ]
    return [c for c in cards if c]


def card(card_type, title, **data):
    return {
        "type": card_type,
        "title": title,
        "data": {k: v for k, v in data.items() if v is not None},
    }


def day_name(text, as_of):
    day = date.fromisoformat(text)
    if day == as_of:
        return "今天"
    if day == as_of - timedelta(days=1):
        return "昨天"
    return f"{day.month}月{day.day}日"


def trend(deviation):
    """Arrow from the deviation level; none when there is no reliable baseline."""
    return TRENDS.get((deviation or {}).get("level"))


def joined(*parts):
    return " · ".join(p for p in parts if p)


def current_alerts(days):
    """Today's alerts, plus yesterday's for metrics that have no complete value today yet
    (a missing day would otherwise end every streak each morning)."""
    found = [dict(alert) for alert in days[-1]["alerts"]]
    if len(days) > 1:
        held = {(a["rule"], a["direction"]) for a in found}
        for alert in days[-2]["alerts"]:
            measured = days[-1]["metrics"][alert["metric"]]["quality"] == "ok"
            if (alert["rule"], alert["direction"]) not in held and not measured:
                found.append({**alert, "as_of": days[-2]["date"]})
    return found


def alerts_card(days, as_of):
    items = []
    for alert in current_alerts(days):
        metric = METRICS[alert["metric"]]
        word = metric.higher if alert["direction"] == "above" else metric.lower
        message = alert["message"]
        if "as_of" in alert:
            # The summary words the day it was computed for as 今日 (Analyzer._alert).
            message = message.replace("（今日 ", f"（{day_name(alert['as_of'], as_of)} ", 1)
        name = NAMES.get(metric.key, metric.label)
        items.append(
            {
                "title": f"{name}{' ' if name[-1].isascii() else ''}{word}基线",
                "subtitle": message,
                "detail": f"连续 {alert['consecutive_days']} 天",
            }
        )
    if not items:
        return card("list", "健康预警", items=["目前没有需要关注的指标。"], count=0)
    return card("list", "健康预警", items=items, count=len(items))


def last_night_card(nights, as_of):
    night = nights[-1]
    if night["quality"] == "missing":
        recent = next((n for n in reversed(nights) if n["quality"] != "missing"), None)
        earlier = (
            f"最近一晚（{day_name(recent['date'], as_of)}醒来）睡了 "
            f"{hours_minutes(recent['total_minutes'])}"
            if recent
            else f"最近 {len(nights)} 晚都没有记录"
        )
        return card(
            "stat",
            "昨晚睡眠",
            value="没有记录",
            label=joined("手表没戴，或者数据还没从 iPhone 上传", earlier),
        )
    deviation = night.get("vs_30d", {}).get("sleep_minutes")
    delta = None
    if trend(deviation):
        minutes = deviation["delta"]
        delta = (
            "和近 30 天中位数一样"
            if minutes == 0
            else f"比近 30 天中位数{'多' if minutes > 0 else '少'} {hours_minutes(minutes)}"
        )
    notes = []
    if night["quality"] != "ok":
        notes.append("记录不完整，不和基线比较")
    elif deviation and not trend(deviation):
        notes.append("基线数据还不够")
    if night["segments"] > 1:
        notes.append(f"分成 {night['segments']} 段")
    naps = night["naps"]
    if naps["count"]:
        # The value is the whole sleep day; the main sleep and the naps are shown apart.
        timing = (
            f"主睡眠 {hours_minutes(night['asleep_minutes'])}（{night['onset']}–{night['wake']}）",
            f"小睡 {naps['count']} 次 {hours_minutes(naps['minutes'])}",
        )
    else:
        timing = (f"{night['onset']} 入睡", f"{night['wake']} 醒来")
    return card(
        "stat",
        "昨晚睡眠",
        value=hours_minutes(night["total_minutes"]),
        label=joined(*timing, *notes),
        delta=delta,
        trend=trend(deviation),
    )


def stages_card(nights):
    """Stacked bars, hours per stage of the main sleep with the naps on top, so a bar is the
    whole sleep day; a night without stages counts as unspecified sleep, a night without a
    record as zero so the dates stay in line."""
    labels = []
    for night in nights:
        day = date.fromisoformat(night["date"])
        labels.append(f"{day.month}/{day.day}")
    series = []
    for key, name in STAGES:
        values = []
        for night in nights:
            stages = night.get("stages_minutes")
            if stages:
                minutes = stages[key]
            elif key == "unspecified" and night["quality"] != "missing":
                minutes = night["asleep_minutes"]
            else:
                minutes = 0
            values.append(round(minutes / 60, 2))
        if any(values):
            points = [{"x": x, "y": y} for x, y in zip(labels, values, strict=True)]
            series.append({"name": name, "points": points})
    naps = [round(night["naps"]["minutes"] / 60, 2) if "naps" in night else 0 for night in nights]
    if any(naps):
        points = [{"x": x, "y": y} for x, y in zip(labels, naps, strict=True)]
        series.append({"name": "小睡", "points": points})
    if not series:
        return None
    return card("chart", f"最近 {len(nights)} 晚睡眠分期", kind="bar", unit="小时", series=series)


def regularity_card(regularity):
    week, month = regularity["sri_7d"], regularity["sri_30d"]
    spreads = []
    for name, key in (("入睡", "onset"), ("醒来", "wake")):
        spread = regularity[f"{key}_variability_7d"]["sd_minutes"]
        if spread is not None:
            spreads.append(f"{name}时间波动 ±{spread} 分钟")
    if not week["reliable"] or week["value"] is None:
        return card(
            "stat",
            "作息规律",
            value="数据不足",
            label=joined("近 7 天完整的睡眠记录太少，暂时算不出规律性", *spreads),
        )
    delta = None
    if month["reliable"] and month["value"] is not None:
        delta = f"近 30 天 {month['value']}"
    return card(
        "stat",
        "作息规律",
        value=week["value"],
        unit="SRI",
        label=joined("近 7 天睡眠规律性指数，越高越规律", *spreads),
        delta=delta,
    )


def latest(days, metric):
    for day in reversed(days[-LATEST_DAYS:]):
        view = day["metrics"][metric]
        if view["quality"] == "ok" and view["value"] is not None:
            return day["date"], view
    return None, None


def resting_hr_card(days, as_of):
    day, view = latest(days, "resting_hr")
    if view is None:
        return card(
            "stat", "静息心率", value="暂无", label=f"最近 {LATEST_DAYS} 天没有静息心率记录"
        )
    deviation = view.get("vs_30d")
    baseline = None
    if deviation and "median" in deviation:
        baseline = f"近 30 天中位数 {deviation['median']} 次/分"
    method = "按睡眠心率估算" if view.get("method") == "sleep_hr" else None
    return card(
        "stat",
        "静息心率",
        value=view["value"],
        unit="次/分",
        label=joined(day_name(day, as_of), baseline, method),
        delta=f"{deviation['delta']:+.1f}" if trend(deviation) else None,
        trend=trend(deviation),
    )


def readiness_card(days):
    today = days[-1].get("readiness")
    if today is None:
        return None
    parts = []
    for name, part in today["components"].items():
        if not part["weight"]:
            continue
        shown = part["score"] if part["score"] is not None else PART_REASONS[part["reason"]]
        parts.append(f"{PART_LABELS[name]} {shown}")
    logged = days[-1].get("checkin") or {}
    if logged.get("apple_fitness_readiness") is not None:
        parts.append(f"你记的健身 App {logged['apple_fitness_readiness']:g}")
    if logged.get("perceived_readiness") is not None:
        parts.append(f"体感 {logged['perceived_readiness']:g}")
    yesterday = days[-2].get("readiness") if len(days) > 1 else None
    previous = yesterday["score"] if yesterday else None
    if today["score"] is None:
        return card(
            "stat",
            "今日准备度",
            value="数据不足",
            label=joined(f"至少要 {today['min_components']} 项有数据", *parts),
        )
    return card(
        "stat",
        "今日准备度",
        value=today["score"],
        unit="/ 10",
        label=joined(*parts),
        delta=f"昨天 {previous}" if previous is not None else None,
    )


def latest_hrv(days):
    """The latest day (within LATEST_DAYS) with a complete value of its preferred measure."""
    for day in reversed(days[-LATEST_DAYS:]):
        metric = day.get("hrv_preferred")
        if metric and day["metrics"][metric]["quality"] == "ok":
            return day["date"], metric, day["metrics"][metric]
    return None, None, None


def hrv_card(days, as_of):
    """RMSSD when the day is judged by it, else SDNN (Apple's original HRV). Baselines are on
    ln(ms) of the same measure, so the change is a ratio."""
    day, metric, view = latest_hrv(days)
    if view is None:
        return card("stat", "HRV", value="暂无", label=f"最近 {LATEST_DAYS} 天没有 HRV 记录")
    deviation = view.get("vs_30d")
    baseline = None
    if deviation and "median" in deviation:
        baseline = f"近 30 天中位数 {math.exp(deviation['median']):.0f} 毫秒"
    method = {"sleep": "夜间测量", "day": "白天测量"}.get(view.get("method"))
    delta = None
    if trend(deviation):
        delta = f"{(math.exp(deviation['delta']) - 1) * 100:+.0f}%"
    return card(
        "stat",
        "HRV",
        value=round(view[HRV_MS[metric]]),
        unit=f"毫秒（{HRV_NAMES[metric]}）",
        label=joined(day_name(day, as_of), baseline, method),
        delta=delta,
        trend=trend(deviation),
    )


def training_card(load):
    notes = []
    if not load["acute_reliable"]:
        notes.append(f"近 7 天有 {load['acute_missing_days']} 天缺少数据")
    if load["chronic_reliable"]:
        notes.append(f"近 28 天平均每周 {load['chronic_28d_trimp'] / 4:.0f}")
    else:
        notes.append("28 天的数据还不够")
    if load["acwr_reference"] is not None:
        notes.append(f"急慢性负荷比 {load['acwr_reference']}（仅供参考）")
    return card(
        "stat",
        "训练负荷",
        value=round(load["acute_7d_trimp"]),
        unit="TRIMP（近 7 天）",
        label=joined(*notes),
    )
