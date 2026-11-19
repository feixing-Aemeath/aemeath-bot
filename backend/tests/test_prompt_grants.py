"""Prompt tool names checked against grants captured from the actual job entry points."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.mcp_server import create_mcp
from core.models import PluginPatch
from core.prompts import PROMPTS
from core.tokens import current_grant

NOW = datetime(2026, 11, 5, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
# Template variables, tool arguments, result fields, flags and fixed error codes.
NON_TOOLS = {
    "apple_sync_failed",
    "goals_chat",
    "ask_on",
    "blocker_day",
    "blocker_choices",
    "smaller_step",
    "deadline_label",
    "result_question",
    "result_date",
    "waiting_triggers",
    "days_advanced",
    "step_title",
    "waiting_for_user",
    "attempts_left",
    "baseline_insufficient",
    "bot_name",
    "change_text",
    "chat_name",
    "confidence_threshold",
    "credit_kind",
    "due_at",
    "estimated_from",
    "family_pays",
    "has_time",
    "hrv_ln_rmssd",
    "hrv_ln_sdnn",
    "hrv_preferred",
    "last_night_asleep",
    "last_night_main_sleep",
    "last_night_naps",
    "local_now",
    "mean_text",
    "median_lead_hours",
    "memory_path",
    "message_id",
    "needs_setup",
    "next_week",
    "open_on_demand",
    "rain_chance_percent",
    "rain_hours",
    "readiness_note",
    "reply_id",
    "request_id",
    "rmssd_ms",
    "sdnn_ms",
    "self_joined",
    "self_listed",
    "self_missing",
    "soul_path",
    "source_excerpt",
    "source_excerpt_mismatch",
    "source_plugin",
    "source_reference_missing",
    "source_refs",
    "takeout_count",
    "title_hint",
    "todo_fields_user_modified",
    "todo_id",
    "tool_failed",
    "total_minutes",
    "vs_30d",
    "vs_7d",
}


class PromptCaptured(BaseException):
    pass


@pytest.mark.parametrize("template", sorted(p.stem for p in PROMPTS.glob("*.md")))
async def test_prompt_tools_are_allowed_by_the_actual_runner_grant(seeded, monkeypatch, template):
    service = seeded
    captured = []

    async def capture(job, prompt, **options):
        captured.append((job, prompt, options["grant"]))
        raise PromptCaptured()

    monkeypatch.setattr(service.jobs, "run_agent", capture)
    monkeypatch.setattr(service.plugins, "run_agent", capture)
    mcp = create_mcp(service)
    await service.start()
    try:
        for identifier in (
            "homework",
            "campus",
            "grades",
            "weather",
            "weekly_review",
            "health",
            "finance",
            "goals",
        ):
            await service.plugins.patch(identifier, PluginPatch(enabled=True))
            assert identifier in service.plugins.instances
        plugins = service.plugins.instances
        for plugin in plugins.values():
            monkeypatch.setattr(plugin, "now", lambda: NOW, raising=False)
        monkeypatch.setattr(plugins["weather"], "days", lambda: {NOW.date(): object()})
        source = "知识库/合成课程/资料.txt"
        service.knowledge.topic("合成课程")
        service.knowledge.upload(source, b"synthetic source")

        with pytest.raises(PromptCaptured):
            if template == "extract_todos":
                await service.jobs.extract()
            elif template == "knowledge_organize":
                await service.knowledge.batch({"path": "知识库/合成课程"})
            elif template in {"chat", "goals_chat"}:
                bot = service.chat.bot("assistant")
                await service.chat.answer("assistant", bot, bot["name"], "合成问题", "m", "r", "")
            elif template in {"health_morning", "health_weekly"}:
                await plugins["health"].generate(template.removeprefix("health_"), NOW.date(), NOW)
            elif template == "weather_brief":
                await plugins["weather"].brief()
            elif template == "finance_report":
                await plugins["finance"].monthly_report()
            elif template == "weekly_review":
                await plugins["weekly_review"].review()
            else:
                pytest.fail(f"New prompt needs its runner added here: {template}")

        assert len(captured) == 1
        job, prompt, grant = captured[0]
        text = (PROMPTS / f"{template}.md").read_text(encoding="utf-8")
        mentioned = set(re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", text)) - NON_TOOLS
        assert mentioned and all(name in prompt for name in mentioned)
        # Plugin tools must be checked with their registered owner, not as core tools.
        assert all(grant.allows_tool(name, mcp.plugin_owners.get(name)) for name in mentioned), (
            job,
            mentioned,
            grant,
        )
        token = current_grant.set(grant)
        try:
            listed = {tool.name for tool in await mcp.list_tools()}
            # Chat explicitly makes these optional; their plugins are platform-specific.
            optional = (
                {"timetable_days", "video_notes_add"}
                if template in {"chat", "goals_chat"}
                else set()
            )
            assert mentioned - optional <= listed
            if template == "extract_todos":
                assert "关于我.md" in grant.memory_read_paths
                assert len(grant.memory_read_paths) == 2
                paths = grant.memory_read_paths
            elif template == "weather_brief":
                paths = {
                    f"每日/{NOW.date()}/{name}.md"
                    for name in ("今天", "课程", "待办", "作业", "活动与请假")
                }
            elif template.startswith("health_"):
                # The input tool reads goals internally, without exposing memory_read.
                paths = {plugins["health"].report_config.goals_path}
            elif template == "knowledge_organize":
                paths = {source, "知识库/合成课程/整理/已有笔记.md"}
            else:
                paths = set()
            for path in paths:
                service.memory.access.require_read(path)
        finally:
            current_grant.reset(token)
    finally:
        await service.close()
