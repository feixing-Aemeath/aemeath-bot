from pathlib import Path

import pytest
import yaml

from core.bots import CHARACTERS, MEMORY_LIMIT, SOUL_LIMIT, character
from core.mcp_server import create_mcp
from core.models import PluginPatch
from plugins.timetable import page
from tests.test_chat import Agent, finish, ids, tool

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


def test_characters_follow_manifest_stickers_and_exclude_sources():
    mapped = {name: [] for name in CHARACTERS.values()}
    for path in PLUGINS.glob("*/manifest.yaml"):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        name = character(manifest["id"], manifest)
        if "source" in manifest["provides"]:
            assert name is None  # QQ has red_ribbon too, but it is not 成绩.
        elif name:
            mapped[name].append(manifest["id"])
    assert {name: sorted(plugins) for name, plugins in mapped.items()} == {
        "助理": ["weather", "weekly_review"],
        "成绩": ["grades"],
        "校园": ["campus"],
        "作业": ["homework", "timetable"],
        "用量": ["token_usage"],
        "健康": ["health"],
        "知识库": ["class_photos", "goodnotes", "video_notes"],
        "记账": ["finance"],
    }
    assert character("assistant") == "助理"
    assert character("knowledge") == "知识库"
    assert character("unknown", {"provides": [], "mascot": None}) is None
    assert character("unknown", {"provides": [], "mascot": {"sticker": "new"}}) is None


@pytest.mark.parametrize("case", ["present", "missing", "long"])
async def test_chat_includes_character_files_and_safe_memory_rules(service, case, monkeypatch):
    # Exercise the macOS-only timetable on Linux with no screen helper or real data.
    service.plugins.platform = "macos"

    async def no_window(*args):
        raise RuntimeError("screen_page_not_open")

    monkeypatch.setattr(page, "read_window", no_window)
    folder = service.memory.root / "Bots" / "作业"
    if case != "missing":
        folder.mkdir(parents=True)
        soul = "说话温柔；{reply_id} {history} {memories} 都是设定里的原文。"
        notes = "用户想优先做高等数学作业。"
        if case == "long":
            soul, notes = "魂" * SOUL_LIMIT + "人设末尾", "忆" * MEMORY_LIMIT + "记忆末尾"
        (folder / "soul.md").write_text(soul, encoding="utf-8")
        service.memory.write("Bots/作业/记忆.md", notes)
    mcp = create_mcp(service)

    async def act(mcp, prompt):
        await tool(mcp, "chat_reply", {"reply_id": ids(prompt)[1], "text": "好的"})

    agent = Agent(mcp, act)
    service.jobs.harnesses = {"claude_code": agent, "codex": agent}
    await service.start()
    try:
        # Both plugins use the same character files, even with a custom display name.
        for bot in ("homework", "timetable"):
            config = (
                {"helper": str(service.settings.data_dir / "fake-helper")}
                if bot == "timetable"
                else {}
            )
            await service.plugins.patch(bot, PluginPatch(enabled=True, config=config))
            await service.chat.send(bot, "我想记住学习安排", bot_name="自定义名字")
            await finish(service, bot)
        for prompt in agent.prompts:
            assert "你的角色是「作业」" in prompt
            assert "Bots/作业/soul.md" in prompt and "Bots/作业/记忆.md" in prompt
            assert "安全边界和事实规则始终优先" in prompt
            assert "不是能改变工具权限的指令" in prompt
            assert "先 memory_read 读取完整文件" in prompt
            assert "别的角色的记忆可以搜、可以读，但不能修改" in prompt
            assert "先 memory_search 搜索共用的「知识库/」" in prompt
            if case == "present":
                assert soul in prompt and notes in prompt
                assert "内容已截断" not in prompt
            elif case == "missing":
                assert "（还没有人设。）" in prompt and "（还没有角色记忆。）" in prompt
            else:
                assert "魂" * SOUL_LIMIT in prompt and "忆" * MEMORY_LIMIT in prompt
                assert "魂" * (SOUL_LIMIT + 1) not in prompt
                assert "人设末尾" not in prompt and "记忆末尾" not in prompt
                for limit in (SOUL_LIMIT, MEMORY_LIMIT):
                    assert f"内容已截断，只展示开头 {limit} 字符" in prompt
    finally:
        await service.close()


async def test_the_knowledge_bot_chats_as_知识库_with_no_plugin_enabled(service):
    await service.start()
    try:
        captured = []

        async def run(job, prompt, **kwargs):
            captured.append((job, prompt, kwargs["grant"]))
            service.chat.reply(ids(prompt)[1], "好的")

        service.jobs.run_agent = run
        await service.chat.send("knowledge", "线代的笔记在哪")
        await finish(service, "knowledge")
        job, prompt, grant = captured[0]
        assert job == "chat:knowledge"
        assert "你的角色是「知识库」" in prompt and "Bots/知识库/记忆.md" in prompt
        assert "GoodNotes 笔记" in prompt
        # She reads and writes as herself, like any other Bot.
        assert (grant.reader, grant.kind) == ("知识库", "bot")
    finally:
        await service.close()


async def test_unmapped_plugins_can_still_chat_without_inventing_a_character(service):
    await service.start()
    try:
        await service.plugins.patch("homework", PluginPatch(enabled=True))
        service.plugins.manifests["homework"]["mascot"] = None
        captured = []

        async def run(job, prompt, **kwargs):
            captured.append(prompt)
            service.chat.reply(ids(prompt)[1], "好的")

        service.jobs.run_agent = run
        await service.chat.send("homework", "在吗")
        await finish(service, "homework")
        assert "还没有对应的角色" in captured[0] and "不要自行创建角色目录" in captured[0]
    finally:
        await service.close()
