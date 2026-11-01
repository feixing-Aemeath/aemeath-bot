"""Stand-in for the `claude` / `codex` CLI, used to check the evaluation flow without a model.

The real harness starts it with the real command line. It reads the MCP server from that
command line (`--mcp-config` for Claude Code, `-c mcp_servers.assistant.*` for Codex),
connects through the same stdio bridge a real agent uses and applies naive keyword rules.
Its scores say nothing about model quality.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

KEYWORDS = ("截止", "提交", "交", "填写", "完成", "报名", "领取", "参加")


def server_from_args(kind, args):
    """(command, args, cwd, environment) of the assistant MCP server, as each CLI reads it.

    Claude Code hands its own environment to stdio servers; Codex forwards only the
    variables named in `env_vars`.
    """
    if kind == "claude":
        config = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["assistant"]
        forwarded = [key for key in os.environ if key.startswith("ASSISTANT_")]
        return config["command"], config.get("args", []), config.get("cwd"), forwarded
    options = {}
    for index, arg in enumerate(args[:-1]):
        if arg == "-c":
            key, _, value = args[index + 1].partition("=")
            options[key] = json.loads(value)
    prefix = "mcp_servers.assistant."
    return (
        options[prefix + "command"],
        options.get(prefix + "args", []),
        options.get(prefix + "cwd"),
        options.get(prefix + "env_vars", []),
    )


def payload(result):
    if result.isError:
        raise RuntimeError("tool_failed")
    data = result.structuredContent
    if isinstance(data, dict) and set(data) == {"result"}:
        return data["result"]
    if data is not None:
        return data
    return [json.loads(item.text) for item in result.content if getattr(item, "text", None)]


def naive_due(text, sent_at):
    """Only explicit "10月16日" (+ "17:00" / "晚上8点"); relative dates are ignored."""
    day = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if not day:
        return None, False
    month, date = int(day[1]), int(day[2])
    clock = re.search(r"(\d{1,2})[:：](\d{2})", text[day.end() :])
    hour, minute = (int(clock[1]), int(clock[2])) if clock else (None, 0)
    if hour is None:
        spoken = re.search(r"(上午|下午|晚上)?(\d{1,2})点", text[day.end() :])
        if spoken:
            hour = int(spoken[2]) + (
                12 if spoken[1] in {"下午", "晚上"} and int(spoken[2]) < 12 else 0
            )
    if hour is not None and hour > 23:
        hour, minute = 23, 59
    moment = sent_at.replace(month=month, day=date, hour=hour or 0, minute=minute)
    return moment, hour is not None


async def work(command, args, cwd, forwarded, local_now, zone):
    environment = {**get_default_environment(), **{k: os.environ[k] for k in forwarded}}
    params = StdioServerParameters(command=command, args=args, cwd=cwd, env=environment)
    processed = 0
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        while True:
            batch = payload(await session.call_tool("messages_get_unprocessed", {"limit": 100}))
            if not batch:
                break
            notices = {}
            for message in batch:
                text = message["text"]
                if message["sender"] in {"我", "林小满"} or not any(k in text for k in KEYWORDS):
                    notices.setdefault(message["plugin"], []).append(text[:30])
                    continue
                sent_at = datetime.fromisoformat(message["sent_at"]).astimezone(zone)
                due, has_time = naive_due(text, sent_at)
                todo = {
                    "title": re.sub(r"@\S+\s*", "", text).strip()[:20] or text[:20],
                    "due_at": due.isoformat() if due else None,
                    "has_time": has_time,
                    "source_plugin": message["plugin"],
                    "source_refs": [message["id"]],
                    "source_excerpt": text[:40],
                    "confidence": 0.9,
                }
                payload(await session.call_tool("todos_add", {"todo": todo}))
            for plugin, texts in notices.items():
                summary = f"（mock）{len(texts)} 条非待办消息：" + "；".join(texts[:3])
                arguments = {"date": local_now[:10], "plugin": plugin, "summary": summary}
                payload(await session.call_tool("digest_write", arguments))
            ids = [message["id"] for message in batch]
            payload(await session.call_tool("messages_mark_processed", {"ids": ids}))
            processed += len(ids)
    return processed


def main(kind):
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    now = re.search(r"当前本地时间：(\S+?)。", prompt)
    zone = re.search(r"时区：(\S+?)。", prompt)
    if not now or not zone or "messages_get_unprocessed" not in prompt:
        print(json.dumps({"type": "error", "is_error": True, "message": "unexpected prompt"}))
        raise SystemExit(1)
    processed = asyncio.run(work(*server_from_args(kind, args), now[1], ZoneInfo(zone[1])))
    if kind == "claude":
        print(json.dumps({"type": "result", "is_error": False, "result": f"mock {processed}"}))
    else:
        print(json.dumps({"type": "thread.started"}))
        print(json.dumps({"type": "turn.completed"}))


if __name__ == "__main__":
    main(sys.argv.pop(1))
