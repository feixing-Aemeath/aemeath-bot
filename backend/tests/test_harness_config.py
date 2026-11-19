import asyncio
import os
import signal
import sys

import pytest

from core import helper_process
from core.config import Settings, safe_bind
from harness.base import CLIHarness, classify
from harness.claude_code import ClaudeCodeHarness
from harness.codex import CodexHarness, app_server_models


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "8.8.8.8", "evil.example"])
def test_public_and_lan_bind_rejected(host):
    with pytest.raises(ValueError):
        safe_bind(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "100.99.10.20", "fd7a:115c:a1e0::1"])
def test_loopback_and_tailscale(host):
    assert safe_bind(host) == host


def test_token_required_and_cli_flags(tmp_path):
    with pytest.raises(ValueError):
        Settings(token="short", _env_file=None)
    config = {"stdio": {"command": "python", "args": ["-m", "core.mcp_stdio"], "cwd": "/backend"}}
    claude = ClaudeCodeHarness(tmp_path).command(config)
    codex = CodexHarness(tmp_path).command(config)
    assert "--strict-mcp-config" in claude
    assert "--tools" in claude and "" in claude
    assert "--ignore-user-config" in codex
    assert "features.shell_tool=false" in codex
    # `codex exec` cannot ask anyone; only "approve" lets it call the assistant's tools.
    assert 'mcp_servers.assistant.default_tools_approval_mode="approve"' in codex
    assert not any("dangerously" in flag for flag in claude + codex)
    assert classify("quota exceeded") == "quota_exhausted"
    assert classify("unauthorized") == "authentication_required"
    # A model picked for one chat line.
    assert ClaudeCodeHarness(tmp_path).command(config, "opus")[-2:] == ["--model", "opus"]
    assert CodexHarness(tmp_path).command(config, "gpt-6-luna")[-3:] == [
        "--model",
        "gpt-6-luna",
        "-",
    ]
    assert "--model" not in claude + codex
    assert [m["id"] for m in ClaudeCodeHarness(tmp_path).models()] == ["fable", "opus", "sonnet"]


async def test_codex_models_come_from_its_app_server(tmp_path):
    script = tmp_path / "app_server.py"
    script.write_text(
        "import json, sys\n"
        "assert sys.argv[1:] == ['app-server']\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if message.get('method') == 'initialize':\n"
        "        print(json.dumps({'id': message['id'], 'result': {}}), flush=True)\n"
        "    elif message.get('method') == 'model/list':\n"
        "        data = [\n"
        "            {'id': 'a', 'model': 'gpt-a', 'displayName': 'GPT-A', 'isDefault': True},\n"
        "            {'id': 'b', 'model': 'gpt-b', 'displayName': 'GPT-B', 'hidden': True},\n"
        "            {'id': 'c', 'model': 'gpt-c'},\n"
        "        ]\n"
        "        print(json.dumps({'id': message['id'], 'result': {'data': data}}), flush=True)\n",
        encoding="utf-8",
    )
    command = script
    assert await app_server_models(str(command)) == [
        {"id": "gpt-a", "name": "GPT-A", "default": True},
        {"id": "gpt-c", "name": "gpt-c", "default": False},
    ]

    class Codex(CodexHarness):
        executable = str(command)

    harness = Codex(tmp_path)
    assert harness.models() == []  # read in the background the first time
    await harness.reading
    assert [m["id"] for m in harness.models()] == ["gpt-a", "gpt-c"]
    # A Codex that cannot answer leaves the list as it was.
    command.write_text("import sys; sys.exit(1)\n")
    harness.models_read_at = None
    harness.models()
    await harness.reading
    assert [m["id"] for m in harness.models()] == ["gpt-a", "gpt-c"]


