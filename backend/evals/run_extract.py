"""Run prompts/extract_todos.md over the dataset with one agent harness, then score it.

    uv run python -m evals.run_extract --harness claude_code
    uv run python -m evals.run_extract --harness codex --mock     # no model, checks the flow

A throwaway core (temporary database, fake Apple sync, no scheduler) is started on a free
local port, the dataset is ingested as qq_screen / feishu messages, the user profile is
written to memory, and the chosen CLI is started exactly as the scheduled job would start
it: same prompt template, same stdio MCP bridge. Only the clock in the prompt is fixed to
the dataset's reference time, so relative dates are reproducible. Results go to
.cache/evals/<time>-<harness>/ (predictions.json, report.json, report.md).

A real run uses the CLI's own login and quota and sends the synthetic messages to the
model provider. The --mock flag runs evals.mock_agent instead.
"""

import argparse
import asyncio
import json
import logging
import secrets
import socket
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from core.api import create_app
from core.apple import FakeAppleBridge
from core.config import Settings
from core.models import MessageInput, RuntimeSettings
from core.service import Service
from core.task_grants import extraction_grant
from evals.dataset import DEFAULT, load
from evals.score import render, score

BACKEND = Path(__file__).resolve().parents[1]
HARNESSES = ("claude_code", "codex")
EXECUTABLES = {"claude_code": "claude", "codex": "codex"}


@contextmanager
def mock_cli():
    """Use Python entry points on every platform, without shell wrappers."""
    with tempfile.TemporaryDirectory() as directory:
        executables = {}
        for kind in ("claude", "codex"):
            source = Path(directory) / f"{kind}.py"
            source.write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(BACKEND)!r})\n"
                "from evals.mock_agent import main\n"
                f"main({kind!r})\n",
                encoding="utf-8",
            )
            executables[kind] = str(source)
        yield executables


def ingest(service, samples):
    by_plugin = {}
    for sample in samples:
        by_plugin.setdefault(sample["plugin"], []).append(sample)
    for plugin, items in by_plugin.items():
        messages = [
            MessageInput(
                external_id=item["id"],
                chat_name=item["chat_name"],
                sender=item["sender"],
                sent_at=item["sent_at"],
                text=item["text"],
                priority=item.get("priority", False),
                outgoing=item["sender"] == "我",  # as the screen reader marks the user's own
            )
            for item in items
        ]
        service.store.ingest(plugin, messages, sorted({item["chat_name"] for item in items}))


def parse_batches(value):
    if value == "day":
        return value
    if not str(value).isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError("batches 必须是 day 或正整数")
    return int(value)


def message_batches(samples, batches, timezone):
    """Keep the legacy input order unless chronological batches were requested."""
    if batches is None:
        return [samples]
    batches = parse_batches(batches)
    ordered = sorted(samples, key=lambda sample: datetime.fromisoformat(sample["sent_at"]))
    if batches == "day":
        zone = ZoneInfo(timezone)
        return [
            list(items)
            for _, items in groupby(
                ordered,
                key=lambda sample: (
                    datetime.fromisoformat(sample["sent_at"]).astimezone(zone).date()
                ),
            )
        ]
    return [ordered[start : start + batches] for start in range(0, len(ordered), batches)]


def prompt_and_config(service, meta, local_now=None):
    """The same prompt and MCP wiring as core.jobs.Jobs.extract, with a fixed clock."""
    settings = service.store.settings
    now = datetime.fromisoformat(local_now or meta["reference_now"])
    template = (BACKEND / "prompts" / "extract_todos.md").read_text(encoding="utf-8")
    prompt = template.format(
        local_now=now.isoformat(),
        timezone=settings.timezone,
        confidence_threshold=settings.confidence_threshold,
    )
    mcp_config = {
        "stdio": {"command": sys.executable, "args": ["-m", "core.mcp_stdio"], "cwd": str(BACKEND)},
        "environment": {
            "ASSISTANT_TOKEN": service.tokens.temporary(
                extraction_grant(now.astimezone(ZoneInfo(settings.timezone)).date()),
                settings.agent_timeout + 30,
            ),
            "ASSISTANT_BASE_URL": service.settings.base_url,
        },
    }
    return prompt, mcp_config


@contextmanager
def record_todo_changes(service):
    """Audit persisted status changes in the disposable core, without recording content."""
    changes = []

    def snapshot():
        return {
            row["id"]: row for row in service.db.rows("SELECT id,status,source_refs FROM todos")
        }

    def wrap(method):
        async def recorded(*args, **kwargs):
            before = snapshot()
            try:
                return await method(*args, **kwargs)
            finally:
                after = snapshot()
                for identifier, row in after.items():
                    previous = before.get(identifier, {}).get("status")
                    if previous != row["status"]:
                        changes.append(
                            {
                                "source_refs": json.loads(row["source_refs"]),
                                "before_status": previous,
                                "after_status": row["status"],
                            }
                        )

        return recorded

    original_add, original_update = service.add_todo, service.update_todo
    service.add_todo, service.update_todo = wrap(original_add), wrap(original_update)
    try:
        yield changes
    finally:
        service.add_todo, service.update_todo = original_add, original_update


