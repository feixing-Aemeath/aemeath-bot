"""DeepSeek through its API (https://api-docs.deepseek.com), the third Agent next to the two
CLIs. There is no CLI to start: this harness is the agent loop itself. It sends the prompt
with the assistant's MCP tools as functions, calls the tools the model asks for through the
core (/internal/tools, as the stdio bridge does for the CLIs) and sends the results back,
until the model answers without a tool call. The model gets only the assistant's tools: no
shell, files or web.

The API key comes from ASSISTANT_DEEPSEEK_API_KEY (backend/.env) or the Keychain item
"AemeathBot DeepSeek API" (`security add-generic-password -a "$USER" -s "AemeathBot DeepSeek
API" -w`, which asks for the key without showing it). It is sent only to the DeepSeek API and
never logged.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from harness.base import RunResult, Usage, classify, count

BASE_URL = "https://api.deepseek.com"
KEYCHAIN_ITEM = "AemeathBot DeepSeek API"
SECURITY = "/usr/bin/security"
MAX_TURNS = 40  # model calls in one run; a run that needs more is cut off
MODELS_TTL = 3600


def keychain_key(item=KEYCHAIN_ITEM):
    if sys.platform == "win32":
        from core.credentials import windows_secret

        return windows_secret(item)
    if not os.path.exists(SECURITY):
        return None
    try:
        done = subprocess.run(
            [SECURITY, "find-generic-password", "-s", item, "-w"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    key = done.stdout.decode().strip() if done.returncode == 0 else ""
    return key or None


def function(tool):
    """An MCP tool (as /internal/tools lists it) as an OpenAI-style function."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": (tool.get("description") or "")[:1024],
            "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
        },
    }


def tool_text(result):
    """What a tool returned, as the text the model reads."""
    parts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
    return "\n".join(parts) or json.dumps(result.get("structuredContent"), ensure_ascii=False)


class DeepSeekHarness:
    name = "deepseek"

    def __init__(self, workspace=None, key=None, base_url=BASE_URL, model=None, transport=None):
        self.workspace = workspace
        self.base_url = base_url
        self.default_model = model
        self.transport = transport  # tests: an httpx.MockTransport for both APIs
        self._key = key
        self.known_models, self.models_read_at, self.reading = [], None, None

    def key(self):
        key = self._key or os.environ.get("ASSISTANT_DEEPSEEK_API_KEY")
        if not key and sys.platform == "win32":
            from dotenv import dotenv_values

            key = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get(
                "ASSISTANT_DEEPSEEK_API_KEY"
            )
        return key or keychain_key()

    def available(self):
        return bool(self.key())

    def client(self, **options):
        return httpx.AsyncClient(transport=self.transport, trust_env=False, **options)

    # Models -------------------------------------------------------------------------------

    def models(self):
        """The models the API lists for this key, read in the background at most hourly."""
        stale = self.models_read_at is None or time.monotonic() - self.models_read_at > MODELS_TTL
        if stale and (self.reading is None or self.reading.done()) and self.available():
            try:
                self.reading = asyncio.get_running_loop().create_task(self.read_models())
            except RuntimeError:
                pass
        default = self.chosen_default()
        return [{**m, "default": m["id"] == default} for m in self.known_models]

    async def read_models(self):
        try:
            async with self.client(base_url=self.base_url, timeout=20) as client:
                response = await client.get(
                    "/models", headers={"Authorization": f"Bearer {self.key()}"}
                )
            items = response.json().get("data") or [] if response.status_code == 200 else []
            found = [
                {"id": item["id"], "name": item["id"]}
                for item in items
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
        except (httpx.HTTPError, ValueError):
            found = []
        self.models_read_at = time.monotonic()
        if found:
            self.known_models = found

    def chosen_default(self):
        """The configured model, else a flash model (fast and cheap) the API lists."""
        if self.default_model or os.environ.get("ASSISTANT_DEEPSEEK_MODEL"):
            return self.default_model or os.environ["ASSISTANT_DEEPSEEK_MODEL"]
        ids = [m["id"] for m in self.known_models]
        flash = [i for i in ids if "flash" in i and "vision" not in i]
        return (flash or ids or ["deepseek-chat"])[0]

    # One run ------------------------------------------------------------------------------

    async def run(self, prompt, mcp_config, timeout, model=None) -> RunResult:
        key = self.key()
        if not key:
            return RunResult(False, "deepseek_key_missing")
        if not self.known_models and not (model or self.default_model):
            await self.read_models()
        try:
            return await asyncio.wait_for(
                self.loop(prompt, mcp_config["environment"], key, model or self.chosen_default()),
                timeout,
            )
        except TimeoutError:
            return RunResult(False, "timeout")

    async def loop(self, prompt, environment, key, model):
        core = self.client(
            base_url=environment["ASSISTANT_BASE_URL"],
            headers={"Authorization": f"Bearer {environment['ASSISTANT_TOKEN']}"},
            timeout=120,
        )
        api = self.client(
            base_url=self.base_url, headers={"Authorization": f"Bearer {key}"}, timeout=300
        )
        usage = Usage()
        async with core, api:
            listed = await core.get("/internal/tools")
            tools = [function(tool) for tool in listed.json()]
            messages = [{"role": "user", "content": prompt}]
            for _ in range(MAX_TURNS):
                try:
                    response = await api.post(
                        "/chat/completions",
                        json={"model": model, "messages": messages, "tools": tools},
                    )
                except httpx.HTTPError:
                    return RunResult(False, "deepseek_unreachable", usage)
                if response.status_code != 200:
                    # The body may echo the request; only its kind is kept (base.classify).
                    error = "authentication_required" if response.status_code == 401 else None
                    return RunResult(False, error or classify(response.text), usage)
                body = response.json()
                used = body.get("usage") or {}
                usage = usage + Usage(
                    count(used.get("prompt_tokens")),
                    count(used.get("prompt_cache_hit_tokens")),
                    count(used.get("completion_tokens")),
                )
                message = (body.get("choices") or [{}])[0].get("message") or {}
                calls = message.get("tool_calls") or []
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        **({"tool_calls": calls} if calls else {}),
                    }
                )
                if not calls:
                    return RunResult(True, None, usage)
                for call in calls:
                    name = call.get("function", {}).get("name", "")
                    try:
                        arguments = json.loads(call["function"].get("arguments") or "{}")
                        result = (await core.post(f"/internal/tools/{name}", json=arguments)).json()
                        text = tool_text(result)
                    except (ValueError, KeyError, httpx.HTTPError) as error:
                        text = f"tool_failed: {type(error).__name__}"
                    messages.append(
                        {"role": "tool", "tool_call_id": call.get("id"), "content": text}
                    )
        return RunResult(False, "agent_turn_limit", usage)
