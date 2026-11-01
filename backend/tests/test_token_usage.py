"""Token counts from the Agent CLIs, how the core records them and the token_usage plugin."""

import json
import shutil
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.db import uid
from core.models import PluginPatch, RuntimeSettings
from harness.base import RunResult, Usage
from harness.claude_code import ClaudeCodeHarness
from harness.codex import CodexHarness
from plugins.token_usage.sessions import COUNTING, SCHEMA, import_sessions, recount_claude
from plugins.token_usage.usage import Ledger, job_name, short
from tests.test_agent_runner import Scripted
from tests.test_api import client_for

ZONE = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 10, 23)
CLAUDE_RESULT = {
    "type": "result",
    "is_error": False,
    "result": "私密的回答",
    "total_cost_usd": 0.42,
    "usage": {
        "input_tokens": 10,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 5000,
        "output_tokens": 300,
    },
}
CODEX_EVENTS = [
    {"type": "thread.started", "thread_id": "t"},
    {"type": "turn.completed", "usage": {"input_tokens": 2000, "cached_input_tokens": 1500,
                                         "output_tokens": 100}},
    {"type": "turn.completed", "usage": {"input_tokens": 3000, "cached_input_tokens": 0,
                                         "output_tokens": 50}},
]  # fmt: skip


def printing(harness_class, tmp_path, lines, code=0):
    class Stub(harness_class):
        executable = sys.executable

        def command(self, mcp_config):
            text = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
            return [sys.executable, "-c", f"import sys; print({text!r}); sys.exit({code})"]

    return Stub(tmp_path)


async def test_claude_code_and_codex_usage_are_parsed(tmp_path):
    claude = await printing(ClaudeCodeHarness, tmp_path, [CLAUDE_RESULT]).run(
        "", {"environment": {}}, 10
    )
    assert claude.success
    assert claude.usage == Usage(6010, 5000, 300, 0.42)
    codex = await printing(CodexHarness, tmp_path, CODEX_EVENTS).run("", {"environment": {}}, 10)
    assert codex.usage == Usage(5000, 1500, 150, None)
    # A run that hit its quota still used tokens before it stopped.
    failed = await printing(
        CodexHarness, tmp_path, [*CODEX_EVENTS[:2], {"type": "turn.failed", "error": "quota"}]
    ).run("", {"environment": {}}, 10)
    assert failed.error == "quota_exhausted" and failed.usage == Usage(2000, 1500, 100)
    exited = await printing(ClaudeCodeHarness, tmp_path, [CLAUDE_RESULT], code=1).run(
        "", {"environment": {}}, 10
    )
    assert not exited.success and exited.usage.output_tokens == 300
    # Malformed counts are ignored instead of failing the run.
    odd = {
        **CLAUDE_RESULT,
        "total_cost_usd": "1",
        "usage": {"input_tokens": -5, "output_tokens": True},
    }
    result = await printing(ClaudeCodeHarness, tmp_path, [odd]).run("", {"environment": {}}, 10)
    assert result.success and result.usage == Usage()


async def test_codex_connection_retries_do_not_fail_a_completed_turn(tmp_path):
    retried = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "error", "message": "Reconnecting... 1/5"},
        CODEX_EVENTS[1],
    ]
    result = await printing(CodexHarness, tmp_path, retried).run("", {"environment": {}}, 10)
    assert result.success and result.usage == Usage(2000, 1500, 100)
    gave_up = [*retried[:2], {"type": "error", "message": "stream disconnected: quota exceeded"}]
    result = await printing(CodexHarness, tmp_path, gave_up).run("", {"environment": {}}, 10)
    assert result.error == "quota_exhausted"


