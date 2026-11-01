import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from core import helper_process
from core.platforms import find_command


@dataclass(frozen=True)
class Usage:
    """One run's tokens, the same way for every CLI: input includes cached input."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    # Only when the CLI reports a price (Claude Code's total_cost_usd).
    cost_usd: float | None = None

    def __add__(self, other):
        costs = [c for c in (self.cost_usd, other.cost_usd) if c is not None]
        return Usage(
            self.input_tokens + other.input_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
            self.output_tokens + other.output_tokens,
            sum(costs) if costs else None,
        )


@dataclass(frozen=True)
class RunResult:
    success: bool
    error: str | None = None
    # Token counts the CLI reported for this run (see Usage), or None when it reported none.
    usage: Usage | None = None


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def classify(output: str):
    lower = output.lower()
    if any(
        word in lower
        for word in ("rate_limit", "rate limit", "quota", "usage limit", "credit balance")
    ):
        return "quota_exhausted"
    if any(
        word in lower for word in ("unauthorized", "authentication", "not logged", "login required")
    ):
        return "authentication_required"
    return "agent_failed"


class CLIHarness:
    executable = ""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        workspace.mkdir(parents=True, exist_ok=True)

    def available(self):
        return bool(find_command(self.executable))

    def command(self, mcp_config, model=None):
        raise NotImplementedError

    def models(self):
        """Models one can pick for a run: [{"id", "name", "default"}]."""
        return []

    def usage(self, item) -> Usage | None:
        """Tokens in one JSON output line, if it carries any; the run's usage is their sum."""
        return None

    async def run(self, prompt, mcp_config, timeout, model=None) -> RunResult:
        if not self.available():
            return RunResult(False, "cli_unavailable")
        env = os.environ.copy()
        env.update(mcp_config["environment"])
        # A child never needs unrelated app secrets. Model/CLI login remains managed by its CLI.
        for key in list(env):
            if key.startswith("FEISHU_"):
                env.pop(key)
        command = self.command(mcp_config, model) if model else self.command(mcp_config)
        command[0] = find_command(command[0]) or command[0]
        proc = await helper_process.start(
            command,
            cwd=self.workspace,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
        except TimeoutError:
            await helper_process.stop(proc)
            return RunResult(False, "timeout")
        except asyncio.CancelledError:
            await helper_process.stop(proc)
            raise
        finally:
            helper_process.close_job(proc)
        # CLI responses may contain private source text: classify in memory and discard.
        # Only token counts are kept.
        output = (stdout + stderr).decode(errors="replace")
        usage, failure = None, None
        for line in stdout.decode(errors="replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            found = self.usage(item)
            if found:
                usage = found if usage is None else usage + found
            kind = item.get("type")
            if item.get("is_error") or kind in {"error", "turn.failed"}:
                failure = classify(line)
            elif kind == "turn.completed":
                # Codex reports a dropped connection it then retried as an "error" event; a
                # turn that completes after it succeeded.
                failure = None
        if proc.returncode:
            return RunResult(False, classify(output), usage)
        if failure:
            return RunResult(False, failure, usage)
        return RunResult(True, usage=usage)
