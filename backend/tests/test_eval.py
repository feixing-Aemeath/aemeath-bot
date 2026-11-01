import copy
import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from evals.dataset import clusters, load, validate
from evals.run_extract import run
from evals.score import judge_due, render, score

SAMPLES, META = load()
ZONE = ZoneInfo("Asia/Shanghai")


def perfect():
    """One todo per labelled duplicate group, citing all of its samples."""
    todos, seen = [], set()
    for sample in SAMPLES:
        label = sample["label"]
        if not label["is_todo"] or sample["id"] in seen:
            continue
        group = sorted({sample["id"], *label["duplicates"]})
        seen.update(group)
        due = label["due_at"]
        todos.append(
            {
                "title": label["title"],
                "due_at": None
                if due is None
                else (due if len(due) > 10 else due + "T00:00:00+08:00"),
                "has_time": due is not None and len(due) > 10,
                "source_refs": group,
            }
        )
    return {"harness": "test", "todos": todos, "unprocessed": 0}


def test_dataset_shape():
    assert 150 <= len(SAMPLES) <= 200
    plugins = Counter(sample["plugin"] for sample in SAMPLES)
    assert set(plugins) == {"qq_screen", "feishu"}
    kinds = Counter(sample["label"]["kind"] for sample in SAMPLES)
    assert kinds["chat"] >= 30 and kinds["notice"] >= 20  # chit-chat and pure notices
    texts = [sample["text"] for sample in SAMPLES]
    assert any("@全体成员" in t for t in texts) and any("@所有人" in t for t in texts)
    assert any(t.startswith("【群公告】") for t in texts)
    gold = clusters(SAMPLES)
    groups = {group for group in gold.values() if len(group) > 1}
    assert len(groups) >= 10  # forwarded / repeated notices
    plugin_of = {sample["id"]: sample["plugin"] for sample in SAMPLES}
    assert any(len({plugin_of[i] for i in group}) == 2 for group in groups)  # QQ + Feishu
    assert sum(1 for s in SAMPLES if s["label"]["is_todo"] and s["label"]["due_at"] is None) >= 2
    # One-to-one chats are named after the other person and include the user's own lines.
    assert {"赵磊", "何静", "黄老师"} <= {sample["chat_name"] for sample in SAMPLES}
    assert any(sample["sender"] == "我" for sample in SAMPLES)


def test_validation_rejects_inconsistent_labels():
    broken = copy.deepcopy(SAMPLES)
    first = next(s for s in broken if s["label"]["duplicates"])
    first["label"]["duplicates"].append("zzz")
    with pytest.raises(ValueError, match="unknown duplicate"):
        validate(broken, META)
    broken = copy.deepcopy(SAMPLES)
    first = next(s for s in broken if s["label"]["is_todo"] and s["label"]["duplicates"])
    first["label"]["due_at"] = "2026-12-01"
    with pytest.raises(ValueError, match="disagree"):
        validate(broken, META)
    broken = copy.deepcopy(SAMPLES)
    next(s for s in broken if not s["label"]["is_todo"])["label"]["title"] = "x"
    with pytest.raises(ValueError, match="non-todo"):
        validate(broken, META)


def test_perfect_predictions_score_one():
    report = score(SAMPLES, META, perfect())
    assert report["detection"]["f1"] == 1.0
    assert report["due"]["accuracy_exact"] == 1.0
    assert report["dedup"]["pairwise_f1"] == 1.0
    assert report["dedup"]["groups_exact"] == report["dedup"]["groups"] == 17
    assert report["dedup"]["gold_todos"] == report["dedup"]["predicted_todos"] == 33
    assert report["title_similarity"] == 1.0


def test_errors_are_counted_where_they_belong():
    predictions = perfect()
    todos = predictions["todos"]
    by_ref = {ref: todo for todo in todos for ref in todo["source_refs"]}
    # Missed: the 篮球社 fee reminder is never extracted (two samples, one group).
    todos.remove(by_ref["e04"])
    # Split: the forwarded OJ deadline becomes two todos instead of one.
    lab = by_ref["b12"]
    lab["source_refs"] = ["a34", "b12"]
    todos.append({**lab, "source_refs": ["b13"]})
    # Due: the class meeting keeps the old 20:00 instead of the moved 19:30.
    by_ref["a18"]["due_at"] = "2026-10-19T20:00:00+08:00"
    # False positive: an optional singing contest sign-up.
    todos.append({"title": "报名十佳歌手", "due_at": None, "source_refs": ["d02"]})
    report = score(SAMPLES, META, predictions)
    detection = report["detection"]
    assert (detection["tp"], detection["fp"], detection["fn"]) == (53, 1, 2)
    assert report["due"]["date"] == 2  # a18 and a42 share the wrong-time todo
    assert report["dedup"]["groups_exact"] == 15  # e04/e10 missed, lab split
    assert report["dedup"]["pairwise_precision"] == 1.0
    assert report["dedup"]["pairwise_recall"] < 1.0
    text = render(report)
    assert "d02" in text and "e04" in text and "a34, b12, b13" in text


@pytest.mark.parametrize(
    ("gold", "todo", "verdict"),
    [
        (datetime(2026, 10, 18, 23, 59, tzinfo=ZONE), {"due_at": "2026-10-19T00:00:00+08:00", "has_time": True}, "exact"),
        (datetime(2026, 10, 18, 23, 59, tzinfo=ZONE), {"due_at": "2026-10-18T15:59:00+00:00", "has_time": True}, "exact"),
        (datetime(2026, 10, 18, 17, 0, tzinfo=ZONE), {"due_at": "2026-10-18T00:00:00+08:00", "has_time": False}, "date"),
        (datetime(2026, 10, 18, 17, 0, tzinfo=ZONE), {"due_at": "2026-10-17T17:00:00+08:00", "has_time": True}, "wrong"),
        (datetime(2026, 10, 18, 17, 0, tzinfo=ZONE).date(), {"due_at": "2026-10-17T16:00:00+00:00", "has_time": False}, "exact"),
        (datetime(2026, 10, 18).date(), {"due_at": "2026-10-18T08:00:00+08:00", "has_time": True}, "date"),
        (datetime(2026, 10, 18).date(), {"due_at": None}, "missing"),
        (None, {"due_at": "2026-10-18T08:00:00+08:00", "has_time": True}, "invented"),
        (None, {"due_at": None}, "exact"),
    ],
)  # fmt: skip
def test_due_verdicts(gold, todo, verdict):
    assert judge_due(gold, todo, ZONE) == verdict


@pytest.mark.parametrize("harness", ["claude_code", "codex"])
async def test_mock_run_goes_through_harness_and_mcp(tmp_path, harness):
    report = await run(harness, tmp_path, rounds=1, mock=True, timeout=120)
    assert report["mock"] is True and report["harness"] == harness
    assert report["attempts"] == [{"success": True, "error": None}]
    assert report["unprocessed"] == 0  # the fake CLI marked every message through MCP
    assert report["detection"]["tp"] > 0 and report["unknown_refs"] == 0
    predictions = json.loads((tmp_path / "predictions.json").read_text(encoding="utf-8"))
    assert predictions["digests"]  # notices went to the digest
    assert all(
        ref in {s["id"] for s in SAMPLES} for t in predictions["todos"] for ref in t["source_refs"]
    )
    assert "MOCK" in (tmp_path / "report.md").read_text(encoding="utf-8")