async def test_run_agent_records_usage_per_attempt(service):
    service.db.put("settings", RuntimeSettings(harness_order=["claude_code", "codex"]).model_dump())
    service.jobs.harnesses = {
        "claude_code": Scripted(RunResult(False, "quota_exhausted", Usage(100, 0, 10, 0.01))),
        "codex": Scripted(RunResult(True, usage=Usage(2000, 500, 200))),
    }
    assert await service.jobs.run_agent("extract_todos", "提示词") == "codex"
    rows = {r["harness"]: r for r in service.db.usage_since("2000-01-01")}
    runs = {r["harness"]: r["id"] for r in service.store.runs()["items"]}
    assert rows["claude_code"]["run_id"] == runs["claude_code"]
    assert (rows["codex"]["input_tokens"], rows["codex"]["output_tokens"]) == (2000, 200)
    assert rows["codex"]["cost_usd"] is None and rows["codex"]["job"] == "extract_todos"
    # Only numbers are stored.
    assert set(rows["codex"]) == {
        "run_id", "job", "harness", "recorded_at", "input_tokens", "cached_input_tokens",
        "output_tokens", "cost_usd",
    }  # fmt: skip


def record(service, local, harness, job, input_tokens, output_tokens, cost=None, error=None):
    """A finished run at a local time, with its usage row."""
    at = datetime.fromisoformat(local).replace(tzinfo=ZONE).astimezone(ZoneInfo("UTC")).isoformat()
    run_id = uid()
    service.db.execute(
        "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
        (run_id, job, harness, at, at, "failed" if error else "succeeded", error, 1),
    )
    service.db.execute(
        "INSERT INTO run_usage VALUES(?,?,?,?,?,?,?,?)",
        (run_id, job, harness, at, input_tokens, 0, output_tokens, cost),
    )


def seed(service):
    record(service, "2026-09-05T10:00", "claude_code", "extract_todos", 400_000, 0, 4.0)
    record(service, "2026-10-02T10:00", "claude_code", "extract_todos", 900_000, 100_000, 10.0)
    record(service, "2026-10-21T10:00", "claude_code", "extract_todos", 140_000, 10_000, 1.0)
    record(service, "2026-10-22T09:00", "claude_code", "extract_todos", 10_000, 0, 0.1,
           error="quota_exhausted")  # fmt: skip
    record(service, "2026-10-22T09:05", "codex", "extract_todos", 180_000, 20_000)
    record(service, "2026-10-22T23:30", "codex", "plugin:health:morning_report", 50_000, 0)
    record(service, "2026-10-23T08:00", "codex", "plugin:x:other", 7_000, 0)


def test_cards_and_summary(service):
    seed(service)
    config = {
        "balances": {"claude_code": {"amount_usd": 20, "as_of": "2026-10-01"}},
        "low_balance_usd": 15,
        "prices": {"codex": {"input": 1, "output": 10}},
        "manual_totals": [{"name": "DeepSeek", "tokens": 1_200_000_000}],
    }
    ledger = Ledger(service.db, ZONE, TODAY, config)
    cards = {c["title"]: c["data"] for c in ledger.cards()}
    assert list(cards) == [
        "总共使用的 token",
        "Claude 余额",
        "本月总消耗",
        "最近 7 天消耗",
    ]
    # All time, with the DeepSeek count entered by hand.
    assert cards["总共使用的 token"] == {
        "kind": "donut",
        "value": "1.2B",
        "unit": "tokens",
        "label": "Claude 1.56M · GPT 257k · DeepSeek 1.2B",
        "points": [
            {"x": "Claude", "y": 1_560_000},
            {"x": "GPT", "y": 257_000},
            {"x": "DeepSeek", "y": 1_200_000_000},
        ],
    }
    # $20 minus 10 + 1 + 0.1 since Oct 1; 1.1 over the 7 full days before today.
    assert cards["Claude 余额"]["value"] == "$8.90"
    assert cards["Claude 余额"]["label"] == "按最近 7 天的用量还能用 57 天左右"
    assert cards["Claude 余额"]["delta"] == "每天约 -$0.16"
    month = cards["本月总消耗"]
    assert month["value"] == "1.42M" and month["unit"] == "tokens"
    assert month["label"] == "Claude Code 1.16M · Codex 257k"
    assert month["delta"] == "比上月同期 +254%" and month["trend"] == "up"
    chart = cards["最近 7 天消耗"]
    assert chart["kind"] == "line" and chart["unit"] == "千 tokens"
    assert chart["points"][-3:] == [
        {"x": "10/21", "y": 150.0}, {"x": "10/22", "y": 260.0}, {"x": "10/23", "y": 7.0}
    ]  # fmt: skip
    assert ledger.summary().splitlines() == [
        "昨天一共用了 **260k** tokens，比前天多 73%。",
        "提炼待办时 Claude Code 额度用完了 1 次，自动换成了 Codex。",
        "Claude 余额低于 $15.00，按最近 7 天的用量还能用 57 天左右。",
    ]


