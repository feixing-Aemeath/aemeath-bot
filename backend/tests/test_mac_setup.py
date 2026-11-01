import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from core.config import Settings
from scripts.mac_setup import ensure_env, install_plist, render_plist, search_path, wait_for_api

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "install_mac.sh"


def test_env_is_created_once_and_never_overwritten(tmp_path):
    shutil.copy(BACKEND / ".env.example", tmp_path / ".env.example")
    assert ensure_env(tmp_path) == "created"
    env = tmp_path / ".env"
    if os.name != "nt":
        assert env.stat().st_mode & 0o777 == 0o600
    token = next(
        line.split("=", 1)[1]
        for line in env.read_text(encoding="utf-8").splitlines()
        if line.startswith("ASSISTANT_TOKEN=")
    )
    assert len(token) >= 32
    Settings(_env_file=env)  # the generated file is a valid configuration
    env.write_text("ASSISTANT_TOKEN=keep-my-own-value-000000000000000000\n", encoding="utf-8")
    assert ensure_env(tmp_path) == "kept"
    assert "keep-my-own-value" in env.read_text(encoding="utf-8")


def test_launch_agent_is_written_once_and_kept(tmp_path):
    backend, home = tmp_path / "backend", tmp_path / "home"
    target = home / "Library" / "LaunchAgents" / "local.assistant.plist"
    plist = render_plist(backend, home, "/opt/homebrew/bin:/usr/bin:/bin")
    assert plist["ProgramArguments"] == [str(backend / ".venv/bin/python"), "-m", "core"]
    assert plist["WorkingDirectory"] == str(backend)
    assert plist["LimitLoadToSessionType"] == "Aqua"
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True

    state = backend / ".state"
    assert install_plist(target, plist, state) == "created"
    assert plistlib.loads(target.read_bytes()) == plist
    assert (state / "logs").is_dir()
    assert install_plist(target, plist, state) == "unchanged"

    edited = {**plist, "ThrottleInterval": 60}
    target.write_bytes(plistlib.dumps(edited))
    assert install_plist(target, plist, state) == "differs"
    assert plistlib.loads(target.read_bytes()) == edited  # user's edit kept
    assert plistlib.loads((state / "launchd" / "local.assistant.plist.candidate").read_bytes())

    assert install_plist(target, plist, state, replace=True) == "replaced"
    assert plistlib.loads(target.read_bytes()) == plist
    backups = list((state / "launchd").glob("local.assistant.plist.*.bak"))
    assert len(backups) == 1 and plistlib.loads(backups[0].read_bytes()) == edited


def test_search_path_puts_agent_clis_first_without_duplicates():
    home = PurePosixPath("/Users/test-user")
    tools = {"claude": f"{home}/.local/bin/claude", "uv": "/opt/homebrew/bin/uv"}
    value = search_path(home, which=tools.get).split(":")
    assert value[:2] == [f"{home}/.local/bin", "/opt/homebrew/bin"]
    assert len(value) == len(set(value))
    assert "/usr/bin" in value and "/bin" in value


def test_wait_for_api_polls_until_ready():
    answers = iter([None, 503, 200])

    class Probe:
        def http_status(self, url, token=None, timeout=3):
            assert url == "http://127.0.0.1:8765/api/v1/status" and token == "w" * 40
            return next(answers)

    settings = Settings(token="w" * 40, _env_file=None)
    assert wait_for_api(settings, 30, Probe(), sleep=lambda seconds: None) == 200


@pytest.mark.skipif(
    sys.platform == "win32", reason="macOS shell installer; PowerShell is checked separately"
)
def test_install_script_syntax_and_help():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    for option in ("--no-apps", "--serve", "--restart", "--replace-plist"):
        assert option in result.stdout
    bad = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
    assert bad.returncode == 2


@pytest.mark.skipif(
    sys.platform in {"darwin", "win32"}, reason="Linux refusal test for the Mac shell installer"
)
def test_install_script_refuses_to_run_off_macos():
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "只能在 macOS 上运行" in result.stderr
