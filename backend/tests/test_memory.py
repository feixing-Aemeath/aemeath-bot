import errno
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

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


def test_disk_full_memory_write_keeps_original_and_removes_temp(service, monkeypatch):
    service.memory.write("existing.md", "原来的笔记")
    temporary_file = tempfile.NamedTemporaryFile

    @contextmanager
    def full_file(*args, **kwargs):
        with temporary_file(*args, **kwargs) as stream:

            class Output:
                name = stream.name

                def write(self, _content):
                    stream.write("未写完的内容")
                    raise OSError(errno.ENOSPC, "synthetic disk full")

            yield Output()

    monkeypatch.setattr("core.memory.tempfile.NamedTemporaryFile", full_file)
    with pytest.raises(OSError):
        service.memory.write("existing.md", "新的笔记")
    assert (service.memory.root / "existing.md").read_text() == "原来的笔记"
    assert list(service.memory.root.glob(".memory-*.tmp")) == []


@pytest.mark.parametrize("content", ["第一行\n第二行\n", "第一行\r\n第二行\r\n"])
def test_memory_save_preserves_line_endings_on_windows(service, monkeypatch, content):
    temporary_file = tempfile.NamedTemporaryFile

    def windows_file(*args, **kwargs):
        kwargs.setdefault("newline", "\r\n")
        return temporary_file(*args, **kwargs)

    monkeypatch.setattr("core.memory.tempfile.NamedTemporaryFile", windows_file)
    service.memory.write("newlines.md", content)
    assert (service.memory.root / "newlines.md").read_bytes() == content.encode("utf-8")
    assert list(service.memory.root.glob(".memory-*.tmp")) == []


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


@pytest.mark.parametrize("query", ["共同", "共同关键词"])
def test_deleted_matches_do_not_consume_search_limit(service, query):
    for name in ("deleted.md", "live.md"):
        service.memory.write(name, "共同关键词")
    (service.memory.root / "deleted.md").unlink()
    assert [row["path"] for row in service.memory.search(query, 1)] == ["live.md"]


def test_reindex_does_not_lose_a_concurrent_memory_write(service, monkeypatch):
    memory = service.memory
    memory.write("note.md", "旧的关键词")
    scanned, resume, indexing = threading.Event(), threading.Event(), threading.Event()
    reader, index_one = memory.documents.read, memory.index_one
    reindex_thread = []

    def read(path, *args, **kwargs):
        result = reader(path, *args, **kwargs)
        if threading.get_ident() in reindex_thread and not scanned.is_set():
            scanned.set()
            assert resume.wait(5)
        return result

    def index(path):
        indexing.set()
        return index_one(path)

    def reindex():
        reindex_thread.append(threading.get_ident())
        return memory.reindex()

    monkeypatch.setattr(memory.documents, "read", read)
    monkeypatch.setattr(memory, "index_one", index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan = pool.submit(reindex)
        try:
            assert scanned.wait(5)
            write = pool.submit(memory.write, "note.md", "新的关键词")
            assert indexing.wait(5)
            try:
                write.result(timeout=1)
            except TimeoutError:
                pass  # An index rebuild may serialize the writer until its snapshot is installed.
        finally:
            resume.set()
        scan.result(timeout=5)
        write.result(timeout=5)
    assert memory.read("note.md")["content"] == "新的关键词"
    assert memory.search("新的关键词")
    assert memory.search("旧的关键词") == []
