import copy
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from core.models import TodoCreate, TodoPatch
from evals import run_extract as runner
from evals.dataset import load, validate
from evals.score import render, score

SAMPLES, META = load()


def sample(identifier, sent_at, text="提交系统实践实验报告", **changes):
    return {
        "id": identifier,
        "plugin": "qq_screen",
        "chat_name": "系统实践课程群",
        "sender": "程老师",
        "sent_at": sent_at,
        "text": text,
        "label": {
            "is_todo": True,
            "title": "提交系统实践实验报告",
            "due_at": "2026-10-20T17:00:00+08:00",
            "duplicates": [],
            "kind": "deadline",
        },
        **changes,
    }


def write_dataset(directory, samples):
    directory.mkdir()
    (directory / "meta.json").write_text(json.dumps(META), encoding="utf-8")
    (directory / "dataset.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in samples), encoding="utf-8"
    )
    return directory


def test_batches_sort_instants_and_group_by_dataset_timezone():
    samples = [
        sample("late", "2026-10-13T00:30:00+08:00"),
        sample("first", "2026-10-12T08:00:00+08:00"),
        sample("second", "2026-10-12T15:30:00+00:00"),
    ]
    days = runner.message_batches(samples, "day", META["timezone"])
    assert [[s["id"] for s in day] for day in days] == [["first", "second"], ["late"]]
    sizes = runner.message_batches(samples, 2, META["timezone"])
    assert [[s["id"] for s in batch] for batch in sizes] == [["first", "second"], ["late"]]
    assert runner.message_batches(samples, None, META["timezone"]) == [samples]
    assert runner.message_batches([], "day", META["timezone"]) == []


@pytest.mark.parametrize("value", ["0", "-1", "week", "1.5"])
def test_cli_rejects_invalid_batch_sizes(value):
    with pytest.raises(SystemExit) as exc:
        runner.main(["--harness", "codex", "--mock", "--batches", value])
    assert exc.value.code == 2


@pytest.mark.parametrize("mode", [None, "day", 1, 2])
async def test_run_batches_keep_state_and_never_expose_future_messages(tmp_path, monkeypatch, mode):
    samples = [
        sample("last", "2026-10-14T08:00:00+08:00"),
        sample("first", "2026-10-12T08:00:00+08:00"),
        sample("middle", "2026-10-13T08:00:00+08:00"),
    ]
    dataset = write_dataset(tmp_path / "dataset", samples)
    actual_service = runner.Service
    snapshots = []

    def service_factory(settings, **kwargs):
        service = actual_service(settings, **kwargs)

        async def agent_run(prompt, config, timeout):
            local_now = re.search(r"当前本地时间：(\S+?)。", prompt)[1]
            rows = service.db.rows("SELECT * FROM messages")
            unprocessed = service.store.unprocessed()
            snapshots.append((local_now, {r["external_id"] for r in rows}))
            assert service.memory.read(META["profile_path"])["content"] == META["profile"]
            if mode is not None:
                assert all(
                    datetime.fromisoformat(row["sent_at"]) <= datetime.fromisoformat(local_now)
                    for row in rows
                )
            todos = service.store.todos()["items"]
            if todos:
                assert len(todos) == 1
                await service.update_todo(todos[0]["id"], TodoPatch(notes="updated in later batch"))
            else:
                row = unprocessed[0]
                await service.add_todo(
                    TodoCreate(
                        title="保留的待办",
                        source_plugin=row["plugin"],
                        source_refs=[row["id"]],
                        source_excerpt=row["text"],
                        confidence=0.9,
                    )
                )
            service.store.mark_processed([row["id"] for row in unprocessed])
            return SimpleNamespace(success=True, error=None)

        service.jobs.harnesses["codex"] = SimpleNamespace(run=agent_run)
        return service

    monkeypatch.setattr(runner, "Service", service_factory)
    out = tmp_path / "out"
    report = await runner.run("codex", out, dataset, batches=mode)
    predictions = json.loads((out / "predictions.json").read_text())
    assert report["unprocessed"] == 0
    assert len(predictions["todos"]) == 1
    if mode is None:
        assert snapshots == [(META["reference_now"], {"first", "middle", "last"})]
        assert "batches" not in predictions
    else:
        assert snapshots[0][1] == ({"first", "middle"} if mode == 2 else {"first"})
        assert snapshots[-1] == (samples[0]["sent_at"], {"first", "middle", "last"})
        assert predictions["todos"][0]["notes"] == "updated in later batch"
        assert report["batches"] == predictions["batches"]
        assert len(predictions["batches"]) == (2 if mode == 2 else 3)


