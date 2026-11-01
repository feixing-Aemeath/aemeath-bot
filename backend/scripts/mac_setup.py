"""Steps of scripts/install_mac.sh that are easier to get right (and test) in Python.

Every step is idempotent and never overwrites existing configuration:

- env:     create backend/.env from .env.example with a fresh token, only when it is missing.
- launchd: render the LaunchAgent plist; write it when missing, leave a differing one alone
           (unless --replace, which keeps a backup) and report what happened on stdout.
- port:    print the configured API port.
- wait:    wait until the local API answers, for the installer to run doctor afterwards.

Status words go to stdout for the shell script; explanations go to stderr. The token is never
printed.
"""

import argparse
import os
import plistlib
import secrets
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

BACKEND = Path(__file__).resolve().parents[1]
LABEL = "local.assistant"
DEFAULT_PATH = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)
TOOLS = ("claude", "codex", "uv", "tailscale")


def say(message):
    print(message, file=sys.stderr)


def ensure_env(backend_dir=BACKEND):
    """Create .env with a new token if it does not exist. Returns created / kept."""
    target = backend_dir / ".env"
    if target.exists():
        return "kept"
    lines = []
    for line in (backend_dir / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("ASSISTANT_TOKEN="):
            line = "ASSISTANT_TOKEN=" + secrets.token_urlsafe(32)
        lines.append(line)
    # O_EXCL: never clobber a file that appeared meanwhile; 0600 before any secret is written.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return "created"


def search_path(home, which=shutil.which):
    """PATH for launchd: where the agent CLIs live now, then the usual locations."""
    entries = []
    for tool in TOOLS:
        found = which(tool)
        if found:
            entries.append(str(PurePosixPath(found).parent))
    entries += [entry.replace("~", str(home), 1) for entry in DEFAULT_PATH]
    return ":".join(dict.fromkeys(entries))


def render_plist(backend_dir, home, path_env, label=LABEL):
    logs = backend_dir / ".state" / "logs"
    return {
        "Label": label,
        # The venv's python symlink survives `uv sync`; permissions attach to its real target.
        "ProgramArguments": [str(backend_dir / ".venv" / "bin" / "python"), "-m", "core"],
        "WorkingDirectory": str(backend_dir),
        "EnvironmentVariables": {"PATH": path_env, "HOME": str(home), "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        # Screen capture and accessibility need the logged-in GUI session.
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(logs / "service.log"),
        "StandardErrorPath": str(logs / "service-error.log"),
    }


def install_plist(target, plist, state_dir, replace=False):
    """Returns created / unchanged / differs / replaced."""
    target, state_dir = Path(target), Path(state_dir)
    content = plistlib.dumps(plist, sort_keys=False)
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            current = plistlib.loads(target.read_bytes())
        except Exception:
            current = None
        if current == plist:
            return "unchanged"
        launchd_dir = state_dir / "launchd"
        launchd_dir.mkdir(parents=True, exist_ok=True)
        if not replace:
            candidate = launchd_dir / f"{target.name}.candidate"
            candidate.write_bytes(content)
            say(f"已有的 {target} 和新生成的不同，保留原文件。")
            say(f"新版本写在 {candidate}；确认后可加 --replace-plist 替换（会先备份）。")
            return "differs"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(target, launchd_dir / f"{target.name}.{stamp}.bak")
        say(f"已备份原文件到 {launchd_dir / f'{target.name}.{stamp}.bak'}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        replace = False
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)
    return "replaced" if replace else "created"


def load_settings(backend_dir):
    sys.path.insert(0, str(backend_dir))
    from core.config import Settings

    return Settings(_env_file=backend_dir / ".env")


def wait_for_api(settings, timeout, probe=None, sleep=time.sleep):
    from scripts.doctor import SystemProbe

    probe = probe or SystemProbe()
    deadline = time.monotonic() + timeout
    token = settings.token.get_secret_value()
    while True:
        status = probe.http_status(f"{settings.base_url}/api/v1/status", token)
        if status == 200 or time.monotonic() >= deadline:
            return status
        sleep(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("env")
    launchd = commands.add_parser("launchd")
    launchd.add_argument("--target", default=f"~/Library/LaunchAgents/{LABEL}.plist")
    launchd.add_argument("--replace", action="store_true")
    commands.add_parser("port")
    wait = commands.add_parser("wait")
    wait.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args(argv)
    home = Path.home()
    if args.command == "env":
        status = ensure_env()
        say(
            ".env 已存在，未改动。"
            if status == "kept"
            else "已生成 .env（token 只写在本机文件里）。"
        )
        print(status)
    elif args.command == "launchd":
        plist = render_plist(BACKEND, home, search_path(home))
        target = Path(args.target).expanduser()
        print(install_plist(target, plist, BACKEND / ".state", args.replace))
    elif args.command == "port":
        print(load_settings(BACKEND).port)
    else:
        status = wait_for_api(load_settings(BACKEND), args.timeout)
        print("ready" if status == 200 else f"unavailable:{status}")


if __name__ == "__main__":
    main()