def predictions_from(service, changes=()):
    external = {
        row["id"]: row["external_id"]
        for row in service.db.rows("SELECT id, external_id FROM messages")
    }
    todos = []
    for row in service.db.rows("SELECT * FROM todos ORDER BY created_at, id"):
        todos.append(
            {
                "title": row["title"],
                "notes": row["notes"],
                "due_at": row["due_at"],
                "has_time": bool(row["has_time"]),
                "source_refs": [external.get(ref, ref) for ref in json.loads(row["source_refs"])],
                "status": row["status"],
                "confidence": row["confidence"],
            }
        )
    return {
        "todos": todos,
        "todo_changes": [
            {**change, "source_refs": [external.get(ref, ref) for ref in change["source_refs"]]}
            for change in changes
        ],
        "digests": service.store.digests(limit=200)["items"],
        "runs": service.store.runs(limit=50)["items"],
        "unprocessed": service.db.rows("SELECT count(*) AS n FROM messages WHERE processed=0")[0][
            "n"
        ],
    }


async def run(
    harness, out_dir, dataset_dir=DEFAULT, timeout=1200, rounds=3, mock=False, batches=None
):
    if harness not in HARNESSES:
        raise ValueError(f"harness must be one of {HARNESSES}")
    samples, meta = load(dataset_dir)
    if batches is not None:
        batches = parse_batches(batches)
    groups = message_batches(samples, batches, meta["timezone"])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    with tempfile.TemporaryDirectory() as state:
        settings = Settings(
            token=secrets.token_urlsafe(32),
            data_dir=Path(state),
            vault_dir=Path(state) / "vault",
            host="127.0.0.1",
            port=sock.getsockname()[1],
            scheduler_enabled=False,
            apple_mode="fake",
            _env_file=None,
        )
        service = Service(settings, apple_bridge=FakeAppleBridge(settings.data_dir / "fake-apple"))
        runtime = RuntimeSettings(
            timezone=meta["timezone"], harness_order=[harness], agent_timeout=min(timeout, 3600)
        )
        service.db.put("settings", runtime.model_dump())
        server = uvicorn.Server(
            uvicorn.Config(create_app(service=service), log_level="critical", access_log=False)
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        attempts = []
        batch_results = []
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.02)
            service.memory.write(meta["profile_path"], meta["profile"])
            agent = service.jobs.harnesses[harness]
            with (
                record_todo_changes(service) as changes,
                mock_cli() if mock else _nothing() as executables,
            ):
                if mock:
                    agent.executable = executables[EXECUTABLES[harness]]
                for index, group in enumerate(groups):
                    ingest(service, group)
                    local_now = (
                        datetime.fromisoformat(group[-1]["sent_at"])
                        .astimezone(ZoneInfo(meta["timezone"]))
                        .isoformat()
                        if batches is not None
                        else meta["reference_now"]
                    )
                    batch_attempts = []
                    for _ in range(rounds):
                        if not service.store.unprocessed(limit=1):
                            break
                        prompt, mcp_config = prompt_and_config(service, meta, local_now)
                        try:
                            result = await agent.run(prompt, mcp_config, timeout)
                        finally:
                            service.tokens.discard(mcp_config["environment"]["ASSISTANT_TOKEN"])
                        attempt = {"success": result.success, "error": result.error}
                        attempts.append(attempt)
                        batch_attempts.append(attempt)
                        if not result.success and result.error in {
                            "cli_unavailable",
                            "authentication_required",
                            "quota_exhausted",
                        }:
                            break
                    remaining = service.db.rows(
                        "SELECT count(*) AS n FROM messages WHERE processed=0"
                    )[0]["n"]
                    batch_results.append(
                        {
                            "index": index + 1,
                            "local_now": local_now,
                            "sample_ids": [sample["id"] for sample in group],
                            "attempts": batch_attempts,
                            "unprocessed": remaining,
                        }
                    )
                    if remaining:
                        break
            predictions = {
                "harness": harness,
                "mock": mock,
                "attempts": attempts,
                **predictions_from(service, changes),
            }
            if batches is not None:
                predictions.update(
                    batch_mode=batches, planned_batches=len(groups), batches=batch_results
                )
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 30)
            sock.close()
    report = score(samples, meta, predictions)
    report["attempts"] = attempts
    (out_dir / "predictions.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render(report), encoding="utf-8")
    return report


@contextmanager
def _nothing():
    yield


def main(argv=None):
    parser = argparse.ArgumentParser(description="用 claude_code 或 codex 跑一遍待办提炼并打分")
    parser.add_argument("--harness", choices=HARNESSES, required=True)
    parser.add_argument("--mock", action="store_true", help="用假 CLI 验证流程，不调用模型")
    parser.add_argument("--dataset", type=Path, default=DEFAULT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=int, default=1200, help="每轮 CLI 超时秒数")
    parser.add_argument("--rounds", type=int, default=3, help="仍有未处理消息时，每批最多运行几轮")
    parser.add_argument(
        "--batches", type=parse_batches, metavar="day|N", help="按本地日期或每批 N 条分批投放"
    )
    args = parser.parse_args(argv)
    for name in ("mcp", "httpx", "httpcore", "apscheduler", "uvicorn"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-mock" if args.mock else ""
    out = args.out or BACKEND / ".cache" / "evals" / f"{stamp}-{args.harness}{suffix}"
    if not args.mock:
        print(
            f"将用本机已登录的 {EXECUTABLES[args.harness]} 处理 {args.dataset.name} 的合成消息，"
            "会消耗额度。",
            file=sys.stderr,
        )
    report = asyncio.run(
        run(args.harness, out, args.dataset, args.timeout, args.rounds, args.mock, args.batches)
    )
    print(render(report))
    print(f"完整结果：{out}", file=sys.stderr)
    failed = [a for a in report["attempts"] if not a["success"]]
    if failed and report["unprocessed"]:
        raise SystemExit(f"Agent 运行失败：{failed[-1]['error']}")
    if args.batches is not None and report["unprocessed"]:
        raise SystemExit("当前批次仍有未处理消息，后续批次未投放；详见批次记录")


if __name__ == "__main__":
    main()
