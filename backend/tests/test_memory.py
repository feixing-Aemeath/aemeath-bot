import pytest
from mcp.server.fastmcp.exceptions import ToolError

from core.mcp_server import create_mcp
from tests.test_chat import tool


async def search(mcp, query):
    _, structured = await mcp.call_tool("memory_search", {"query": query})
    return structured["result"]


def test_fts_chinese_short_query_and_external_changes(service):
    service.memory.write("学业/课程.md", "本周学校通知，需要提交报名表。")
    assert service.memory.search("学校通知")[0]["path"] == "学业/课程.md"
    assert service.memory.search("学校")
    assert service.memory.read("学业/课程.md")["content"].startswith("本周")
    path = service.memory.root / "学业" / "课程.md"
    path.write_text("新的课程安排", encoding="utf-8")
    service.memory.reindex()
    assert service.memory.search("课程安排")
    path.unlink()
    service.memory.reindex()
    assert service.memory.search("课程安排") == []


@pytest.mark.parametrize(
    "path", ["../outside.md", "/etc/passwd.md", "C:\\private.md", "text.txt", "..\\x.md"]
)
def test_path_escape_rejected(service, path):
    with pytest.raises(ValueError):
        service.memory.write(path, "blocked")


def test_fts_query_is_literal(service):
    service.memory.write("a.md", 'quote " literal words')
    assert service.memory.search('" OR *') == []


async def test_unreadable_notes_do_not_block_startup(service):
    root = service.memory.root
    (root / "附件.md").mkdir()
    (root / "旧笔记.md").write_bytes("老师说周五交作业".encode("gbk"))
    (root / "正常.md").write_text("本周提交报名表", encoding="utf-8")
    await service.start()
    try:
        assert service.memory.search("提交报名表")[0]["path"] == "正常.md"
        assert service.memory.reindex() == 1
    finally:
        await service.close()


@pytest.mark.parametrize(
    "path",
    [
        "Bots/成绩/soul.md",
        "Bots/陌生角色/soul.md",
        "Bots\\成绩\\soul.md",
        "./bots/成绩/SOUL.MD",
        "知识库/新笔记.md",
        "知识库/子目录/资料.txt",
        "知识库\\子目录\\资料.md",
    ],
)
async def test_mcp_rejects_writing_souls_and_knowledge(service, path):
    with pytest.raises(ToolError, match="只读"):
        await create_mcp(service).call_tool("memory_write", {"path": path, "content": "不能写"})


async def test_mcp_can_read_souls_and_maintain_character_memory(service):
    mcp = create_mcp(service)
    path = service.memory.root / "Bots" / "助理" / "soul.md"
    path.parent.mkdir(parents=True)
    path.write_text("用户写的人设", encoding="utf-8")
    assert (await tool(mcp, "memory_read", {"path": "Bots/助理/soul.md"}))[
        "content"
    ] == "用户写的人设"
    with pytest.raises(ToolError, match="只读"):
        await mcp.call_tool("memory_write", {"path": "Bots/助理/soul.md", "content": "改写"})
    assert path.read_text(encoding="utf-8") == "用户写的人设"
    notes = "Bots/助理/记忆.md"
    await tool(mcp, "memory_write", {"path": notes, "content": "偏好早上提醒"})
    original = (await tool(mcp, "memory_read", {"path": notes}))["content"]
    await tool(mcp, "memory_write", {"path": notes, "content": original + "\n周末晚点提醒"})
    assert (await search(mcp, "周末晚点提醒"))[0]["path"] == notes


async def test_knowledge_markdown_and_text_are_searchable_and_readonly(service):
    folder = service.memory.root / "知识库" / "学业"
    folder.mkdir(parents=True)
    for filename in ("选课.md", "资料.txt"):
        (folder / filename).write_text("我的选课约定：优先人工智能课程。", encoding="utf-8")
    (folder / "附件.pdf").write_bytes(b"not a note")
    mcp = create_mcp(service)
    assert service.memory.reindex() == 2
    found = await search(mcp, "人工智能课程")
    assert {row["path"] for row in found} == {"知识库/学业/选课.md", "知识库/学业/资料.txt"}
    for row in found:
        assert "人工智能课程" in (await tool(mcp, "memory_read", {"path": row["path"]}))["content"]
        with pytest.raises(ToolError, match="只读"):
            await mcp.call_tool("memory_write", {"path": row["path"], "content": "覆盖"})
    (folder / "资料.txt").unlink()
    assert service.memory.reindex() == 1
    assert len(await search(mcp, "人工智能课程")) == 1


@pytest.mark.parametrize("protected", ["Bots/成绩/soul.md", "知识库/资料.md"])
def test_symlink_alias_cannot_bypass_readonly_memory(service, protected):
    target = service.memory.root / protected
    target.parent.mkdir(parents=True)
    target.write_text("用户原文", encoding="utf-8")
    alias = service.memory.root / "别名.md"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="只读"):
        service.memory.write("别名.md", "覆盖")
    assert target.read_text(encoding="utf-8") == "用户原文"
