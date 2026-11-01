"""Score todo extraction against the labelled dataset.

predictions.json (written by evals.run_extract, or by hand):

    {"todos": [{"title": "…", "due_at": "ISO 8601" | null, "has_time": bool,
                "source_refs": ["a01", "c01"], "status": "pending"}], ...}

source_refs are dataset sample IDs. Metrics:

- detection: a sample is predicted positive when at least one todo cites it. Precision,
  recall and F1 over samples.
- due: on correctly detected samples, the due time of the todo citing it. exact = same
  minute (±1 min, so 24:00 and 23:59 agree) for a labelled time, same date with has_time
  false for a labelled date, or both empty; date = the local date is right.
- dedup: on correctly detected samples, every pair is "same todo" or not. Pairwise precision,
  recall, F1; plus the share of labelled duplicate groups produced as exactly one todo.
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

from evals.dataset import DEFAULT, clusters, load, parse_due


def ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else 0.0


def prf(true_positive, false_positive, false_negative):
    precision = ratio(true_positive, true_positive + false_positive)
    recall = ratio(true_positive, true_positive + false_negative)
    f1 = ratio(2 * precision * recall, precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
    }


def judge_due(gold, todo, zone):
    """exact / date / missing / invented / wrong for one detected sample."""
    raw = todo.get("due_at")
    predicted = datetime.fromisoformat(raw).astimezone(zone) if raw else None
    has_time = bool(todo.get("has_time")) and predicted is not None
    if gold is None:
        return "exact" if predicted is None else "invented"
    if predicted is None:
        return "missing"
    if isinstance(gold, datetime):
        if has_time and abs((predicted - gold).total_seconds()) <= 60:
            return "exact"
        return "date" if predicted.date() == gold.astimezone(zone).date() else "wrong"
    if predicted.date() != gold:
        return "wrong"
    return "exact" if not has_time else "date"  # a time the message never gave


def bigrams(text):
    text = "".join(text.split())
    return {text[i : i + 2] for i in range(len(text) - 1)} or {text}


def title_similarity(left, right):
    a, b = bigrams(left), bigrams(right)
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def score(samples, meta, predictions):
    zone = ZoneInfo(meta["timezone"])
    by_id = {sample["id"]: sample for sample in samples}
    gold = clusters(samples)
    todos = [t for t in predictions.get("todos", []) if t.get("status", "pending") != "deleted"]
    cited, unknown = defaultdict(list), 0
    for index, todo in enumerate(todos):
        for ref in dict.fromkeys(todo.get("source_refs", [])):
            if ref in by_id:
                cited[ref].append(index)
            else:
                unknown += 1
    positive, predicted = set(gold), set(cited)
    tp, fp, fn = positive & predicted, predicted - positive, positive - predicted

    verdicts, errors, similarity = {}, {"false_positive": [], "false_negative": [], "due": []}, []
    for sample_id in sorted(tp):
        label = by_id[sample_id]["label"]
        todo = todos[cited[sample_id][0]]
        verdict = judge_due(parse_due(label["due_at"]), todo, zone)
        verdicts[sample_id] = verdict
        similarity.append(title_similarity(label["title"], todo.get("title", "")))
        if verdict != "exact":
            errors["due"].append(
                {
                    "id": sample_id,
                    "verdict": verdict,
                    "expected": label["due_at"],
                    "predicted": todo.get("due_at"),
                    "has_time": todo.get("has_time"),
                }
            )
    for sample_id in sorted(fp):
        errors["false_positive"].append(
            {
                "id": sample_id,
                "text": by_id[sample_id]["text"][:60],
                "kind": by_id[sample_id]["label"]["kind"],
                "predicted_title": todos[cited[sample_id][0]].get("title"),
            }
        )
    for sample_id in sorted(fn):
        label = by_id[sample_id]["label"]
        errors["false_negative"].append(
            {"id": sample_id, "title": label["title"], "text": by_id[sample_id]["text"][:60]}
        )

    counts = defaultdict(int)
    for verdict in verdicts.values():
        counts[verdict] += 1
    due = {
        "evaluated": len(verdicts),
        "accuracy_exact": ratio(counts["exact"], len(verdicts)),
        "accuracy_date": ratio(counts["exact"] + counts["date"], len(verdicts)),
        **{name: counts[name] for name in ("exact", "date", "wrong", "missing", "invented")},
    }

    pair_tp = pair_fp = pair_fn = 0
    for left, right in combinations(sorted(tp), 2):
        same_gold = gold[left] == gold[right]
        same_predicted = bool(set(cited[left]) & set(cited[right]))
        pair_tp += same_gold and same_predicted
        pair_fp += same_predicted and not same_gold
        pair_fn += same_gold and not same_predicted
    groups = {group for group in gold.values() if len(group) > 1}
    exact_groups, split_groups = 0, []
    for group in sorted(groups, key=sorted):
        shared = set.intersection(*(set(cited.get(member, [])) for member in group))
        clean = [
            index
            for index in shared
            if all(
                ref in group or ref not in positive for ref in todos[index].get("source_refs", [])
            )
        ]
        if clean:
            exact_groups += 1
        else:
            split_groups.append(sorted(group))
    pairs = prf(pair_tp, pair_fp, pair_fn)
    dedup = {
        "pairwise_precision": pairs["precision"],
        "pairwise_recall": pairs["recall"],
        "pairwise_f1": pairs["f1"],
        "groups": len(groups),
        "groups_exact": exact_groups,
        "groups_exact_rate": ratio(exact_groups, len(groups)),
        "gold_todos": len(set(gold.values())),
        "predicted_todos": sum(
            1 for index in range(len(todos)) if any(index in v for v in cited.values())
        ),
    }
    errors["dedup"] = split_groups
    return {
        "dataset": {"name": meta["name"], "version": meta["version"], "samples": len(samples)},
        "harness": predictions.get("harness"),
        "mock": bool(predictions.get("mock")),
        "detection": prf(len(tp), len(fp), len(fn)),
        "due": due,
        "dedup": dedup,
        "title_similarity": round(sum(similarity) / len(similarity), 4) if similarity else 0.0,
        "unknown_refs": unknown,
        "unprocessed": predictions.get("unprocessed"),
        "errors": errors,
    }


def render(report, limit=15):
    detection, due, dedup = report["detection"], report["due"], report["dedup"]
    lines = [
        f"# 待办提炼评测：{report['harness'] or '未知'}"
        + ("（MOCK，不代表模型效果）" if report["mock"] else ""),
        "",
        f"数据集 {report['dataset']['name']} v{report['dataset']['version']}，"
        f"{report['dataset']['samples']} 条消息；剩余未处理 {report['unprocessed']}。",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 待办识别 精确率 / 召回率 / F1 | {detection['precision']:.3f} / {detection['recall']:.3f} / {detection['f1']:.3f}（TP {detection['tp']}，FP {detection['fp']}，FN {detection['fn']}） |",
        f"| 截止时间 完全正确 / 日期正确 | {due['accuracy_exact']:.3f} / {due['accuracy_date']:.3f}（{due['evaluated']} 条；缺失 {due['missing']}，编造 {due['invented']}，错误 {due['wrong']}） |",
        f"| 去重 成对精确率 / 召回率 / F1 | {dedup['pairwise_precision']:.3f} / {dedup['pairwise_recall']:.3f} / {dedup['pairwise_f1']:.3f} |",
        f"| 重复组完全合并为一条 | {dedup['groups_exact']} / {dedup['groups']} |",
        f"| 待办条数（标注 / 生成） | {dedup['gold_todos']} / {dedup['predicted_todos']} |",
        f"| 标题相似度（字符二元组，仅供参考） | {report['title_similarity']:.3f} |",
    ]
    errors = report["errors"]
    sections = [
        (
            "误报（不是待办却建了待办）",
            errors["false_positive"],
            "{id} [{kind}] {text} → {predicted_title}",
        ),
        ("漏报", errors["false_negative"], "{id} {title}：{text}"),
        ("截止时间不对", errors["due"], "{id} {verdict}：应为 {expected}，实际 {predicted}"),
    ]
    for title, items, pattern in sections:
        if items:
            lines += ["", f"## {title}（{len(items)}）", ""]
            lines += [f"- {pattern.format(**item)}" for item in items[:limit]]
    if errors["dedup"]:
        lines += ["", f"## 没有合并成一条的重复组（{len(errors['dedup'])}）", ""]
        lines += [f"- {', '.join(group)}" for group in errors["dedup"][:limit]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="给待办提炼结果打分")
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--dataset", type=Path, default=DEFAULT)
    parser.add_argument("--json", type=Path, help="把完整报告写成 JSON")
    args = parser.parse_args(argv)
    samples, meta = load(args.dataset)
    report = score(samples, meta, json.loads(args.predictions.read_text(encoding="utf-8")))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render(report))
    return report


if __name__ == "__main__":
    main()
