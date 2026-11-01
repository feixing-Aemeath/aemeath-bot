import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from core import helper_process
from core.apple import MacRequiredBridge
from core.config import Settings
from core.lock import InstanceLock
from core.models import PluginPatch
from core.platforms import command_argv, platform_name, windows_component
from core.service import Service
from plugins.token_usage.limits import json_lines
from tests.conftest import candidate
from tests.test_api import client_for


def test_instance_lock_rejects_overlap_and_can_be_reacquired(tmp_path):
    first, second = InstanceLock(tmp_path / "lock"), InstanceLock(tmp_path / "lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another_backend"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_npm_shims_preserve_json_arguments_without_a_shell(tmp_path, monkeypatch):
    root = tmp_path / "node_modules/@openai/codex"
    root.mkdir(parents=True)
    entry = root / "bin/codex.js"
    entry.parent.mkdir()
    entry.write_text("// synthetic npm entry", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"bin": {"codex": "bin/codex.js"}}))
    node = tmp_path / "node.exe"
    node.touch()
    shim = tmp_path / "codex.cmd"
    shim.touch()
    monkeypatch.setattr(
        "core.platforms.shutil.which", lambda name: str(shim) if name == "codex" else None
    )
    args = ["codex", "-c", 'mcp.env="a & b | %USERPROFILE% ^ 中文"', ""]
    assert command_argv(args, windows=True) == [str(node), str(entry), *args[1:]]
    with pytest.raises(FileNotFoundError, match="npm_entry"):
        command_argv([str(tmp_path / "unknown.cmd")], windows=True)


def test_platform_and_windows_device_paths(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(helper_process, "WINDOWS", True)
    assert platform_name() == "windows"
    assert helper_process.process_options()["creationflags"] & 0x200
    for name in ("CON.txt", "aux", "LPT1.md", "file:stream", "alias.", "alias "):
        assert not windows_component(name)
    assert windows_component("课堂记录.md")


async def test_windows_keeps_todos_pending_and_reports_mac_required(seeded):
    seeded.apple.bridge = MacRequiredBridge()
    result = await seeded.add_todo(candidate(seeded))
    assert result["todo"]["sync_error"] == "macos_required"
    assert result["todo"]["reminder_id"] is None
    assert seeded.db.rows("SELECT count(*) n FROM outbox")[0]["n"] == 1
    with pytest.raises(RuntimeError, match="macos_required"):
        await seeded.jobs.sync()
    with client_for(seeded) as client:
        assert client.get("/api/v1/status").json()["apple_sync_error"] == "macos_required"
    # Not scheduled every five minutes only to fail.
    seeded.jobs.configure()
    assert seeded.jobs.scheduler.get_job("apple_sync") is None
    assert seeded.jobs.scheduler.get_job("extract_todos") is not None


async def test_windows_skips_mac_plugins_and_accepts_windows_manifests(tmp_path):
    service = Service(
        Settings(token="w" * 40, data_dir=tmp_path, scheduler_enabled=False, _env_file=None),
        platform="windows",
    )
    assert isinstance(service.apple.bridge, MacRequiredBridge)
    await service.start()
    try:
        assert service.plugins.view("class_photos")["state"] == "unsupported"
        assert service.plugins.supported("feishu")
        with pytest.raises(Exception, match="unsupported_platform"):
            await service.plugins.patch("class_photos", PluginPatch(enabled=True))
    finally:
        await service.close()


async def test_windows_stop_closes_job_after_launcher_exit(monkeypatch):
    closed = []

    async def wait():
        closed.append("wait")

    process = SimpleNamespace(
        returncode=0, _assistant_job=SimpleNamespace(close=lambda: closed.append("job")), wait=wait
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(helper_process, "WINDOWS", True)
    await helper_process.stop(process)
    assert closed == ["job", "wait"]


def test_windows_json_pipe_does_not_use_socket_selectors(monkeypatch):
    import io
    import time

    monkeypatch.setattr(sys, "platform", "win32")
    assert list(json_lines(io.BytesIO(b'bad\n{"id":2}\n'), time.monotonic() + 1)) == [{"id": 2}]


async def test_python_helper_subprocess_is_portable(tmp_path):
    script = tmp_path / "helper with spaces.py"
    script.write_text("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())")
    data = "中文 & | % ^".encode()
    assert await helper_process.run([str(script)], data, 10) == (0, data)


async def test_timeout_kills_python_grandchild_on_every_os(tmp_path):
    late = tmp_path / "late"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(late)!r}).touch()"
    code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(20)"
    )
    with pytest.raises(TimeoutError):
        await helper_process.run([sys.executable, "-c", code], None, 0.3)
    await asyncio.sleep(1.1)
    assert not late.exists()


def test_windows_credentials_use_only_the_os_vault(monkeypatch):
    from core.credentials import windows_secret

    monkeypatch.setattr(sys, "platform", "win32")
    calls = []
    vault = SimpleNamespace(
        get_password=lambda service, user: calls.append((service, user)) or "secret"
    )
    monkeypatch.setitem(
        sys.modules, "keyring.backends.Windows", SimpleNamespace(WinVaultKeyring=lambda: vault)
    )
    monkeypatch.setattr("core.credentials.getpass.getuser", lambda: "test-user")
    assert windows_secret("AemeathBot DeepSeek API") == "secret"
    assert calls == [("AemeathBot DeepSeek API", "test-user")]


def test_windows_job_assigns_before_resuming_and_closes_handles(monkeypatch):
    from core.windows_process import ProcessJob

    calls = []
    process_handle = SimpleNamespace(Close=lambda: calls.append("process_close"))
    thread_handle = SimpleNamespace(Close=lambda: calls.append("thread_close"))
    job_handle = SimpleNamespace(Close=lambda: calls.append("job_close"))
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(
            OpenProcess=lambda *args: process_handle,
            OpenThread=lambda *args: thread_handle,
        ),
    )
    monkeypatch.setitem(
        sys.modules, "win32con", SimpleNamespace(PROCESS_SET_QUOTA=1, PROCESS_TERMINATE=2)
    )
    monkeypatch.setitem(
        sys.modules,
        "win32job",
        SimpleNamespace(
            CreateJobObject=lambda *args: job_handle,
            JobObjectExtendedLimitInformation=9,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0x2000,
            QueryInformationJobObject=lambda *args: {"BasicLimitInformation": {"LimitFlags": 0}},
            SetInformationJobObject=lambda *args: calls.append("kill_on_close"),
            AssignProcessToJobObject=lambda *args: calls.append("assign"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(ResumeThread=lambda thread: calls.append("resume")),
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: SimpleNamespace(threads=lambda: [SimpleNamespace(id=3)])
        ),
    )
    job = ProcessJob(123)
    assert calls.index("assign") < calls.index("resume")
    assert "process_close" in calls and "thread_close" in calls
    job.close()
    job.close()
    assert calls.count("job_close") == 1
