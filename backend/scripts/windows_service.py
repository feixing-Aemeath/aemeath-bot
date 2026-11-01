"""Task Scheduler entry point. One backend process, logged like the Mac LaunchAgent."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from core.config import Settings
from core.helper_process import run_sync
from core.platforms import tool_directories

BACKEND = Path(__file__).resolve().parents[1]


def configuration(backend=BACKEND):
    settings = Settings(_env_file=backend / ".env")
    state = settings.data_dir
    if not state.is_absolute():
        state = backend / state
    return {
        "backend": str(backend),
        "logs": str(state / "logs"),
        "host": settings.host,
        "port": settings.port,
    }


def service_environment():
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([*map(str, tool_directories()), env.get("PATH", os.defpath)])
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return env


def run(config, *, runner=run_sync):
    logs = Path(config["logs"])
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "service.log").open("ab") as out, (logs / "service-error.log").open("ab") as err:
        result = runner(
            [sys.executable, "-X", "utf8", "-m", "core"],
            cwd=config["backend"],
            env=service_environment(),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            timeout=None,
        )
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows 登录任务入口")
    parser.add_argument("--check", action="store_true", help="只验证配置，不启动服务、不打开数据库")
    args = parser.parse_args(argv)
    try:
        config = configuration()
    except Exception:
        print(
            "configuration_invalid: check .env token and loopback/Tailscale binding",
            file=sys.stderr,
        )
        return 1
    if args.check:
        print(json.dumps(config, ensure_ascii=False))
        return 0
    if sys.platform != "win32":
        print("windows_required", file=sys.stderr)
        return 1
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
