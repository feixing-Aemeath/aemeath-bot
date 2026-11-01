"""Import an Apple Health export locally and print recent daily metrics and alerts.

    uv run python -m scripts.health_import ~/Downloads/export.zip
    uv run python -m scripts.health_import path/to/apple_health_export/export.xml --dry-run

Reads export.xml (or 导出.xml) directly or from inside export.zip, streaming. Writes into the
backend data directory (ASSISTANT_DATA_DIR, default .state) unless --dry-run is given. Prints
only daily aggregates; nothing is sent anywhere.
"""

import argparse
import json
import os
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import jsonschema
import yaml

from core.db import Database
from plugins.health.config import HealthConfig
from plugins.health.engine import HealthEngine
from plugins.health.metrics import HRV_MS, HRV_NAMES
from plugins.health.storage import read_days

STATUS = {"complete": "完整", "partial": "部分", "missing": "缺失"}
ARROWS = {"above": "↑", "far_above": "↑↑", "below": "↓", "far_below": "↓↓"}
MANIFEST = Path(__file__).resolve().parents[1] / "plugins" / "health" / "manifest.yaml"


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text, size):
    return text + " " * max(0, size - width(text))


def load_config(db, path, max_hr):
    if path:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        rows = db.rows("SELECT config_json FROM plugins WHERE id='health'")
        config = json.loads(rows[0]["config_json"]) if rows else {}
    if max_hr:
        config.setdefault("training", {})["max_hr"] = max_hr
    schema = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["config_schema"]
    jsonschema.validate(config, schema)
    return HealthConfig.from_dict(config)


def counted(rows, every=200_000):
    for index, row in enumerate(rows, 1):
        if index % every == 0:
            print(f"已读取 {index:,} 条观测…", file=sys.stderr, flush=True)
        yield row


def cell(view, text=None):
    if view["value"] is None:
        return "—"
    text = text if text is not None else str(view["value"])
    level = view.get("vs_30d", {}).get("level")
    if level in ARROWS:
        return text + ARROWS[level]
    if level == "baseline_insufficient":
        return text + "*"
    return text


def hours(minutes):
    return f"{int(minutes // 60)}:{int(round(minutes % 60)):02d}"


def report(engine, end, days):
    daily = engine.daily(end, days, today=end)
    sleep = engine.sleep(end, days, today=end)
    nights = {night["date"]: night for night in sleep["nights"]}
    columns = [
        ("日期", 11),
        ("数据", 5),
        ("睡眠", 9),
        ("主睡眠", 7),
        ("深睡", 5),
        ("核心", 5),
        ("REM", 5),
        ("清醒", 5),
        ("入睡", 8),
        ("醒来", 8),
        ("静息心率", 10),
        ("HRV ms", 10),
        ("准备度", 7),
        ("步数", 9),
        ("TRIMP", 8),
        ("预警", 0),
    ]
    lines = [f"最近 {days} 天（截至 {end}，时区 {engine.config.timezone}）"]
    lines.append("".join(pad(name, size) for name, size in columns).rstrip())
    alerts, missing = [], []
    for day in daily["days"]:
        metrics, night = day["metrics"], nights.get(day["date"], {})
        if day["data_status"] == "missing":
            missing.append(day["date"])
        stages = night.get("stages_minutes") or {}
        sleep_view = metrics["sleep_minutes"]
        preferred = day["hrv_preferred"]
        hrv = metrics[preferred] if preferred else metrics["hrv_ln_sdnn"]
        # R = RMSSD, S = SDNN: each is compared with its own baseline only.
        hrv_text = None
        if preferred and hrv["value"] is not None:
            hrv_text = f"{hrv[HRV_MS[preferred]]:.0f} {HRV_NAMES[preferred][0]}"
        score = day["readiness"]["score"]
        values = [
            day["date"],
            STATUS.get(day["data_status"], day["data_status"]),
            cell(sleep_view, hours(sleep_view["value"]) if sleep_view["value"] else None),
            hours(night["asleep_minutes"]) if "asleep_minutes" in night else "—",
            *(str(stages[k]) if k in stages else "—" for k in ("deep", "core", "rem", "awake")),
            cell(metrics["sleep_onset"]),
            cell(metrics["wake_time"]),
            cell(metrics["resting_hr"]),
            cell(hrv, hrv_text),
            "—" if score is None else f"{score:.1f}",
            cell(metrics["steps"], f"{metrics['steps']['value']:,.0f}")
            if metrics["steps"]["value"] is not None
            else "—",
            "—" if day["workouts"]["trimp"] is None else f"{day['workouts']['trimp']:.1f}",
            "、".join(alert["rule"] for alert in day["alerts"]) or "-",
        ]
        lines.append(
            "".join(pad(v, size) for v, (_, size) in zip(values, columns, strict=True)).rstrip()
        )
        alerts.extend((day["date"], alert["message"]) for alert in day["alerts"])
    lines.append(
        "（睡眠：整个睡眠日，含小睡；主睡眠、各阶段、入睡和醒来只算最长的一段；"
        "↑/↓：相对本人近 30 天基线明显偏高/偏低；↑↑/↓↓：非常明显；*：基线天数不足；—：无数据；"
        "HRV 后的 R/S：RMSSD/SDNN；准备度 0–10，见 docs/health-methods.md 第 7 节）"
    )
    lines.append("")
    lines.append("预警（只表示建议关注，不是诊断）：" if alerts else "预警：无")
    lines.extend(f"  {day} {message}" for day, message in alerts)
    regularity = sleep["regularity"]
    parts = []
    for window in (7, 30):
        sri = regularity[f"sri_{window}d"]
        value = "—" if sri["value"] is None else f"{sri['value']:.1f}"
        note = "" if sri["reliable"] else "，天数不足"
        parts.append(f"近 {window} 天 SRI {value}（有效相邻日 {sri['valid_day_pairs']} 对{note}）")
        onset, wake = (
            regularity[f"onset_variability_{window}d"],
            regularity[f"wake_variability_{window}d"],
        )
        parts.append(
            f"入睡/醒来时间波动 SD {onset['sd_minutes'] if onset['sd_minutes'] is not None else '—'}"
            f"/{wake['sd_minutes'] if wake['sd_minutes'] is not None else '—'} 分钟"
        )
    lines.append("睡眠规律：" + "；".join(parts))
    load = daily["training_load"]
    ratio = "—" if load["acwr_reference"] is None else f"{load['acwr_reference']:.2f}"
    hr_max = load["hr_max"]
    source = {"config": "配置", "observed": "历史观测最高值", "unavailable": "无"}[hr_max["source"]]
    lines.append(
        f"训练负荷：近 7 天 TRIMP {load['acute_7d_trimp']:.1f}，近 28 天 {load['chronic_28d_trimp']:.1f}，"
        f"ACWR {ratio}（仅供参考，学界对其有争议）；最大心率 {hr_max['value'] or '—'}（{source}）"
    )
    if missing:
        lines.append("数据缺失的日子（不参与基线）：" + "、".join(missing))
    return "\n".join(lines), {
        "daily": daily,
        "sleep": sleep,
        "baselines": engine.baselines(end, today=end),
    }