@pytest.mark.parametrize("success,error", [(True, None), (False, "quota_exhausted")])
async def test_batches_stop_before_ingesting_future_when_current_batch_is_unfinished(
    tmp_path, monkeypatch, success, error
):
    dataset = write_dataset(
        tmp_path / "dataset",
        [
            sample("first", "2026-10-12T08:00:00+08:00"),
            sample("future", "2026-10-13T08:00:00+08:00"),
        ],
    )
    actual_service = runner.Service
    seen = []

    def service_factory(settings, **kwargs):
        service = actual_service(settings, **kwargs)

        async def agent_run(*args):
            seen.append(service.db.rows("SELECT external_id FROM messages"))
            return SimpleNamespace(success=success, error=error)

        service.jobs.harnesses["codex"] = SimpleNamespace(run=agent_run)
        return service

    monkeypatch.setattr(runner, "Service", service_factory)
    report = await runner.run("codex", tmp_path / "out", dataset, batches="day", rounds=2)
    assert seen == [[{"external_id": "first"}]] * (2 if success else 1)
    assert report["unprocessed"] == 1
    assert len(report["batches"]) == 1 and report["planned_batches"] == 2
    assert report["batches"][0]["unprocessed"] == 1
    assert "后续批次未投放" in render(report)


async def test_batch_retries_keep_the_same_clock_until_all_messages_are_processed(
    tmp_path, monkeypatch
):
    samples = [
        sample("first", "2026-10-12T08:00:00+08:00"),
        sample("second", "2026-10-12T09:00:00+08:00"),
        sample("future", "2026-10-13T08:00:00+08:00"),
    ]
    dataset = write_dataset(tmp_path / "dataset", samples)
    actual_service = runner.Service
    clocks = []

    def service_factory(settings, **kwargs):
        service = actual_service(settings, **kwargs)

        async def agent_run(prompt, config, timeout):
            clocks.append(re.search(r"当前本地时间：(\S+?)。", prompt)[1])
            if len(clocks) <= 2:
                assert service.db.rows("SELECT count(*) AS n FROM messages")[0]["n"] == 2
            service.store.mark_processed([service.store.unprocessed(limit=1)[0]["id"]])
            return SimpleNamespace(success=True, error=None)

        service.jobs.harnesses["codex"] = SimpleNamespace(run=agent_run)
        return service

    monkeypatch.setattr(runner, "Service", service_factory)
    report = await runner.run("codex", tmp_path / "out", dataset, batches="day", rounds=2)
    assert clocks == [samples[1]["sent_at"], samples[1]["sent_at"], samples[2]["sent_at"]]
    assert [len(b["attempts"]) for b in report["batches"]] == [2, 1]
    assert report["unprocessed"] == 0


