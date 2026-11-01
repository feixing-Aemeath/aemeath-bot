"""Offline acceptance demo with synthetic text and scripted Agent decisions only."""

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from core.apple import FakeAppleBridge
from core.config import Settings
from core.mcp_server import create_mcp
from core.models import MessageInput, TodoCreate
from core.service import Service
from harness.base import RunResult


async def run_demo(directory: Path):
    service = Service(
        Settings(
            token="offline-demo-token-000000000000000000",
            data_dir=directory,
            scheduler_enabled=False,
            _env_file=None,
        ),
        apple_bridge=FakeAppleBridge(directory / "fake-apple"),
    )
    mcp = create_mcp(service)
    await service.start()
    try:
        for source in ("qq_screen", "feishu"):
            service.store.ingest(
                source,
                [
                    MessageInput(
                        external_id="synthetic-1",
                        chat_name="演示群",
                        sender="演示老师",
                        sent_at=datetime.now(UTC),
                        text="请于2026年10月12日18点前提交报名表。",
                    ),
                    MessageInput(
                        external_id="synthetic-2",
                        chat_name="演示群",
                        sender="演示老师",
                        sent_at=datetime.now(UTC),
                        text="下周放假三天。",
                    ),
                ],
                ["演示群"],
            )

        class QuotaFake:
            async def run(self, prompt, mcp_config, timeout):
                return RunResult(False, "quota_exhausted")

        class ScriptedFake:
            async def run(self, prompt, mcp_config, timeout):
                messages = service.store.unprocessed()
                for message in messages:
                    if "报名表" in message["text"]:
                        todo = TodoCreate(
                            title="提交报名表",
                            due_at="2026-10-12T18:00:00+08:00",
                            has_time=True,
                            source_plugin=message["plugin"],
                            source_refs=[message["id"]],
                            source_excerpt=message["text"],
                            confidence=0.95,
                        )
                        await mcp.call_tool("todos_add", {"todo": todo.model_dump(mode="json")})
                    else:
                        await mcp.call_tool(
                            "digest_write",
                            {
                                "date": "2026-10-11",
                                "plugin": message["plugin"],
                                "summary": "下周放假三天，无需建立待办。",
                            },
                        )
                await mcp.call_tool("messages_mark_processed", {"ids": [m["id"] for m in messages]})
                return RunResult(True)

        service.jobs.harnesses = {"claude_code": QuotaFake(), "codex": ScriptedFake()}
        await service.jobs.extract()
        todos = service.store.todos()["items"]
        reminders = json.loads(
            (directory / "fake-apple" / "reminders.json").read_text(encoding="utf-8")
        )
        report = {
            "mode": "synthetic offline demo; no real accounts or models",
            "todos": len(todos),
            "merged_sources": len(todos[0]["source_refs"]),
            "reminders": len(reminders),
            "digests": service.store.digests()["total"],
            "unprocessed": len(service.store.unprocessed()),
            "fallback_verified": any(
                r["error"] == "quota_exhausted" for r in service.store.runs()["items"]
            ),
        }
        assert report["todos"] == report["reminders"] == 1
        assert report["merged_sources"] == 2 and report["unprocessed"] == 0
        return report
    finally:
        await service.close()


def main():
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(asyncio.run(run_demo(Path(directory))), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
