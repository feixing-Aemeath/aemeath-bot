"""Morning briefings and weekly reports: code computes every number, the Agent only words them.

One report is one (kind, date):

1. prepare() computes the input bundle - the health_get_daily / health_get_sleep /
   health_get_baselines payloads, values already converted for reading (7小时40分), weekly
   averages and changes, and the training-goal note from long-term memory - and stores it
   with status pending.
2. The core runs prompts/health_<kind>.md through the configured Agent. The Agent reads the
   bundle with health_report_input and submits the text with health_report_write.
3. check_report() accepts a submission only when every number in it appears in the bundle
   (rounding a value to fewer decimals is allowed), no quantity is spelled in Chinese
   numerals, and no diagnosis or medication wording appears. A rejection goes back to the
   Agent with the reasons so it can rewrite; it is never stored as the report.
4. An accepted report becomes ready, is written to long-term memory and shown as a card.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from core.db import uid
from plugins.health.metrics import HRV_MEASURES, HRV_MS, HRV_NAMES, METRICS
from plugins.health.storage import read_nights

KINDS = {"morning": "晨间健康简报", "weekly": "健康周报"}
MEMORY_FOLDERS = {"morning": "健康/晨间简报", "weekly": "健康/周报"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEK_METRICS = (
    "sleep_minutes",
    "deep_minutes",
    "rem_minutes",
    "resting_hr",
    "steps",
    "active_energy",
)
NUMBER = re.compile(r"\d+(?:\.\d+)?")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
UNITS = "小时|分钟|天|晚|次|步|千步|万步|公里|千米|周|个百分点|岁|千卡|大卡|毫秒"
# "三天", "七个半小时", "十分钟"; a lone 一 ("这一周", "一次") is ordinary wording.
CHINESE_QUANTITY = re.compile(rf"([零一二两三四五六七八九十百千万半]+)\s*个?\s*(?:{UNITS})")
MEDICAL_TERMS = ("确诊", "诊断为", "患有", "疑似", "服用", "用药", "药物", "剂量", "处方", "吃药")


@dataclass(frozen=True)
class ReportConfig:
    enabled: bool = True
    morning_earliest_hour: int = 4
    morning_deadline_hour: int = 10
    weekly_weekday: str = "sun"
    weekly_hour: int = 21
    goals_path: str = "健康/训练目标.md"
    max_attempts: int = 2
    max_rejections: int = 5

    @classmethod
    def from_dict(cls, values):
        config = cls(**(values or {}))
        if config.morning_earliest_hour > config.morning_deadline_hour:
            raise ValueError("morning_earliest_hour must not be after morning_deadline_hour")
        if config.weekly_weekday not in WEEKDAYS:
            raise ValueError("weekly_weekday must be mon..sun")
        return config


def hours_minutes(minutes):
    """ "7小时40分" for 460; the Agent must not convert minutes itself."""
    total = round(abs(minutes))
    hours, rest = divmod(total, 60)
    return f"{hours}小时{rest}分" if hours else f"{rest}分"


def clock_minutes(text, cutoff_hour):
    """ "23:20" as minutes after the sleep-day cutoff, so means do not wrap at midnight."""
    hour, minute = map(int, text.split(":"))
    return (hour * 60 + minute - cutoff_hour * 60) % 1440


def clock_text(minutes, cutoff_hour):
    total = round(cutoff_hour * 60 + minutes) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def mean(values, decimals):
    if not values:
        return None
    value = sum(values) / len(values)
    return round(value) if decimals == 0 else round(value, decimals)


def day_values(payload, metric):
    """Complete daily values of `metric` (days still in progress excluded)."""
    values = []
    for day in payload["days"]:
        view = day["metrics"][metric]
        if view["quality"] != "ok" or view["value"] is None or day["in_progress"]:
            continue
        values.append(view[HRV_MS[metric]] if metric in HRV_MS else view["value"])
    return values


def readiness_values(payload):
    return [
        day["readiness"]["score"]
        for day in payload["days"]
        if not day["in_progress"] and day["readiness"]["score"] is not None
    ]


def week_summary(engine, day, today):
    """This week (the 7 days ending on `day`) against the 7 days before, computed here."""
    cutoff = engine.config.sleep_day_cutoff_hour
    this, last = engine.daily(day, 7, today), engine.daily(day - timedelta(days=7), 7, today)
    sleep_this = engine.sleep(day, 7, today)
    sleep_last = engine.sleep(day - timedelta(days=7), 7, today)
    metrics = {}
    for metric in (*WEEK_METRICS, *reversed(HRV_MEASURES)):
        info = METRICS[metric]
        hrv = metric in HRV_MS
        decimals = 1 if hrv else info.decimals
        current, previous = day_values(this, metric), day_values(last, metric)
        if metric == "hrv_ln_rmssd" and not current and not previous:
            continue  # no RMSSD before watchOS 27
        entry = {
            # SDNN and RMSSD are averaged separately and never compared with each other.
            "label": f"HRV（{HRV_NAMES[metric]}）" if hrv else info.label,
            "unit": "ms" if hrv else info.unit,
            "this_week": {
                "mean": mean(current, decimals),
                "min": min(current) if current else None,
                "max": max(current) if current else None,
                "days": len(current),
            },
            "last_week": {"mean": mean(previous, decimals), "days": len(previous)},
        }
        if entry["this_week"]["mean"] is not None and entry["last_week"]["mean"] is not None:
            change = entry["this_week"]["mean"] - entry["last_week"]["mean"]
            entry["change"] = round(change) if decimals == 0 else round(change, decimals)
        else:
            entry["change"] = None
        if metric == "sleep_minutes" and entry["this_week"]["mean"] is not None:
            entry["this_week"]["mean_text"] = hours_minutes(entry["this_week"]["mean"])
            if entry["change"] is not None:
                entry["change_text"] = hours_minutes(entry["change"])
        metrics[metric] = entry

    def timing(payload, key):
        values = [
            clock_minutes(night[key], cutoff)
            for night in payload["nights"]
            if night.get("quality") == "ok"
        ]
        return clock_text(sum(values) / len(values), cutoff) if values else None

    workouts = {}
    for name, payload in (("this_week", this), ("last_week", last)):
        days = payload["days"]
        workouts[name] = {
            "count": sum(d["workouts"]["count"] for d in days),
            "minutes": sum(d["workouts"]["minutes"] for d in days),
            "trimp": round(sum(d["workouts"]["trimp"] or 0 for d in days), 1),
            "active_days": sum(1 for d in days if d["workouts"]["count"]),
        }
    alerts = {}
    for entry in this["days"]:
        for alert in entry["alerts"]:
            alerts[alert["rule"]] = alert  # the latest day of each rule
    scores, before = readiness_values(this), readiness_values(last)
    readiness_week = {
        "this_week": {"mean": mean(scores, 1), "days": len(scores)},
        "last_week": {"mean": mean(before, 1), "days": len(before)},
        "note": this["readiness_note"],
    }
    if scores and before:
        readiness_week["change"] = round(mean(scores, 1) - mean(before, 1), 1)
    return {
        "range": [this["days"][0]["date"], this["days"][-1]["date"]],
        "previous_range": [last["days"][0]["date"], last["days"][-1]["date"]],
        "metrics": metrics,
        "readiness": readiness_week,
        "sleep_timing": {
            "this_week": {
                "onset_mean": timing(sleep_this, "onset"),
                "wake_mean": timing(sleep_this, "wake"),
                "nights": sum(1 for n in sleep_this["nights"] if n.get("quality") == "ok"),
            },
            "last_week": {
                "onset_mean": timing(sleep_last, "onset"),
                "wake_mean": timing(sleep_last, "wake"),
                "nights": sum(1 for n in sleep_last["nights"] if n.get("quality") == "ok"),
            },
            "regularity": sleep_this["regularity"],
        },
        "workouts": workouts,
        "alerts": list(alerts.values()),
    }


def build_input(engine, kind, day, today, goals=None):
    """Everything the Agent may cite for one report, as plain JSON."""
    if kind not in KINDS:
        raise ValueError("unknown report kind")
    if kind == "morning":
        bundle = {
            "daily": engine.daily(day, 2, today),
            "sleep": engine.sleep(day, 7, today),
            "baselines": engine.baselines(day, today),
        }
        night = bundle["sleep"]["nights"][-1]
        display = {"last_night_date": night["date"], "last_night_quality": night["quality"]}
        if night["quality"] != "missing":
            # The whole sleep day; with naps, the main sleep and the naps as well.
            display["last_night_asleep"] = hours_minutes(night["total_minutes"])
            if night["naps"]["count"]:
                display["last_night_main_sleep"] = hours_minutes(night["asleep_minutes"])
                display["last_night_naps"] = {
                    "count": night["naps"]["count"],
                    "asleep": hours_minutes(night["naps"]["minutes"]),
                }
            vs = night.get("vs_30d", {}).get("sleep_minutes")
            if vs and "delta" in vs:
                display["last_night_vs_30d"] = {
                    "direction": "多" if vs["delta"] >= 0 else "少",
                    "difference": hours_minutes(vs["delta"]),
                }
    else:
        bundle = {
            "week": week_summary(engine, day, today),
            "baselines": engine.baselines(day, today),
        }
        display = {}
    return {
        "schema": f"assistant.health.report_input.{kind}/v1",
        "kind": kind,
        "title_hint": f"{day.month}月{day.day}日{KINDS[kind]}",
        "date": day.isoformat(),
        "timezone": engine.config.timezone,
        **bundle,
        "display": display,
        "goals": goals,
    }


# Dates and clock times as text writes them. They are checked against the dates and times
# in the input only, so a day number such as 12 never makes "12%" look like a known value.
DATE_FORMS = (
    re.compile(r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?"),
    re.compile(r"(?<![\d.])(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?![\d.])"),
    re.compile(r"(?<![\d.])()(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?"),
    re.compile(r"(?<![\d./])()(\d{1,2})/(\d{1,2})(?![\d./])"),
)
CLOCK_FORM = re.compile(r"(?<![\d.])(\d{1,2})\s*[:：]\s*(\d{2})(?![\d.])")


def dates_in(text):
    for pattern in DATE_FORMS:
        for match in pattern.finditer(text):
            year, month, day = match.groups()
            if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                yield match, (int(year) if year else None, int(month), int(day))


class Allowed:
    """Values, dates and clock times that a report may cite, collected from the input."""

    def __init__(self, bundle):
        self.values, self.dates, self.clocks = set(), set(), set()
        self.collect(bundle)

    def collect(self, item):
        if isinstance(item, dict):
            for key, value in item.items():
                self.text(str(key))
                self.collect(value)
        elif isinstance(item, list | tuple):
            for value in item:
                self.collect(value)
        elif isinstance(item, bool) or item is None:
            return
        elif isinstance(item, int | float):
            self.values.add(abs(Decimal(str(item))))  # reports write the sign as words
        else:
            self.text(str(item))

    def text(self, text):
        for match, parts in list(dates_in(text)):
            self.dates.add(parts)
            text = text.replace(match.group(0), " ")
        for match in CLOCK_FORM.finditer(text):
            self.clocks.add((int(match[1]), int(match[2])))
        text = CLOCK_FORM.sub(" ", text)
        for token in NUMBER.findall(text):
            self.values.add(Decimal(token))

    def value(self, token):
        value = Decimal(token)
        places = len(token.split(".")[1]) if "." in token else 0
        quantum = Decimal(1).scaleb(-places)
        return any(
            candidate == value or candidate.quantize(quantum, rounding=ROUND_HALF_UP) == value
            for candidate in self.values
        )

    def date(self, year, month, day):
        return any(
            (month, day) == (m, d) and None in (year, y) or (year, month, day) == (y, m, d)
            for y, m, d in self.dates
        )


def check_report(title, body, bundle):
    """Reasons to reject a submission (empty when it is acceptable)."""
    text = unicodedata.normalize("NFKC", f"{title}\n{body}")
    text = THOUSANDS.sub("", text)
    allowed = Allowed(bundle)
    unsupported = set()
    for match, (year, month, day) in list(dates_in(text)):
        if not allowed.date(year, month, day):
            unsupported.add(match.group(0).strip())
        text = text.replace(match.group(0), " ")
    for match in CLOCK_FORM.finditer(text):
        if (int(match[1]), int(match[2])) not in allowed.clocks:
            unsupported.add(match.group(0).strip())
    text = CLOCK_FORM.sub(" ", text)
    unsupported |= {token for token in NUMBER.findall(text) if not allowed.value(token)}
    unsupported = sorted(unsupported, key=lambda t: (len(t), t))
    errors = []
    if unsupported:
        errors.append(
            {
                "code": "unsupported_numbers",
                "values": unsupported,
                "message": "这些数字不在 health_report_input 返回的数据里，不能自己换算、相加、"
                "求差或估算；请改成数据里已有的数字，或删掉。",
            }
        )
    spelled = sorted({m.group(0) for m in CHINESE_QUANTITY.finditer(text) if m.group(1) != "一"})
    if spelled:
        errors.append(
            {
                "code": "chinese_numerals",
                "values": spelled,
                "message": "数量请照抄数据里的阿拉伯数字，不要写成中文数字。",
            }
        )
    medical = [term for term in MEDICAL_TERMS if term in text]
    if medical:
        errors.append(
            {
                "code": "medical_terms",
                "values": medical,
                "message": "不做诊断、不提疾病判断或用药；只描述数据和趋势，必要时建议就医。",
            }
        )
    return errors


class Reports:
    """health_reports rows for the health plugin. Synchronous; callers use threads."""

    def __init__(self, db, engine, config, memory=None):
        self.db, self.engine, self.config, self.memory = db, engine, config, memory

    def row(self, kind, day):
        rows = self.db.rows(
            "SELECT * FROM health_reports WHERE kind=? AND date=?", (kind, day.isoformat())
        )
        return rows[0] if rows else None

    def status(self, kind, day):
        row = self.row(kind, day)
        return row["status"] if row else None

    def goals(self):
        if self.memory is None:
            return None
        try:
            text = self.memory.read(self.config.goals_path)["content"]
        except (OSError, ValueError):
            return None
        return {"path": self.config.goals_path, "text": text[:2000]}

    def prepare(self, kind, day, today):
        bundle = build_input(self.engine, kind, day, today, self.goals())
        timestamp = datetime.now(UTC).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO health_reports(id,date,kind,status,input_json,created_at,updated_at)"
                " VALUES(?,?,?,'pending',?,?,?) ON CONFLICT(date,kind) DO UPDATE SET "
                "status='pending',input_json=excluded.input_json,rejections=0,error=NULL,"
                "harness=NULL,updated_at=excluded.updated_at",
                (uid(), day.isoformat(), kind, json.dumps(bundle, ensure_ascii=False),
                 timestamp, timestamp),
            )  # fmt: skip
        return bundle

    def input(self, kind, day, today):
        row = self.row(kind, day)
        if row and row["input_json"] not in (None, "{}"):
            return json.loads(row["input_json"])
        return self.prepare(kind, day, today)  # an Agent asked outside a scheduled job

    def submit(self, kind, day, title, body, today):
        bundle = self.input(kind, day, today)
        row = self.row(kind, day)
        timestamp = datetime.now(UTC).isoformat()
        if row["rejections"] >= self.config.max_rejections:
            return {"accepted": False, "errors": [{"code": "too_many_attempts"}]}
        errors = check_report(title, body, bundle)
        if errors:
            self.db.execute(
                "UPDATE health_reports SET rejections=rejections+1,error='report_rejected',"
                "updated_at=? WHERE id=?",
                (timestamp, row["id"]),
            )
            left = self.config.max_rejections - row["rejections"] - 1
            return {"accepted": False, "errors": errors, "attempts_left": max(left, 0)}
        self.db.execute(
            "UPDATE health_reports SET status='ready',title=?,body=?,error=NULL,updated_at=? "
            "WHERE id=?",
            (title, body, timestamp, row["id"]),
        )
        if self.memory is not None:
            # Only this report's generated path uses the trusted writer.
            path = self.memory.path(f"{MEMORY_FOLDERS[kind]}/{day.isoformat()}.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.memory.save(path, f"# {title}\n\n{body}\n")
        return {
            "accepted": True,
            "report": {"id": row["id"], "kind": kind, "date": day.isoformat()},
        }

    def finish(self, kind, day, harness=None, error=None):
        timestamp = datetime.now(UTC).isoformat()
        if error:
            self.db.execute(
                "UPDATE health_reports SET status='failed',error=?,updated_at=? "
                "WHERE kind=? AND date=? AND status!='ready'",
                (error, timestamp, kind, day.isoformat()),
            )
        else:
            self.db.execute(
                "UPDATE health_reports SET harness=? WHERE kind=? AND date=?",
                (harness, kind, day.isoformat()),
            )

    def has_night(self, day):
        night = read_nights(self.db, day, day).get(day)
        return bool(night and night["quality"] == "ok")

    def cards(self, today):
        cards = []
        for kind, window in (("morning", 1), ("weekly", 6)):
            rows = self.db.rows(
                "SELECT * FROM health_reports WHERE kind=? AND status='ready' AND date>=? "
                "ORDER BY date DESC LIMIT 1",
                (kind, (today - timedelta(days=window)).isoformat()),
            )
            for row in rows:
                day = date.fromisoformat(row["date"])
                cards.append(
                    {
                        "type": "text",
                        "title": f"{KINDS[kind]} · {day.month}月{day.day}日",
                        "data": {
                            "text": row["body"],
                            "headline": row["title"],
                            "kind": kind,
                            "date": row["date"],
                            "report_id": row["id"],
                        },
                    }
                )
        return cards