def test_extra_sources_are_distinct_from_extra_tasks_and_keep_legacy_fp():
    samples = [sample("task", "2026-10-12T08:00:00+08:00")]
    notice = sample("notice", "2026-10-12T08:01:00+08:00")
    notice["label"].update(is_todo=False, title=None, due_at=None, kind="notice")
    samples.append(notice)
    predictions = {
        "todos": [
            {"title": "单独误建", "source_refs": ["notice"]},
            {"title": "正确任务，多引用通知", "source_refs": ["task", "notice", "notice"]},
            {"title": "第二个多引用通知的任务", "source_refs": ["task", "notice"]},
            {"title": "无来源误建", "source_refs": []},
            {"title": "未知来源误建", "source_refs": ["unknown"]},
            {"title": "已删除不计", "source_refs": ["notice"], "status": "deleted"},
        ]
    }
    report = score(samples, META, predictions)
    assert report["detection"]["fp"] == 1
    assert report["detection"]["extra_source"] == 1
    assert report["detection"]["extra_task"] == 3
    assert len(report["errors"]["false_positive"]) == 1
    assert report["errors"]["extra_source"][0]["id"] == "notice"
    assert report["errors"]["extra_source"][0]["predicted_title"] == "正确任务，多引用通知"
    assert len(report["errors"]["extra_task"]) == 3
    assert "多引用非待办消息" in render(report) and "额外待办" in render(report)


def test_completed_labels_only_change_tasks_with_explicit_completion_evidence():
    assert META["version"] == 4
    completed = {s["id"] for s in SAMPLES[:190] if s["label"].get("status") == "completed"}
    assert completed == {"a23", "i03", "i06"}
    assert validate(SAMPLES, META)
    for ref in ("g01", "h03", "h05", "i01", "l03", "j04", "j01", "j07"):
        label = next(s for s in SAMPLES if s["id"] == ref)["label"]
        assert label.get("status", "pending") == "pending"


def test_final_state_scores_completed_and_rejects_pending_or_deleted():
    samples = [copy.deepcopy(SAMPLES[0])]
    label = samples[0]["label"]
    label.update(status="completed", duplicates=[])
    validate(samples, META)
    todo = {"title": label["title"], "source_refs": [samples[0]["id"]], "status": "completed"}
    report = score(samples, META, {"todos": [todo]})
    assert report["final_state"]["completed"] == 1
    assert report["final_state"]["accuracy"] == 1
    assert "标注已完成 / 完成任务完全正确 | 1 / 1" in render(report)
    for status in ("pending", "deleted", "cancelled"):
        todo["status"] = status
        assert score(samples, META, {"todos": [todo]})["final_state"]["status_wrong"] == 1


@pytest.mark.parametrize(
    "summary",
    [
        "**新待办**\n- 班级群：交表，10月16日 17:00\n\n**通知**\n- 班级群：周四停课\n\n**还不确定**\n- 班级群：可能改期，待确认",
        "**通知**\n- 班级群：周四停课",
        "**还不确定**\n- 班级群：预览，没看到全文",
        "**新待办**\n- " + "群：" + "字" * 28,
        "没有新的重要消息。",
    ],
)
def test_digest_format_accepts_prompt_sections_optional_sections_and_empty_batch(summary):
    report = score(
        SAMPLES, META, {"digests": [{"date": "2026-10-15", "plugin": "feishu", "summary": summary}]}
    )
    assert report["digest_format"] == {
        "available": True,
        "evaluated": 1,
        "compliant": 1,
        "compliance_rate": 1.0,
    }
    assert not report["errors"]["digest_format"]