def test_empty_and_formatting(service):
    assert Ledger(service.db, ZONE, TODAY, {}).cards() == [
        {"type": "list", "title": "Token 消耗", "data": {"items": [
            "还没有 token 记录。助手的 Agent 完成一次任务、或者本机的 Claude Code、Codex 会话记录"
            "被读到之后，这里会显示每天的 token 消耗。"
        ]}}
    ]  # fmt: skip
    assert Ledger(service.db, ZONE, TODAY, {}).summary() == ""
    assert [short(n) for n in (850, 1000, 21_500, 210_000, 1_820_000, 4_654_295_396)] == [
        "850", "1k", "21.5k", "210k", "1.82M", "4.65B"
    ]  # fmt: skip


async def test_plugin_cards_and_daily_digest(service, monkeypatch):
    # The plugin reads ~/.claude and ~/.codex; keep the real ones out of the test.
    monkeypatch.setenv("HOME", str(service.settings.data_dir))
    record(service, datetime.now(ZONE).replace(tzinfo=None).isoformat(), "codex", "extract_todos",
           1000, 0)  # fmt: skip
    with client_for(service) as client:
        views = {v["id"]: v for v in client.get("/api/v1/plugins").json()}
        view = views["token_usage"]
        assert view["mascot"] == {"sticker": "shouanren", "accent": "#3E7FD6"}
        bad = {"config": {"balances": {"codex": {"amount_usd": -1, "as_of": "2026-10-01"}}}}
        assert client.patch("/api/v1/plugins/token_usage", json=bad).status_code == 422
        assert (
            client.patch("/api/v1/plugins/token_usage", json={"enabled": True}).status_code == 200
        )
        cards = client.get("/api/v1/plugins/token_usage/cards").json()
        # Limit cards (test_token_limits.py) depend on when the first read finishes.
        cards = [c for c in cards if "用量限额" not in c["title"]]
        assert [c["title"] for c in cards] == [
            "总共使用的 token", "本月总消耗", "最近 7 天消耗"
        ]  # fmt: skip
        assert cards[0]["data"]["points"] == [{"x": "GPT", "y": 1000}]
        assert cards[2]["data"]["points"][-1]["y"] == 1.0
    await service.plugins.patch(
        "token_usage",
        PluginPatch(config={"balances": {"claude_code": {"amount_usd": 3, "as_of": "2026-10-01"}}}),
    )
    await service.plugins.instances["token_usage"].check()
    today = datetime.now(ZONE).date().isoformat()
    digest = service.store.digests(day=today, plugin="token_usage")["items"][0]
    assert digest["summary"].startswith("Claude 余额低于 $15.00")


def claude_session(path):
    reply = {
        "type": "assistant",
        "sessionId": "s1",
        "requestId": "req_1",
        "timestamp": "2026-10-22T02:00:00.000Z",
        "message": {
            "id": "msg_1",
            "content": [{"type": "text", "text": "私密的回答"}],
            "usage": {"input_tokens": 5, "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 4000, "output_tokens": 10},
        },
    }  # fmt: skip
    # One reply in three lines (thinking, text, tool use), each repeating the reply's usage;
    # the first logged before the final output count. Each line counts, as in the App's panel.
    blocks = []
    for uuid, output in (("u1", 10), ("u2", 300), ("u3", 300)):
        block = json.loads(json.dumps(reply))
        block["uuid"] = uuid
        block["message"]["usage"]["output_tokens"] = output
        blocks.append(block)
    lines = [
        {"type": "user", "sessionId": "s1", "message": {"content": "私密的问题"}},
        *blocks,
        "not json",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(line if isinstance(line, str) else json.dumps(line, ensure_ascii=False)
                  for line in lines),
        encoding="utf-8",
    )  # fmt: skip


def codex_session(path):
    def count(at, total_in, cached, out):
        usage = {"input_tokens": total_in, "cached_input_tokens": cached, "output_tokens": out}
        return {
            "timestamp": at,
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"total_token_usage": usage}},
        }

    lines = [
        {"timestamp": "2026-10-22T03:00:00Z", "type": "session_meta", "payload": {"id": "c1"}},
        {"timestamp": "2026-10-22T03:00:01Z", "type": "response_item",
         "payload": {"type": "message", "content": "私密的内容"}},
        count("2026-10-22T03:00:02Z", 30_000, 0, 200),
        count("2026-10-22T03:00:03Z", 30_000, 0, 200),  # repeated: nothing new
        count("2026-10-23T03:00:00Z", 64_000, 20_000, 500),
        {"timestamp": "2026-10-23T03:00:01Z", "type": "event_msg",
         "payload": {"type": "token_count", "info": None}},
    ]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines))


