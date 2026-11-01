"""Folder ACLs, source extraction, organizer receipts and the exact App contract, all fake."""

import asyncio
import json
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from core.api import create_app
from core.db import Database
from core.documents import MAX_TEXT, FakeExtractor, MacExtractor, sha256
from core.tokens import MAIN, DeviceTokens, Grant, current_grant
from harness.base import RunResult
from harness.deepseek import DeepSeekHarness
from scripts import tokens as cli
from tests.test_api import client_for
from tests.test_chat import finish, ids


@contextmanager
def as_reader(grant):
    context = current_grant.set(grant)
    try:
        yield
    finally:
        current_grant.reset(context)


def put(service, path, text="共同关键词"):
    target = service.memory.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def bot(name="助理"):
    return Grant(None, "bot", frozenset({"mcp"}), name, "bot")


def agent(service, reader="Codex"):
    token, _ = service.tokens.create(reader, ["mcp"], reader=reader)
    return service.tokens.check(token)


def test_area_discovery_defaults_are_frozen_and_renames_are_new(service):
    put(service, "知识库/课程/一.md")
    put(service, "知识库/散文件.md")
    put(service, "Bots/作业/记忆.md")
    put(service, "关于我.md")
    areas = {a["path"]: a for a in service.knowledge.overview()["areas"]}
    assert set(areas) == {"知识库/课程", "Bots", "."}
    assert areas["."]["name"] == "根目录文件"
    assert areas["Bots"]["locked"] == list(
        __import__("core.bots", fromlist=["CHARACTERS"]).CHARACTERS.values()
    )
    # 知识库 keeps the knowledge base, so new topics are hers to read too by default.
    assert areas["知识库/课程"]["readers"] == ["助理", "知识库"]
    service.memory.root.joinpath("知识库/第二门").mkdir()
    service.memory.access.patch(default_readers=["作业", "Codex"])
    # Newly appeared folders are frozen before defaults change too.
    assert service.knowledge.files("知识库/第二门")["readers"] == ["助理", "知识库"]
    created = service.knowledge.topic("第三门")
    assert created["readers"] == ["作业", "Codex"]
    service.knowledge.patch_access("知识库/课程", [])
    service.memory.root.joinpath("知识库/课程").rename(service.memory.root / "知识库/改名")
    assert service.knowledge.files("知识库/改名")["readers"] == ["作业", "Codex"]
    assert (
        service.db.rows("SELECT readers FROM knowledge_areas WHERE path='知识库/课程'")[0][
            "readers"
        ]
        == "[]"
    )
    assert service.knowledge.overview()["loose_files"] == 1
    assert Database(service.db.path).get("knowledge_settings")["default_readers"] == [
        "作业",
        "Codex",
    ]


@pytest.mark.parametrize("query", ["共同关键词", "共同"])
def test_sql_read_scopes_and_legacy_compatibility(service, query):
    for n in range(8):
        put(service, f"知识库/私密/{n}.md")
    put(service, "知识库/允许/一.md")
    put(service, "知识库/散.md")
    put(service, "关于我.md")
    put(service, "Bots/作业/记忆.md")
    service.knowledge.patch_access("知识库/私密", [])
    service.knowledge.patch_access("知识库/允许", ["Codex"])
    service.memory.reindex()
    restricted = agent(service)
    with as_reader(restricted):
        # Eight forbidden matches ahead of this one cannot consume the SQL LIMIT.
        assert service.memory.search(query, 1)[0]["path"] == "知识库/允许/一.md"
        assert len(service.memory.search(query, 10)) == 1
        for path in ("关于我.md", "Bots/作业/记忆.md", "知识库/私密/0.md", "知识库/散.md"):
            with pytest.raises(ValueError, match="没有读取这个文件夹的权限"):
                service.memory.read(path)
    with as_reader(bot()):
        assert {r["path"] for r in service.memory.search(query)} == {
            "关于我.md",
            "Bots/作业/记忆.md",
        }
    for grant in (MAIN, service.tokens.check(service.tokens.create("legacy", ["mcp"])[0])):
        with as_reader(grant):
            assert len(service.memory.search(query, 50)) == 12
            assert service.memory.read("知识库/散.md")["content"]
    with as_reader(Grant(None, "unmapped", frozenset({"mcp"}), kind="system")):
        assert {r["path"] for r in service.memory.search(query)} == {
            "关于我.md",
            "Bots/作业/记忆.md",
        }