def latest_day(db):
    days = [d for d, row in read_days(db).items() if row["status"] != "missing"]
    return max(days) if days else None


def run(args, data_dir):
    db = Database(data_dir / "assistant.sqlite3")
    engine = HealthEngine(db, data_dir, load_config(db, args.config, args.max_hr))
    try:
        imported = engine.import_export(args.path, progress=counted)
    except ET.ParseError as error:
        print(
            f"export.xml 不是完整的 XML（第 {error.position[0]} 行）：可能是导出或解压不完整。"
            "已写入的部分不影响结果，重新导出后再导入即可（重复导入不会重复计数）。",
            file=sys.stderr,
        )
        return 2
    refreshed = engine.refresh(full=True)
    counts = imported["counts"]
    print(
        f"导入完成：新增 {imported['inserted']:,} 条观测（读取记录 {counts.get('records', 0):,} 条，"
        f"体能训练 {counts.get('workouts', 0):,} 次），导出时间 {imported['export_date'] or '未知'}；"
        f"重算 {refreshed['days']} 天。数据目录："
        + ("临时目录（--dry-run，结束后删除）" if args.dry_run else str(data_dir)),
        file=sys.stderr,
    )
    end = args.end or latest_day(db)
    if end is None:
        print("没有可用的健康数据。", file=sys.stderr)
        return 1
    text, payload = report(engine, end, args.days)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else text)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="导入 Apple 健康导出文件，打印最近若干天的指标和预警（只在本机处理）。"
    )
    parser.add_argument("path", type=Path, help="export.xml、导出.xml 或 export.zip")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="默认 ASSISTANT_DATA_DIR 或 .state"
    )
    parser.add_argument("--dry-run", action="store_true", help="只在临时目录中计算，不写数据目录")
    parser.add_argument("--days", type=int, default=14, choices=range(1, 32), metavar="1-31")
    parser.add_argument("--end", type=date.fromisoformat, help="最后一天，默认最近有数据的一天")
    parser.add_argument("--json", action="store_true", help="输出给模型的摘要 JSON")
    parser.add_argument("--config", type=Path, help="health 插件配置 JSON 文件")
    parser.add_argument("--max-hr", type=float, help="实测最大心率，覆盖配置")
    args = parser.parse_args(argv)
    if not args.path.is_file():
        parser.error(f"找不到文件：{args.path}")
    if args.dry_run:
        with tempfile.TemporaryDirectory() as directory:
            return run(args, Path(directory))
    data_dir = args.data_dir or Path(os.environ.get("ASSISTANT_DATA_DIR", ".state"))
    return run(args, data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