def test_session_files_are_imported_once(service, tmp_path):
    claude_session(tmp_path / "claude" / "-proj" / "s1.jsonl")
    codex_session(tmp_path / "codex" / "2026" / "10" / "22" / "rollout-c1.jsonl")
    # The same files copied over from Windows into the imports folder.
    shutil.copytree(tmp_path / "codex", tmp_path / "imports" / "codex")
    shutil.copytree(tmp_path / "claude", tmp_path / "imports" / "claude")
    folders = [str(tmp_path / name) for name in ("claude", "codex", "imports", "missing")]
    state = import_sessions(service.db, folders, {})
    assert len(state) == 4
    rows = service.db.rows("SELECT * FROM local_usage ORDER BY recorded_at,key")
    assert [
        (r["harness"], r["input_tokens"], r["cached_input_tokens"], r["output_tokens"])
        for r in rows
    ] == [
        ("claude_code", 5005, 4000, 10),
        ("claude_code", 5005, 4000, 300),
        ("claude_code", 5005, 4000, 300),
        ("codex", 30_000, 0, 200),
        ("codex", 34_000, 20_000, 300),
    ]
    assert "私密" not in json.dumps(rows, ensure_ascii=False)
    # Unchanged files are not read again; a changed one is, without counting twice.
    assert import_sessions(service.db, folders, state) == state
    with open(tmp_path / "claude" / "-proj" / "s1.jsonl", "a") as file:
        file.write("\n")
    import_sessions(service.db, folders, state)
    assert service.db.rows("SELECT count(*) AS n FROM local_usage")[0]["n"] == 5

    record(service, "2026-10-22T10:00", "claude_code", "extract_todos", 100_000, 0, 1.0)
    config = {"balances": {"claude_code": {"amount_usd": 20, "as_of": "2026-10-01"}}}
    ledger = Ledger(service.db, ZONE, TODAY, config)
    cards = {c["title"]: c["data"] for c in ledger.cards()}
    assert list(cards) == [
        "总共使用的 token", "Claude 余额", "本月总消耗", "本月自己用的", "最近 7 天消耗"
    ]  # fmt: skip
    # Your own use does not count against the balance.
    assert cards["Claude 余额"]["value"] == "$19.00"
    assert cards["本月总消耗"]["value"] == "180k"
    assert cards["本月自己用的"]["value"] == "80.1k"
    assert cards["本月自己用的"]["label"] == "Claude Code 15.6k · Codex 64.5k"
    series = {s["name"]: s["points"][-2:] for s in cards["最近 7 天消耗"]["series"]}
    assert series == {
        "助手任务": [{"x": "10/22", "y": 100.0}, {"x": "10/23", "y": 0.0}],
        "自己用的": [{"x": "10/22", "y": 45.8}, {"x": "10/23", "y": 34.3}],
    }
    assert ledger.summary().startswith("昨天一共用了 **146k** tokens（自己用的 45.8k）")