def test_writes_and_agent_removal(service):
    put(service, "Bots/作业/记忆.md")
    put(service, "健康/目标.md")
    put(service, "知识库/课程/资料.md")
    with as_reader(bot("作业")):
        service.memory.write("Bots/作业/记忆.md", "自己的")
        service.memory.write("每日/2026-10-29/作业.md", "分析")
        for path in (
            "Bots/助理/记忆.md",
            "知识库/课程/新.md",
            "健康/目标.md",
            "Bots/作业/SOUL.MD",
        ):
            with pytest.raises(ValueError):
                service.memory.write(path, "不能写")
    grant = agent(service)
    service.knowledge.patch_access("Bots", ["Codex"])
    service.knowledge.patch_access("健康", ["Codex"])
    with as_reader(grant):
        service.memory.write("健康/目标.md", "读者系统区")
        with pytest.raises(ValueError):
            service.memory.write("Bots/作业/记忆.md", "不能写")
    service.memory.access.patch(agents=["Claude Code"])
    with as_reader(grant):
        assert service.memory.search("读者系统区") == []
        with pytest.raises(ValueError):
            service.memory.read("健康/目标.md")
    assert service.tokens.list()[0]["status"] == "active"


async def test_temporary_grants_authentication_chat_and_cleanup(service):
    tokens = []
    app = create_app(service=service)

    class Harness:
        async def run(self, prompt, mcp_config, timeout, **kwargs):
            token = mcp_config["environment"]["ASSISTANT_TOKEN"]
            tokens.append(token)
            assert token != service.settings.token.get_secret_value()
            assert service.tokens.check(token).reader == "助理"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer " + token},
            ) as client:
                assert (await client.get("/internal/tools")).status_code == 200
                assert (await client.get("/api/v1/knowledge")).status_code == 403
                denied = await client.post(
                    "/internal/tools/memory_read", json={"path": "知识库/私密/资料.md"}
                )
                assert "没有读取这个文件夹的权限" in denied.text
                assert "绝密数据" not in denied.text
            service.chat.reply(ids(prompt)[1], "完成")
            return RunResult(True)

    put(service, "知识库/私密/资料.md", "绝密数据")
    service.knowledge.patch_access("知识库/私密", [])
    service.jobs.harnesses = {"codex": Harness()}
    await service.chat.send("assistant", "你好", harness="codex")
    await finish(service)
    assert service.tokens.check(tokens[0]) is None
    assert service.db.rows("SELECT * FROM device_tokens") == []


async def test_temporary_token_timeout_cancel_exception_and_expiry(service):
    seen = []

    class Harness:
        async def run(self, prompt, mcp_config, timeout, **kwargs):
            seen.append(mcp_config["environment"]["ASSISTANT_TOKEN"])
            await asyncio.Event().wait()

    service.jobs.harnesses = {"codex": Harness()}
    task = asyncio.create_task(service.jobs.run_agent("test", "x", grant=bot(), harness="codex"))
    while not seen:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.tokens.check(seen[0]) is None
    clock = datetime.now(UTC)
    registry = DeviceTokens(service.db, clock=lambda: clock)
    token = registry.temporary(bot(), 1)
    clock += timedelta(seconds=2)
    assert registry.check(token) is None


async def test_deepseek_uses_the_same_temporary_credential(service):
    seen = []
    app = create_app(service=service)
    transport = httpx.ASGITransport(app=app)

    async def handle(request):
        if request.url.host == "127.0.0.1":
            token = request.headers["Authorization"].removeprefix("Bearer ")
            seen.append(token)
            assert service.tokens.check(token).reader == "作业"
            return await transport.handle_async_request(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "好"}}]}
        )

    harness = DeepSeekHarness(key="fake", model="fake", transport=httpx.MockTransport(handle))
    service.jobs.harnesses = {"deepseek": harness}
    await service.jobs.run_agent("test", "x", harness="deepseek", grant=bot("作业"))
    assert seen and all(service.tokens.check(token) is None for token in seen)


