"""Compare the readiness score with the logged Apple Fitness readiness and own rating, and
suggest readiness weights. Only prints; it never changes the plugin configuration.

    uv run python -m scripts.readiness_calibration                 # last 60 days
    uv run python -m scripts.readiness_calibration --target perceived --days 42
    uv run python -m scripts.readiness_calibration --json

Reads the backend's database in ASSISTANT_DATA_DIR (environment or backend/.env, default
.state) and the health plugin's saved configuration. Uses the data as of the last health
refresh (every 15 minutes). How it works: docs/readiness-calibration.md.
"""

import argparse
import json
import sys
from datetime import date

from plugins.health.calibration import MIN_DAYS, TARGETS, calibrate
from plugins.health.engine import HealthEngine
from plugins.health.readiness import LABELS
from scripts.health_import import load_config
from scripts.tokens import database

TARGET_NAMES = {
    "both": "健身 App 准备指数和体感的平均",
    "apple": "健身 App 准备指数",
    "perceived": "体感（1–5 换算到 0–10）",
}


def number(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def weights_line(weights):
    return " · ".join(f"{LABELS[name]} {weight:.2f}" for name, weight in weights.items())


def report(result):
    lines = ["综合准备度校准（只给建议，不会修改任何配置）", ""]
    counts = result["counts"]
    span = f"{result['range'][0]} 到 {result['range'][1]}" if result["range"] else "—"
    lines.append(
        f"对比对象：{TARGET_NAMES[result['target']]}。可用的日子 {result['days']} 天（{span}；"
        f"记了健身 App {counts['apple_fitness_readiness']} 天，记了体感 {counts['perceived_readiness']} 天）"
    )
    if result["status"] == "insufficient":
        lines.append(
            f"还不够：至少要 {result['min_days']} 天既有算出的准备度、又有记录，"
            f"再记 {result['min_days'] - result['days']} 天后重新运行。"
        )
        return "\n".join(lines)
    if result["status"] == "no_candidates":
        lines.append("这些日子里能算分的项目太少，没法比较权重。")
        return "\n".join(lines)
    correlation = result["correlation"]
    lines += [
        "",
        "一致程度（秩相关，-1 到 1，越接近 1 越一致）：",
        f"  算出的准备度 和 对比对象：{number(correlation['score_vs_target'])}",
        f"  和 健身 App：{number(correlation['score_vs_apple'])}　和 体感：{number(correlation['score_vs_perceived'])}",
        "  各项和对比对象："
        + " · ".join(
            f"{LABELS[name]} {number(value)}"
            for name, value in correlation["parts_vs_target"].items()
        ),
        f"  平均差（算出的 − 对比对象）：{result['bias']:+.2f} 分。权重只改变各项的比例，改不了这个整体偏差。",
        "",
        f"当前权重：{weights_line(result['current_weights'])}　平均误差 {result['current_mae']:.2f}",
        f"拟合权重：{weights_line(result['fitted_weights'])}　平均误差 {result['fitted_mae']:.2f}"
        f"（留一法 {result['loo_mae']:.2f}）",
        "",
    ]
    if result["status"] == "suggest_change":
        weights = json.dumps(
            {"readiness": {"weights": result["fitted_weights"]}}, ensure_ascii=False
        )
        lines += [
            f"建议：改用拟合权重。留一法误差比当前低 {result['improvement']:.0%}（门槛 {result['min_improvement']:.0%}）。",
            "要采用，把下面这段并进 health 插件的配置（PATCH /api/v1/plugins/health 的 config 是整体替换的，"
            "记得保留已有的其他设置；脚本不会自己改）：",
            f"  {weights}",
        ]
    else:
        lines.append(
            f"建议：保持当前权重。拟合权重的留一法误差只比当前低 {result['improvement']:.0%}"
            f"（门槛 {result['min_improvement']:.0%}），差别可能只是这几周的偶然波动。"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m scripts.readiness_calibration",
        description="对比算出的综合准备度和手动记录，给出权重建议（不修改配置）。",
    )
    parser.add_argument("--data-dir", help="数据目录，默认 ASSISTANT_DATA_DIR")
    parser.add_argument("--days", type=int, default=60, choices=range(7, 367), metavar="7-366")
    parser.add_argument("--end", type=date.fromisoformat, help="最后一天，默认今天（今天不计入）")
    parser.add_argument("--target", choices=TARGETS, default="both")
    parser.add_argument("--min-days", type=int, default=MIN_DAYS)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    try:
        db = database(args.data_dir)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    config = load_config(db, None, None)
    engine = HealthEngine(db, db.path.parent, config)
    today = engine.today()
    daily = engine.daily(args.end or today, args.days, today=today)
    result = calibrate(daily, config.readiness, args.target, args.min_days)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
