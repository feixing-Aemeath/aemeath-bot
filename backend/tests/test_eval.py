import copy
import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from evals.dataset import clusters, load, validate
from evals.mock_agent import change_target
from evals.run_extract import run
from evals.score import judge_due, render, score

SAMPLES, META = load()
V3 = SAMPLES[:190]
LEGACY = V3[:168]
ZONE = ZoneInfo("Asia/Shanghai")


def perfect(samples=SAMPLES):
    """One todo per labelled duplicate group, citing all of its samples."""
    todos, seen = [], set()
    for sample in samples:
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
                "status": label.get("status", "pending"),
                "notes": "；".join(label.get("notes_contains", [])),
            }
        )
    return {"harness": "test", "todos": todos, "unprocessed": 0}


def test_dataset_shape():
    assert len(SAMPLES) == 246
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


@pytest.mark.parametrize(
    ("samples", "groups", "tasks"), [(LEGACY, 17, 33), (V3, 23, 40), (SAMPLES, 31, 65)]
)
def test_perfect_predictions_score_one(samples, groups, tasks):
    report = score(samples, META, perfect(samples))
    assert report["detection"]["f1"] == 1.0
    assert report["due"]["accuracy_exact"] == 1.0
    assert report["dedup"]["pairwise_f1"] == 1.0
    assert report["dedup"]["groups_exact"] == report["dedup"]["groups"] == groups
    assert report["dedup"]["gold_todos"] == report["dedup"]["predicted_todos"] == tasks
    assert report["title_similarity"] == 1.0
    assert report["final_state"]["accuracy"] == 1.0


def test_errors_are_counted_where_they_belong():
    predictions = perfect(LEGACY)
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
    report = score(LEGACY, META, predictions)
    detection = report["detection"]
    assert (detection["tp"], detection["fp"], detection["fn"]) == (53, 1, 2)
    assert report["due"]["date"] == 2  # a18 and a42 share the wrong-time todo
    assert report["dedup"]["groups_exact"] == 15  # e04/e10 missed, lab split
    assert report["dedup"]["pairwise_precision"] == 1.0
    assert report["dedup"]["pairwise_recall"] < 1.0
    text = render(report)
    assert "d02" in text and "e04" in text and "a34, b12, b13" in text


def test_legacy_labels_and_predictions_need_no_status():
    samples = copy.deepcopy(LEGACY)
    for sample in samples:
        sample["label"].pop("status", None)
    predictions = perfect(samples)
    for todo in predictions["todos"]:
        del todo["status"]
    assert score(samples, META, predictions)["final_state"]["accuracy"] == 1.0


def test_final_state_catches_old_deadlines_missing_cancellations_and_extra_rows():
    predictions = perfect()
    todos = predictions["todos"]
    by_ref = {ref: todo for todo in todos for ref in todo["source_refs"]}
    by_ref["nc01"]["due_at"] = "2026-10-16T23:59:00+08:00"  # original deadline
    by_ref["nc08"]["status"] = "pending"  # cancellation missed
    by_ref["nc10"]["status"] = "deleted"  # deleting loses the cancellation record
    todos.append({**by_ref["nc04"], "source_refs": ["nc05"]})  # full merge plus extra copy
    by_ref["nc06"]["notes"] = "仍在 A101"
    todos.remove(by_ref["nc12"])
    report = score(SAMPLES, META, predictions)
    final = report["final_state"]
    assert final["exact"] == 59 and final["evaluated"] == 65
    assert final["due_wrong"] == 1 and final["status_wrong"] == 2
    assert final["duplicates"] == final["notes_wrong"] == final["missing"] == 1
    assert {tuple(e["ids"]) for e in report["errors"]["final_state"]} >= {("nc04", "nc05")}
    assert "最终待办不对" in render(report)


def test_final_state_rejects_merged_tasks_and_lost_provenance():
    predictions = perfect()
    by_ref = {ref: todo for todo in predictions["todos"] for ref in todo["source_refs"]}
    by_ref["nc01"]["source_refs"].remove("nc02")
    by_ref["nc04"]["source_refs"].append("nc06")
    final = score(SAMPLES, META, predictions)["final_state"]
    assert final["incomplete_refs"] == 1 and final["overmerged"] == 1
    assert final["duplicates"] == 1  # nc06 now occurs in two tasks


@pytest.mark.parametrize("refs", [["nc20"], ["unknown"], []])
def test_final_state_penalizes_unrelated_extra_tasks(refs):
    predictions = perfect()
    predictions["todos"].append({"title": "无依据的任务", "source_refs": refs})
    report = score(SAMPLES, META, predictions)
    final = report["final_state"]
    assert final["exact"] == 65 and final["evaluated"] == 66
    assert final["unexpected"] == 1 and final["accuracy"] < 1
    assert any(e["problems"] == ["unexpected"] for e in report["errors"]["final_state"])


def test_final_state_ignores_a_deleted_duplicate_when_the_final_task_is_retained():
    predictions = perfect()
    original = next(t for t in predictions["todos"] if "nc04" in t["source_refs"])
    predictions["todos"].append({**original, "source_refs": ["nc05"], "status": "deleted"})
    final = score(SAMPLES, META, predictions)["final_state"]
    assert final["accuracy"] == 1 and final["duplicates"] == 0


@pytest.mark.parametrize("field,value", [("status", "archived"), ("notes_contains", "B204")])
def test_validation_rejects_bad_final_state_labels(field, value):
    samples = copy.deepcopy(SAMPLES)
    samples[0]["label"][field] = value
    with pytest.raises(ValueError):
        validate(samples, META)


def test_validation_rejects_disagreeing_final_status():
    samples = copy.deepcopy(SAMPLES)
    next(s for s in samples if s["id"] == "nc08")["label"]["status"] = "pending"
    with pytest.raises(ValueError, match="disagree on status"):
        validate(samples, META)


def test_mock_change_target_requires_a_unique_match():
    todo = {"title": "提交系统实践报告", "source_excerpt": "提交系统实践报告"}
    assert change_target("系统实践报告延期", [todo]) is todo
    assert change_target("系统实践报告延期", [todo, todo.copy()]) is None
    assert change_target("撤回了一条消息", [todo]) is None


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
async def test_mock_run_goes_through_harness_and_mcp(tmp_path, harness, monkeypatch):
    unrelated = tmp_path / "unrelated-vault"
    monkeypatch.setenv("ASSISTANT_VAULT_DIR", str(unrelated))
    monkeypatch.setenv("ASSISTANT_HOST", "0.0.0.0")
    report = await run(harness, tmp_path, rounds=1, mock=True, timeout=120)
    assert report["mock"] is True and report["harness"] == harness
    assert report["attempts"] == [{"success": True, "error": None}]
    assert report["unprocessed"] == 0  # the fake CLI marked every message through MCP
    assert report["detection"]["tp"] > 0 and report["unknown_refs"] == 0
    predictions = json.loads((tmp_path / "predictions.json").read_text(encoding="utf-8"))
    assert predictions["digests"]  # notices went to the digest
    assert any(
        t["status"] == "cancelled" and len(t["source_refs"]) > 1 for t in predictions["todos"]
    )
    assert all(
        ref in {s["id"] for s in SAMPLES} for t in predictions["todos"] for ref in t["source_refs"]
    )
    assert "MOCK" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert not unrelated.exists()
