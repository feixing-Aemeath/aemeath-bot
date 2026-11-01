import asyncio
import json
import time

from core import helper_process
from core.platforms import find_command
from harness.base import CLIHarness, Usage, count

MODELS_TTL = 3600  # seconds a model list read from Codex is kept


async def app_server_models(executable, timeout=20):
    """model/list from a `codex app-server` started for this one question: the models this
    account can use, as [{"id", "name", "default"}]."""
    process = await helper_process.start(
        [executable, "app-server"],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1 << 22,
    )

    async def send(*messages):
        process.stdin.write(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
        await process.stdin.drain()

    async def answer(request_id):
        while line := await process.stdout.readline():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message.get("result")
        return None

    async def exchange():
        client = {"name": "aemeath_bot", "version": "0.1.0"}
        await send({"id": 1, "method": "initialize", "params": {"clientInfo": client}})
        await answer(1)
        await send({"method": "initialized"}, {"id": 2, "method": "model/list", "params": {}})
        return await answer(2)

    try:
        result = await asyncio.wait_for(exchange(), timeout)
    finally:
        await helper_process.stop(process)
        helper_process.close_job(process)
    found = []
    for item in (result or {}).get("data") or []:
        model = item.get("model") or item.get("id") if isinstance(item, dict) else None
        if isinstance(model, str) and model and not item.get("hidden"):
            name = item.get("displayName") or model
            found.append({"id": model, "name": str(name), "default": bool(item.get("isDefault"))})
    return found


class CodexHarness(CLIHarness):
    executable = "codex"

    def __init__(self, workspace):
        super().__init__(workspace)
        self.known_models, self.models_read_at, self.reading = [], None, None

    def models(self):
        """The models last read from Codex; reads them again in the background when they are
        older than an hour (the first answer is empty)."""
        stale = self.models_read_at is None or time.monotonic() - self.models_read_at > MODELS_TTL
        if stale and (self.reading is None or self.reading.done()) and self.available():
            try:
                self.reading = asyncio.get_running_loop().create_task(self.read_models())
            except RuntimeError:
                pass  # no event loop: nothing to read with
        return [dict(model) for model in self.known_models]

    async def read_models(self):
        try:
            found = await app_server_models(find_command(self.executable))
        except Exception:
            found = None
        self.models_read_at = time.monotonic()
        if found:
            self.known_models = found

    def command(self, mcp_config, model=None):
        stdio = mcp_config["stdio"]
        options = {
            "mcp_servers.assistant.command": stdio["command"],
            "mcp_servers.assistant.args": stdio["args"],
            "mcp_servers.assistant.cwd": stdio["cwd"],
            "mcp_servers.assistant.env_vars": ["ASSISTANT_TOKEN", "ASSISTANT_BASE_URL"],
            "mcp_servers.assistant.required": True,
            # "approve": run the assistant's tools without asking. "auto" still asks for some,
            # and `codex exec` has nobody to ask, so the call fails ("approval policy is never").
            "mcp_servers.assistant.default_tools_approval_mode": "approve",
            "features.shell_tool": False,
            "web_search": "disabled",
        }
        result = [
            self.executable,
            "exec",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
        ]
        for key, value in options.items():
            result += ["-c", f"{key}={json.dumps(value)}"]
        if model:
            result += ["--model", model]
        return [*result, "-"]

    def usage(self, item):
        # One turn.completed event per turn; its input_tokens already include cached input.
        usage = item.get("usage")
        if item.get("type") != "turn.completed" or not isinstance(usage, dict):
            return None
        return Usage(
            count(usage.get("input_tokens")),
            count(usage.get("cached_input_tokens")),
            count(usage.get("output_tokens")),
        )