def test_chart_switches_to_millions_on_heavy_days(service):
    record(service, "2026-10-22T10:00", "claude_code", "extract_todos", 153_277_500, 0)
    chart = {c["title"]: c["data"] for c in Ledger(service.db, ZONE, TODAY, {}).cards()}
    assert chart["最近 7 天消耗"]["unit"] == "百万 tokens"
    assert chart["最近 7 天消耗"]["points"][-2:] == [
        {"x": "10/22", "y": 153.3},
        {"x": "10/23", "y": 0.0},
    ]


def test_chat_replies_are_one_task():
    assert job_name("chat:assistant") == job_name("chat:health") == "和 Bot 聊天"
    assert job_name("extract_todos") == "提炼待办"


def test_deepseek_runs_and_a_hand_entered_total_are_one_family(service):
    # The App showed "deepseek" (the assistant's runs) and "DeepSeek" (typed in) side by side.
    record(service, "2026-10-22T10:00", "deepseek", "plugin:weather:brief", 132_000, 0)
    record(service, "2026-10-22T11:00", "codex", "extract_todos", 1_000, 0)
    total = Ledger(service.db, ZONE, TODAY, {}).cards()[0]["data"]
    assert [p["x"] for p in total["points"]] == ["GPT", "DeepSeek"]
    typed = {"manual_totals": [{"name": "deepseek", "tokens": 1_200_000_000}]}
    total = Ledger(service.db, ZONE, TODAY, typed).cards()[0]["data"]
    # The typed total is all of it (no as_of), so it replaces what was counted.
    assert total["points"] == [{"x": "GPT", "y": 1_000}, {"x": "DeepSeek", "y": 1_200_000_000}]


def test_rows_counted_per_reply_are_read_again(service):
    with service.db.connect() as conn:
        conn.executescript(SCHEMA)
    for key, harness in (("claude:msg_1:req_1", "claude_code"), ("codex:c1:1:1", "codex")):
        service.db.execute(
            "INSERT INTO local_usage VALUES(?,?,?,?,?,?)",
            (key, harness, "2026-10-22T02:00:00+00:00", 1, 0, 0),
        )
    assert recount_claude(service.db, None)
    left = service.db.rows("SELECT key FROM local_usage")
    assert left == [{"key": "codex:c1:1:1"}]
    assert not recount_claude(service.db, COUNTING)


def test_resumed_sessions_count_copied_lines_again(service, tmp_path):
    # Like the desktop App's panel: a resumed session's file repeats earlier lines, and those
    # count again; a copy of the same file in another folder does not.
    claude_session(tmp_path / "mac" / "-proj" / "s1.jsonl")
    shutil.copy(tmp_path / "mac" / "-proj" / "s1.jsonl", tmp_path / "mac" / "-proj" / "s2.jsonl")
    shutil.copytree(tmp_path / "mac", tmp_path / "imports" / "mac")
    import_sessions(service.db, [str(tmp_path / "mac"), str(tmp_path / "imports")], {})
    assert service.db.rows("SELECT count(*) AS n FROM local_usage")[0]["n"] == 6


def test_hand_entered_total_replaces_what_it_already_includes(service):
    # GPT from the ChatGPT profile page on 10/23 already includes the Codex use counted before.
    record(service, "2026-10-22T10:00", "codex", "extract_todos", 70_000_000, 0)
    record(service, "2026-10-23T10:00", "codex", "extract_todos", 5_000_000, 0)
    record(service, "2026-10-22T10:00", "claude_code", "extract_todos", 1_000_000, 0)
    config = {
        "manual_totals": [
            {"name": "GPT", "tokens": 2_820_000_000, "as_of": "2026-10-23"},
            {"name": "DeepSeek", "tokens": 1_200_000_000},
        ]
    }
    total = Ledger(service.db, ZONE, TODAY, config).cards()[0]["data"]
    assert total["points"] == [
        {"x": "Claude", "y": 1_000_000},
        {"x": "GPT", "y": 2_825_000_000},
        {"x": "DeepSeek", "y": 1_200_000_000},
    ]
    assert total["value"] == "4.03B"
    # Without as_of the entered number stands alone.
    config["manual_totals"][0].pop("as_of")
    total = Ledger(service.db, ZONE, TODAY, config).cards()[0]["data"]
    assert total["points"][1] == {"x": "GPT", "y": 2_820_000_000}
