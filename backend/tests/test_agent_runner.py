"""Core pieces that plugins use to run an Agent: run_agent, PluginContext, prompt rendering."""

import pytest

from core.db import now, uid
from core.models import PluginPatch, RuntimeSettings
from core.prompts import PROMPTS, render
from harness.base import RunResult
from tests.test_api import client_for
from tests.test_plugins_jobs import make_plugin


class Scripted:
    def __init__(self, result, effect=None):
        self.result, self.effect, self.prompts = result, effect, []

    async def run(self, prompt, mcp_config, timeout):
        self.prompts.append(prompt)
        assert mcp_config["stdio"]["args"] == ["-m", "core.mcp_stdio"]
        assert set(mcp_config["environment"]) == {"ASSISTANT_TOKEN", "ASSISTANT_BASE_URL"}
        if self.effect:
            self.effect()
        return self.result


async def test_run_agent_falls_back_and_checks_the_work(service):
    done = []
    service.jobs.harnesses = {
        "claude_code": Scripted(RunResult(True)),  # exits 0 without writing anything
        "codex": Scripted(RunResult(True), effect=lambda: done.append(True)),
    }
    used = await service.jobs.run_agent(
        "plugin:x:report",
        "提示词",
        succeeded=lambda: bool(done),
        incomplete_error="nothing_written",
    )
    assert used == "codex"
    runs = {r["harness"]: r for r in service.store.runs()["items"]}
    assert runs["claude_code"]["error"] == "nothing_written"
    assert runs["codex"]["status"] == "succeeded"


async def test_run_agent_prefers_the_job_harness_and_raises_when_all_fail(service):
    service.db.put(
        "settings", RuntimeSettings(job_harness={"plugin:x:report": "codex"}).model_dump()
    )
    calls = []

    class Failing(Scripted):
        async def run(self, prompt, mcp_config, timeout):
            calls.append(self.name)
            return RunResult(False, "quota_exhausted")

    service.jobs.harnesses = {name: Failing(None) for name in ("claude_code", "codex")}
    for name, harness in service.jobs.harnesses.items():
        harness.name = name
    with pytest.raises(RuntimeError, match="all_harnesses_failed"):
        await service.jobs.run_agent("plugin:x:report", "提示词")
    assert calls == ["codex", "claude_code"]


async def test_plugins_receive_memory_and_agent_runner(tmp_path):
    from core.config import Settings
    from core.service import Service

    root = tmp_path / "plugins"
    make_plugin(
        root,
        body="""from core.plugins import Plugin as Base
class Plugin(Base):
    async def setup(self, context, config):
        await super().setup(context, config)
        assert context.memory is not None and context.run_agent is not None
    async def report(self):
        name = await self.context.run_agent("plugin:fixture:report", "写一份报告")
        self.context.memory.write("报告/今天.md", "由 " + name + " 生成")
    def jobs(self):
        return {"report": {"cron": "0 9 * * *", "callback": self.report}}
""",
    )
    service = Service(
        Settings(
            token="x" * 40, data_dir=tmp_path / "state", scheduler_enabled=False, _env_file=None
        ),
        plugin_dir=root,
    )
    await service.start()
    try:
        await service.plugins.patch("fixture", PluginPatch(enabled=True))
        assert service.plugins.view("fixture")["state"] == "enabled"
        service.jobs.harnesses = {"claude_code": Scripted(RunResult(True))}
        service.db.put("settings", RuntimeSettings(harness_order=["claude_code"]).model_dump())
        await service.jobs.submit("plugin:fixture:report")
        assert service.memory.read("报告/今天.md")["content"] == "由 claude_code 生成"
    finally:
        await service.close()


def test_render_only_replaces_named_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr("core.prompts.PROMPTS", tmp_path)
    (tmp_path / "x.md").write_text('调用 tool，参数 {"date": "{date}"}，{unknown} 保留', "utf-8")
    assert (
        render("x", date="2026-10-15") == '调用 tool，参数 {"date": "2026-10-15"}，{unknown} 保留'
    )
    assert PROMPTS.name == "prompts"


def test_extract_prompt_is_unchanged_by_the_renderer():
    text = render("extract_todos", local_now="NOW", timezone="TZ", confidence_threshold=0.8)
    assert "当前本地时间：NOW。时区：TZ。待办最低置信度：0.8。" in text
    assert "{" not in text


def test_health_reports_route_filters(service):
    for day, kind, status in [
        ("2026-10-14", "morning", "ready"),
        ("2026-10-15", "morning", "failed"),
        ("2026-10-11", "weekly", "ready"),
    ]:
        service.db.execute(
            "INSERT INTO health_reports(id,date,kind,status,title,body,error,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (uid(), day, kind, status, f"{day} 简报", "正文", None, now(), now()),
        )
    with client_for(service) as client:
        page = client.get("/api/v1/health/reports").json()
        assert page["total"] == 3
        assert [i["date"] for i in page["items"]] == ["2026-10-15", "2026-10-14", "2026-10-11"]
        one = client.get("/api/v1/health/reports?date=2026-10-14").json()["items"]
        assert [(i["kind"], i["status"], i["title"]) for i in one] == [
            ("morning", "ready", "2026-10-14 简报")
        ]
        ready = client.get("/api/v1/health/reports?kind=morning&status=ready").json()
        assert ready["total"] == 1 and "input_json" not in ready["items"][0]
        assert client.get("/api/v1/health/reports?kind=daily").status_code == 422
        assert client.get("/api/v1/health/reports?date=2026-13-01").status_code == 422