async def test_harness_real_subprocess_and_timeout(tmp_path):
    class Stub(CLIHarness):
        executable = sys.executable
        code = 'print(\'{"is_error":true,"error":"quota exceeded"}\')'

        def command(self, mcp_config):
            return [sys.executable, "-c", self.code]

    harness = Stub(tmp_path)
    result = await harness.run("private input", {"environment": {}}, 10)
    assert result.error == "quota_exhausted"
    harness.code = "import time; time.sleep(20)"
    assert (await harness.run("", {"environment": {}}, 0.1)).error == "timeout"
    harness.code = 'print(\'{"result":"ok"}\')'
    assert (await harness.run("", {"environment": {}}, 10)).success


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("exited", [False, True])
async def test_timeout_cleans_the_group_after_the_cli_exits(tmp_path, monkeypatch, exited):
    killed = []

    class Process:
        pid = 12345
        returncode = 0 if exited else None

        async def communicate(self, _data):
            raise TimeoutError

        async def wait(self):
            return 0

    async def spawn(*_args, **_kwargs):
        return Process()

    def killpg(pid, sig):
        killed.append((pid, sig))
        if not exited:
            raise ProcessLookupError

    class Stub(CLIHarness):
        executable = sys.executable

        def command(self, _config):
            return [self.executable]

    monkeypatch.setattr(helper_process, "start", spawn)
    monkeypatch.setattr(helper_process, "WINDOWS", False)
    monkeypatch.setattr(helper_process.os, "killpg", killpg, raising=False)
    result = await Stub(tmp_path).run("", {"environment": {}}, 1)
    assert result.error == "timeout"
    assert killed == [(12345, signal.SIGKILL)]


@pytest.mark.parametrize(
    "harness_class,events",
    [
        (ClaudeCodeHarness, [{"type": "system", "subtype": "init"}]),
        (CodexHarness, [{"type": "thread.started"}, {"type": "turn.started"}]),
        (CodexHarness, [{"type": "turn.completed"}, {"type": "turn.started"}]),
    ],
)
async def test_cli_zero_exit_without_a_complete_result_fails(tmp_path, harness_class, events):
    from tests.test_token_usage import printing

    result = await printing(harness_class, tmp_path, events).run("", {"environment": {}}, 10)
    assert not result.success and result.error == "agent_incomplete"


@pytest.mark.parametrize(
    "output", ["usage_limit_reached", "Insufficient Balance", "You've hit your limit"]
)
def test_quota_error_variants_are_classified(output):
    assert classify(output) == "quota_exhausted"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
async def test_timeout_kills_mcp_child_even_after_cli_parent_exits(tmp_path):
    marker, pid_file = tmp_path / "survived", tmp_path / "child.pid"
    child = f"import time; from pathlib import Path; time.sleep(0.5); Path({str(marker)!r}).touch()"
    parent = (
        "import subprocess,sys; from pathlib import Path; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        f"Path({str(pid_file)!r}).write_text(str(p.pid))"
    )

    class Stub(CLIHarness):
        executable = sys.executable

        def command(self, mcp_config):
            return [sys.executable, "-c", parent]

    try:
        result = await Stub(tmp_path).run("", {"environment": {}}, 0.2)
        assert result.error == "timeout"
        assert pid_file.is_file()
        await asyncio.sleep(0.7)
        assert not marker.exists(), "stdio MCP child survived the timed-out run"
    finally:
        if pid_file.is_file():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


async def test_cli_environment_contains_only_its_run_scoped_assistant_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ASSISTANT_TOKEN", "synthetic-master")
    monkeypatch.setenv("ASSISTANT_DEEPSEEK_API_KEY", "synthetic-other-provider")
    monkeypatch.setenv("FEISHU_APP_SECRET", "synthetic-source")
    script = (
        "import os,sys; "
        "sys.exit(0 if os.environ.get('ASSISTANT_TOKEN') == 'synthetic-run' "
        "and 'ASSISTANT_DEEPSEEK_API_KEY' not in os.environ "
        "and 'FEISHU_APP_SECRET' not in os.environ else 1)"
    )

    class Stub(CLIHarness):
        executable = sys.executable

        def command(self, _config):
            return [self.executable, "-c", script]

    result = await Stub(tmp_path).run("", {"environment": {"ASSISTANT_TOKEN": "synthetic-run"}}, 5)
    assert result.success