@pytest.mark.parametrize(
    "summary,code",
    [
        ("本批摘要如下：\n**通知**\n- 班级群：停课", "invalid_line"),
        ("**其他**\n- 班级群：停课", "unknown_section"),
        ("**通知**\n- 班级群：停课\n\n**新待办**\n- 班级群：交表", "section_order"),
        ("**通知**\n- 班级群：停课\n\n**通知**\n- 班级群：换教室", "section_order"),
        ("**通知**\n1. 班级群：停课", "invalid_line"),
        ("**通知**\n- 班级群：" + "字" * 27, "item_too_long"),
        ("**通知**", "empty_section"),
        ("", "empty_summary"),
        ("**通知**\n- 班级群：读了 12 条消息", "process_language"),
        ("**通知**\n- 班级群：新增了三条待办", "process_language"),
        ("**通知**\n- 班级群：置信度0.9", "process_language"),
        ("**通知**\n- 班级群：todos_add 成功", "process_language"),
        ("**通知**\n- 班级群：source_excerpt_mismatch", "process_language"),
        ("**通知**\n- 班级群：tool_failed", "process_language"),
        ("**通知**\n- 班级群：已请求读取全文", "process_language"),
        ("**通知**\n- 班级群：无需处理，已忽略", "process_language"),
        ("**通知**\n- 班级群：调用todos_add失败", "process_language"),
        ("**通知**\n- 班级群：source_plugin_mismatch", "process_language"),
        ("**通知**\n- 班级群：停课\n**还不确定**\n- 班级群：可能改期", "section_spacing"),
        ("**通知**\n- 今天停课", "missing_source"),
        ("**通知**\n- ", "empty_item"),
    ],
)
def test_digest_format_reports_violations_with_origin_and_line(summary, code):
    report = score(
        SAMPLES, META, {"digests": [{"date": "2026-10-15", "plugin": "feishu", "summary": summary}]}
    )
    assert report["digest_format"]["compliance_rate"] == 0
    error = report["errors"]["digest_format"][0]
    assert error["index"] == 0 and error["date"] == "2026-10-15" and error["plugin"] == "feishu"
    assert any(v["code"] == code and v["line"] >= 1 for v in error["violations"])
    assert code in render(report)


@pytest.mark.parametrize("predictions,available", [({}, False), ({"digests": []}, True)])
def test_missing_digests_are_not_reported_as_perfect(predictions, available):
    result = score(SAMPLES, META, predictions)["digest_format"]
    assert result == {
        "available": available,
        "evaluated": 0,
        "compliant": 0,
        "compliance_rate": None,
    }


def test_digest_compliance_rate_counts_digests_not_violations():
    report = score(
        SAMPLES,
        META,
        {
            "digests": [
                {"summary": "没有新的重要消息。"},
                {"summary": "开场白\n**错误节名**\n内容"},
            ]
        },
    )
    assert report["digest_format"]["compliance_rate"] == 0.5
    assert len(report["errors"]["digest_format"]) == 1


@pytest.mark.parametrize("harness", ["claude_code", "codex"])
async def test_mock_updates_and_cancels_the_same_task_across_daily_batches(tmp_path, harness):
    samples = [
        sample(
            "original", "2026-10-12T08:00:00+08:00", "请于10月16日17:00前提交系统实践实验报告。"
        ),
        sample("change", "2026-10-13T08:00:00+08:00", "系统实践实验报告延期到10月20日17:00交。"),
        sample("cancel", "2026-10-14T08:00:00+08:00", "系统实践实验报告取消，不用提交。"),
    ]
    for s in samples:
        s["label"].update(status="cancelled", duplicates=[o["id"] for o in samples if o is not s])
    dataset = write_dataset(tmp_path / "dataset", samples)
    out = tmp_path / "out"
    report = await runner.run(harness, out, dataset, batches="day", mock=True, timeout=120)
    assert report["attempts"] == [{"success": True, "error": None}] * 3
    assert report["unprocessed"] == 0 and report["final_state"]["accuracy"] == 1
    predictions = json.loads((out / "predictions.json").read_text())
    assert len(predictions["todos"]) == 1
    todo = predictions["todos"][0]
    assert todo["status"] == "cancelled" and set(todo["source_refs"]) == {s["id"] for s in samples}
    assert datetime.fromisoformat(todo["due_at"]) == datetime.fromisoformat(
        "2026-10-20T17:00:00+08:00"
    )
    assert {d["date"] for d in predictions["digests"]} == {s["sent_at"][:10] for s in samples}
    assert report["digest_format"]["compliance_rate"] == 1