def test_fake_extraction_cache_failure_and_limits(service):
    folder = service.knowledge.topic("课程")
    assert folder["files"] == 0
    fake = FakeExtractor(
        {
            "a.pdf": {
                "pages": [
                    {"page": 1, "text": "图论资料", "ocr": False},
                    {"page": 2, "text": "最短路径", "ocr": True},
                ]
            },
            "b.png": {"text": "图像识别文字"},
            "c.docx": {"text": "文档识别文字"},
            "bad.pdf": ValueError("encrypted"),
        }
    )
    service.memory.documents.extractor = fake
    for name in ("a.pdf", "b.png", "c.docx", "bad.pdf", "unknown.zip"):
        (service.memory.root / "知识库/课程" / name).write_bytes(name.encode())
    service.memory.reindex()
    assert len(fake.calls) == 4
    assert service.memory.search("最短路径")[0]["path"] == "知识库/课程/a.pdf"
    assert service.memory.search("图像识别文字") and service.memory.search("文档识别文字")
    pdf = service.memory.read("知识库/课程/a.pdf")
    assert pdf["extracted"] is True and "[第 2 页]" in pdf["content"]
    service.memory.reindex()
    assert all(fake.calls.count(name) == 1 for name in ("a.pdf", "b.png", "c.docx"))
    assert fake.calls.count("bad.pdf") == 2
    assert service.knowledge.source("知识库/课程/bad.pdf")["status"] == "unreadable"
    assert service.knowledge.source("知识库/课程/unknown.zip")["error"] == "不支持这种格式"
    assert not list(service.memory.root.rglob("*.json"))
    (service.memory.root / "知识库/课程/a.pdf").write_bytes(b"changed")
    service.memory.reindex()
    assert fake.calls.count("a.pdf") == 2
    fake.results["big.pdf"] = {"pages": [{"page": 1, "text": "字" * MAX_TEXT}]}
    big = put(service, "知识库/课程/big.pdf")
    extracted = service.memory.documents.read(big)
    assert extracted["truncated"] and len(extracted["content"].encode()) <= MAX_TEXT
    assert service.knowledge.file("知识库/课程/big.pdf")["truncated"]


@pytest.mark.parametrize(
    "failure", [FileNotFoundError(), subprocess.TimeoutExpired("fake", 120), RuntimeError()]
)
@pytest.mark.parametrize("legacy_cache", [False, True])
def test_reindex_retries_failed_extraction_without_changing_file(service, failure, legacy_cache):
    path = put(service, "知识库/课程/retry.pdf", "synthetic pdf")
    documents = service.memory.documents
    fake = FakeExtractor({path.name: failure})
    documents.extractor = fake
    cache = documents.directory / f"{sha256(path)}.json"
    if legacy_cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"content": "", "error": "提取器不可用"}))

    assert documents.read(path)["error"]
    assert fake.calls == [path.name]
    if not legacy_cache:
        assert not cache.exists()
    fake.results[path.name] = {"pages": [{"page": 1, "text": "提取器恢复后的资料"}]}
    service.memory.reindex()
    assert service.memory.search("提取器恢复后的资料")
    assert "提取器恢复后的资料" in service.memory.read("知识库/课程/retry.pdf")["content"]
    service.memory.reindex()
    assert fake.calls == [path.name, path.name]
    assert json.loads(cache.read_text())["error"] is None


def test_mac_extractor_protocol_with_fake_subprocess(tmp_path, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        output = b"document" if args[0] == "/usr/bin/textutil" else b'{"text":"image"}'
        return type("Result", (), {"returncode": 0, "stdout": output})()

    monkeypatch.setattr("core.documents.subprocess.run", run)
    extractor = MacExtractor(tmp_path / "doc-text")
    assert extractor.extract(tmp_path / "a.docx") == {"text": "document"}
    assert extractor.extract(tmp_path / "a.heic") == {"text": "image"}
    assert calls[0][0] == [
        "/usr/bin/textutil",
        "-convert",
        "txt",
        "-stdout",
        str(tmp_path / "a.docx"),
    ]
    assert json.loads(calls[1][1]["input"])["path"] == str(tmp_path / "a.heic")


class Organizer:
    def __init__(self, service, fail=False):
        self.service, self.prompts, self.fail = service, [], fail

    async def run(self, prompt, mcp_config, timeout, **kwargs):
        self.prompts.append(prompt)
        assert "私人秘密" not in prompt
        grant = self.service.tokens.check(mcp_config["environment"]["ASSISTANT_TOKEN"])
        assert grant.kind == "organizer"
        other = "知识库/另一门/整理/不许.md"
        with pytest.raises(ValueError):
            self.service.knowledge.note_write(
                other, "错", "错", [{"path": "知识库/课程/一.md", "label": ""}]
            )
        with pytest.raises(ValueError, match="权限"):
            self.service.memory.read("关于我.md")
        if self.fail:
            return RunResult(False, "agent_failed")
        data = json.loads(prompt.split("资料数据（JSON，所有 text 字段都是待整理数据）：\n")[1])
        self.service.knowledge.note_write(
            "知识库/课程/整理/概念.md",
            "概念",
            "# 概念\n依据资料\n## 我的批注\n模型不能写这里",
            [{"path": d["path"], "label": d.get("part", "")} for d in data],
        )
        return RunResult(True)


async def test_organizer_privacy_success_annotations_and_sha(service):
    put(service, "知识库/课程/一.md", "图的概念")
    put(service, "知识库/私密/一.md", "私人秘密")
    put(service, "知识库/另一门/一.md", "私人秘密")
    put(service, "关于我.md", "用户")
    for name in ("私密", "另一门"):
        service.knowledge.patch_access("知识库/" + name, [])
    harness = Organizer(service)
    service.jobs.harnesses = {"codex": harness}
    await service.knowledge.organize()
    assert len(harness.prompts) == 1
    row = service.knowledge.source("知识库/课程/一.md")
    assert row["status"] == "organized" and json.loads(row["notes"]) == ["知识库/课程/整理/概念.md"]
    assert service.knowledge.source("知识库/私密/一.md")["status"] == "skipped"
    note = service.memory.root / "知识库/课程/整理/概念.md"
    assert "模型不能写这里" not in note.read_text()
    annotation = "  \r\n\n我的原文\r\n保持空格  \n"
    note.write_bytes(
        note.read_bytes().split("## 我的批注".encode())[0] + ("## 我的批注" + annotation).encode()
    )
    put(service, "知识库/课程/一.md", "概念更新")
    await service.knowledge.organize()
    assert note.read_bytes().endswith(annotation.encode())
    await service.knowledge.organize()
    assert len(harness.prompts) == 2
    assert service.memory.search("依据资料")[0]["path"].endswith("概念.md")
    with as_reader(MAIN), pytest.raises(ValueError):
        service.knowledge.note_write("知识库/课程/整理/概念.md", "标题", "正文", [])


async def test_organizer_failure_remains_pending_and_long_sources_resume(service):
    put(service, "知识库/课程/一.md", "一" * 60_010)
    failed = Organizer(service, fail=True)
    service.jobs.harnesses = {"codex": failed}
    with pytest.raises(RuntimeError):
        await service.knowledge.organize()
    assert service.knowledge.source("知识库/课程/一.md")["status"] == "pending"
    assert service.knowledge.source("知识库/课程/一.md")["error"]
    good = Organizer(service)
    service.jobs.harnesses = {"codex": good}
    # Over 60,000 characters: two batches, both in the same run.
    await service.knowledge.organize()
    assert service.knowledge.source("知识库/课程/一.md")["status"] == "organized"
    assert len(good.prompts) == 2
    await service.knowledge.organize()
    assert len(good.prompts) == 2, "nothing left to send"


def test_knowledge_http_contract_and_errors(service, tmp_path):
    put(service, "Bots/作业/记忆.md", "用户偏好")
    put(service, "关于我.md", "用户介绍")
    put(service, "知识库/散.md", "散文件")
    app_token, _ = service.tokens.create("iPhone", ["app"], reader="Codex")
    with client_for(service) as client:
        client.headers["Authorization"] = "Bearer " + app_token
        assert client.get("/api/v1/knowledge").json()["organize"] == {
            "last_run_at": None,
            "status": None,
            "next_run_at": None,
        }
        created = client.post(
            "/api/v1/knowledge/topics", json={"name": "课程", "readers": ["作业", "Codex"]}
        )
        assert created.status_code == 201 and created.json()["files"] == 0
        assert client.post("/api/v1/knowledge/topics", json={"name": "课程"}).status_code == 409
        for name in ("", ".隐藏", "整理", "a/b", "a\\b", "a:b", "字" * 41):
            assert client.post("/api/v1/knowledge/topics", json={"name": name}).status_code == 422
        assert (
            client.post(
                "/api/v1/knowledge/topics", json={"name": "新", "readers": ["陌生"]}
            ).status_code
            == 422
        )
        assert (
            client.patch("/api/v1/knowledge", json={"default_readers": ["陌生"]}).status_code == 422
        )
        for agents in (["作业"], [""], ["a" * 41], ["Codex", "Codex"]):
            assert client.patch("/api/v1/knowledge", json={"agents": agents}).status_code == 422
        assert (
            client.patch(
                "/api/v1/knowledge",
                json={
                    "agents": ["Claude Code", "Codex", "Gemini CLI"],
                    "default_readers": ["Gemini CLI"],
                },
            ).status_code
            == 200
        )
        assert (
            client.patch(
                "/api/v1/knowledge/access", json={"path": "missing", "readers": []}
            ).status_code
            == 404
        )
        assert client.patch(
            "/api/v1/knowledge/access", json={"path": "Bots", "readers": []}
        ).json()["locked"]
        assert (
            client.patch(
                "/api/v1/knowledge/access", json={"path": "知识库/课程", "readers": ["陌生"]}
            ).status_code
            == 422
        )
        upload = "/api/v1/knowledge/files"
        location = "知识库/课程/原文.txt"
        result = client.post(
            upload,
            params={"path": location},
            content="最短路径资料".encode(),
            headers={"Content-Type": "text/plain"},
        )
        assert result.status_code == 201 and result.json()["status"] == "pending"
        assert (
            client.post(upload, params={"path": location}, content=b"overwrite").status_code == 409
        )
        assert service.memory.root.joinpath(location).read_text() == "最短路径资料"
        assert (
            client.post(upload, params={"path": "知识库/课程/整理/新.md"}, content=b"x").status_code
            == 422
        )
        assert (
            client.post(upload, params={"path": "知识库/missing/a.md"}, content=b"x").status_code
            == 404
        )
        assert (
            client.post(upload, params={"path": "知识库/课程/a.zip"}, content=b"x").status_code
            == 422
        )
        listed = client.get(upload, params={"path": "知识库/课程"}).json()
        assert listed["area"] == "知识库/课程" and listed["entries"][0]["kind"] == "source"
        assert client.get(upload, params={"path": "知识库"}).json()["area"] is None
        assert client.get(upload, params={"path": "."}).json()["entries"][0]["kind"] == "file"
        file = client.get("/api/v1/knowledge/file", params={"path": location}).json()
        assert file["content"] == "最短路径资料" and file["notes"] == [] and file["sources"] == []
        assert file["organized_by"] is None and file["extracted"] is False
        raw = client.get("/api/v1/knowledge/raw", params={"path": location})
        assert raw.content == "最短路径资料".encode() and raw.headers["content-type"].startswith(
            "text/plain"
        )
        found = client.get("/api/v1/knowledge/search", params={"q": "最短路径"}).json()["results"]
        assert found[0]["path"] == location
        for params in ({"q": ""}, {"q": "x", "limit": 51}, {"q": "x" * 501}):
            assert client.get("/api/v1/knowledge/search", params=params).status_code == 422
        assert client.get("/api/v1/knowledge/raw", params={"path": "关于我.md"}).status_code == 422
        outside = tmp_path / "outside.md"
        outside.write_text("secret")
        (service.memory.root / "知识库/课程/link.md").symlink_to(outside)
        (service.memory.root / "alias").symlink_to(
            service.memory.root / "知识库/课程", target_is_directory=True
        )
        for endpoint in ("files", "file", "raw"):
            for path in ("../outside.md", "/etc/passwd", "知识库/课程/link.md", "alias/原文.txt"):
                assert (
                    client.get("/api/v1/knowledge/" + endpoint, params={"path": path}).status_code
                    == 422
                )
            assert (
                client.get(
                    "/api/v1/knowledge/" + endpoint, params={"path": "知识库/missing"}
                ).status_code
                == 404
            )
        assert (
            client.post(
                upload, params={"path": "知识库/课程/link.md"}, content=b"overwrite"
            ).status_code
            == 422
        )
        assert outside.read_text() == "secret"
        schema = client.get("/openapi.json").json()
        assert "requestBody" in schema["paths"]["/api/v1/knowledge/files"]["post"]
        assert schema["components"]["schemas"]["Area"]["required"] == list(
            schema["components"]["schemas"]["Area"]["properties"]
        )


def test_token_cli_reader(service, capsys):
    base = ["--data-dir", str(service.settings.data_dir)]
    assert (
        cli.main([*base, "create", "Claude Mac", "--scope", "mcp", "--reader", "Claude Code"]) == 0
    )
    assert "a24_" in capsys.readouterr().out
    assert cli.main([*base, "create", "旧令牌", "--scope", "mcp"]) == 0
    capsys.readouterr()
    assert cli.main([*base, "list"]) == 0
    output = capsys.readouterr().out
    assert "Claude Code" in output and "未限定" in output
    assert cli.main([*base, "create", "未知", "--scope", "mcp", "--reader", "陌生"]) == 1


def test_mcp_http_scopes_apply_to_search_read_and_note_write(service):
    put(service, "知识库/课程/公开.md", "图论共同内容")
    put(service, "知识库/私人/隐藏.md", "图论共同内容：绝密")
    service.knowledge.patch_access("知识库/课程", ["Codex"])
    service.knowledge.patch_access("知识库/私人", [])
    credential, _ = service.tokens.create("Codex MCP", ["mcp"], reader="Codex")
    temporary = service.tokens.temporary(bot("作业"), 30)
    legacy, _ = service.tokens.create("legacy MCP", ["mcp"])
    with client_for(service) as client:

        def invoke(token, name, arguments):
            response = client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer " + token,
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            assert response.status_code == 200
            return response.json()["result"]

        result = invoke(credential, "memory_search", {"query": "图论", "limit": 1})
        assert "公开.md" in str(result) and "隐藏.md" not in str(result)
        for token in (credential, temporary):
            denied = invoke(token, "memory_read", {"path": "知识库/私人/隐藏.md"})
            assert denied["isError"] and "绝密" not in str(denied)
            denied = invoke(
                token,
                "knowledge_note_write",
                {"path": "知识库/课程/整理/n.md", "title": "n", "content": "n", "sources": []},
            )
            assert denied["isError"]
        assert "绝密" in str(invoke(legacy, "memory_read", {"path": "知识库/私人/隐藏.md"}))
        service.tokens.discard(temporary)
        assert (
            client.get(
                "/internal/tools", headers={"Authorization": "Bearer " + temporary}
            ).status_code
            == 401
        )


def test_app_note_metadata_contents_raw_types_and_limits(service):
    service.knowledge.topic("课程")
    for suffix in ("pdf", "png", "jpg", "jpeg", "heic", "docx"):
        (service.memory.root / f"知识库/课程/a.{suffix}").write_bytes(suffix.encode())
    put(service, "知识库/课程/original.md", "资料")
    grant = Grant(
        None, "organizer", frozenset({"mcp"}), kind="organizer", area="知识库/课程", harness="codex"
    )
    with as_reader(grant):
        service.knowledge.note_write(
            "知识库/课程/整理/n.md",
            "有标题",
            "# 笔记正文",
            [{"path": "知识库/课程/original.md", "label": "章节"}],
        )
        user = put(
            service, "知识库/课程/整理/user.md", "---\norganized_by: codex\n---\n用户自己的文件"
        )
        with pytest.raises(ValueError, match="用户文件不能覆盖"):
            service.knowledge.note_write(
                "知识库/课程/整理/user.md",
                "覆盖",
                "不能",
                [{"path": "知识库/课程/original.md", "label": ""}],
            )
        assert "用户自己的文件" in user.read_text()
        with pytest.raises(ValueError, match="来源"):
            service.knowledge.note_write(
                "知识库/课程/整理/other.md",
                "标题",
                "正文",
                [{"path": "知识库/课程/整理/n.md", "label": ""}],
            )
    with client_for(service) as client:
        note = client.get("/api/v1/knowledge/file", params={"path": "知识库/课程/整理/n.md"}).json()
        assert note["title"] == "有标题" and note["organized_by"] == "codex"
        assert note["content"].startswith("\n# 笔记正文") and "---" not in note["content"]
        assert note["sources"][0]["label"] == "章节" and note["notes"] == []
        assert datetime.fromisoformat(note["organized_at"]).tzinfo is not None
        types = {
            "pdf": "application/pdf",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "heic": "image/heic",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        for suffix, content_type in types.items():
            response = client.get(
                "/api/v1/knowledge/raw", params={"path": f"知识库/课程/a.{suffix}"}
            )
            assert response.status_code == 200 and response.content == suffix.encode()
            assert response.headers["content-type"] == content_type
        huge = service.memory.root / "知识库/课程/too-big.pdf"
        with huge.open("wb") as stream:
            stream.truncate(500 * 1024 * 1024 + 1)
        assert (
            client.get(
                "/api/v1/knowledge/raw", params={"path": "知识库/课程/too-big.pdf"}
            ).status_code
            == 422
        )
        invalid = client.get(
            "/api/v1/knowledge/file", params={"path": "知识库/课程/too-big.pdf"}
        ).json()
        assert invalid["status"] == "unreadable" and invalid["content"] == ""
        response = client.post(
            "/api/v1/knowledge/files",
            params={"path": "知识库/课程/large.txt"},
            content=b"a" * 2_100_000,
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 201
        large = client.get(
            "/api/v1/knowledge/file", params={"path": "知识库/课程/large.txt"}
        ).json()
        assert large["truncated"] and len(large["content"]) == 200_000
        response = client.post(
            "/api/v1/knowledge/files",
            params={"path": "知识库/课程/over.txt"},
            content=b"a" * (50 * 1024 * 1024 + 1),
        )
        assert (
            response.status_code == 413
            and not (service.memory.root / "知识库/课程/over.txt").exists()
        )


async def test_empty_readers_are_rechecked_before_fallback(service):
    put(service, "知识库/课程/一.md", "绝不传给下一个模型")

    class Revoke:
        async def run(self, prompt, config, timeout, **kwargs):
            service.knowledge.patch_access("知识库/课程", [])
            return RunResult(False, "agent_failed")

    class MustNotRun:
        async def run(self, *args, **kwargs):
            pytest.fail("revoked topic reached fallback")

    service.jobs.harnesses = {"codex": Revoke(), "claude_code": MustNotRun()}
    with pytest.raises(RuntimeError):
        await service.knowledge.organize()
    assert service.knowledge.source("知识库/课程/一.md")["status"] == "skipped"


def test_database_migration_retains_legacy_tokens_and_user_files(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE device_tokens(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,hint TEXT NOT NULL,scopes TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT,last_used_at TEXT,revoked_at TEXT)"
        )
        conn.execute("PRAGMA user_version=3")
    db = Database(path)
    assert db.rows("PRAGMA user_version")[0]["user_version"] == 5
    assert "reader" in {r["name"] for r in db.rows("PRAGMA table_info(device_tokens)")}
    token, _ = DeviceTokens(db).create("legacy", ["mcp"])
    assert DeviceTokens(Database(path)).check(token).reader is None


async def test_job_registration_settings_and_whole_run_status(service):
    await service.start()
    try:
        assert "knowledge_organize" in service.jobs.callbacks
        assert service.store.settings.knowledge_organize_cron == "0 3 * * *"
        await service.jobs.submit("knowledge_organize")
        summary = service.knowledge.overview()["organize"]
        assert summary["status"] == "succeeded" and summary["last_run_at"] is not None
        # An individual attempt cannot hide a failed/running overall task.
        overall = service.db.start_run("knowledge_organize")
        attempt = service.db.start_run("knowledge_organize", "codex")
        service.db.finish_run(attempt)
        assert service.knowledge.overview()["organize"]["status"] == "running"
        service.db.finish_run(overall, "failed", "knowledge_organize_failed")
        assert service.knowledge.overview()["organize"]["status"] == "failed"
    finally:
        await service.close()


def test_private_upload_is_pending_then_skipped_and_preserves_completed_receipts(service):
    service.knowledge.topic("私人", [])
    entry = service.knowledge.upload("知识库/私人/a.txt", b"private")
    assert entry["status"] == "pending"
    assert service.knowledge.file(entry["path"])["status"] == "skipped"
    service.knowledge.patch_access("知识库/私人", ["Codex"])
    timestamp = datetime.now(UTC).isoformat()
    service.db.execute(
        "UPDATE knowledge_sources SET status='organized',organized_at=?,notes=? WHERE path=?",
        (timestamp, json.dumps(["知识库/私人/整理/a.md"]), entry["path"]),
    )
    service.knowledge.patch_access("知识库/私人", [])
    assert service.knowledge.source(entry["path"])["status"] == "skipped"
    service.knowledge.patch_access("知识库/私人", ["Codex"])
    source = service.knowledge.source(entry["path"])
    assert source["status"] == "organized" and source["organized_at"] == timestamp
    assert json.loads(source["notes"]) == ["知识库/私人/整理/a.md"]


def test_knowledge_schedule_patch_and_run_http(service):
    with client_for(service) as client:
        result = client.patch("/api/v1/settings", json={"knowledge_organize_cron": "30 2 * * *"})
        assert (
            result.status_code == 200 and result.json()["knowledge_organize_cron"] == "30 2 * * *"
        )
        assert (
            client.patch(
                "/api/v1/settings", json={"knowledge_organize_cron": "invalid"}
            ).status_code
            == 422
        )
        assert client.post("/api/v1/jobs/knowledge_organize/run").status_code == 202


def test_hidden_files_are_not_knowledge(service):
    put(service, "知识库/课程/一.md", "一")
    put(service, "知识库/课程/.DS_Store", "x")
    put(service, "知识库/.DS_Store", "x")
    put(service, ".obsidian/app.md", "x")
    overview = service.knowledge.overview()
    assert overview["loose_files"] == 0
    assert ".obsidian" not in [a["path"] for a in overview["areas"]]
    names = [e["name"] for e in service.knowledge.files("知识库/课程")["entries"]]
    assert names == ["一.md"]


@pytest.mark.parametrize("extension", ["csv", "xlsx", "zip"])
def test_private_only_statement_upload_and_skipped_unknown_formats(service, extension):
    service.knowledge.topic("财务", readers=[])
    entry = service.knowledge.upload(f"知识库/财务/test.{extension}", b"synthetic")
    assert entry["status"] == "skipped" and entry["error"] is None
    service.knowledge.topic("公开")
    with pytest.raises(ValueError, match="没有读者"):
        service.knowledge.upload(f"知识库/公开/test.{extension}", b"synthetic")
    service.knowledge.sync()
    assert service.knowledge.source(entry["path"])["status"] == "skipped"
    assert service.knowledge.source(entry["path"])["error"] is None
    service.knowledge.patch_access("知识库/财务", ["知识库"])
    assert service.knowledge.source(entry["path"])["status"] == "unreadable"
    service.knowledge.patch_access("知识库/财务", [])
    assert service.knowledge.source(entry["path"])["error"] is None
