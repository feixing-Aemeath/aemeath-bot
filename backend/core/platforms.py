"""Portable paths and executable discovery. No Windows imports during ordinary imports."""

import json
import os
import shutil
import sys
from pathlib import Path


def platform_name():
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)


def command_argv(argv, *, windows=None):
    """Resolve npm's .cmd shims to node + its package's entry point, without cmd.exe.

    JSON CLI options contain quotes and shell metacharacters. Running the JS entry point
    directly preserves every argument and never puts prompts or credentials through a shell.
    Unknown batch files fail closed; native .exe tools and Python entry points work normally.
    """
    argv = list(map(str, argv))
    if Path(argv[0]).suffix.lower() == ".py":
        return [sys.executable, "-X", "utf8", *argv]
    if not (sys.platform == "win32" if windows is None else windows):
        return argv
    executable = shutil.which(argv[0]) or argv[0]
    shim = Path(executable)
    if shim.suffix.lower() not in {".cmd", ".bat"}:
        return [executable, *argv[1:]]
    packages = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
    package = packages.get(shim.stem.lower())
    if package:
        root = shim.parent / "node_modules" / package
        try:
            metadata = json.loads((root / "package.json").read_text(encoding="utf-8"))
            bins = metadata["bin"]
            entry = root / (bins[shim.stem.lower()] if isinstance(bins, dict) else bins)
            node = shim.parent / "node.exe"
            node = str(node) if node.is_file() else find_command("node")
            if node and entry.is_file() and entry.resolve().is_relative_to(root.resolve()):
                return [node, str(entry), *argv[1:]]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    raise FileNotFoundError("npm_entry_point_not_found")


def tool_directories(home=None, environ=None):
    home, env = home or Path.home(), os.environ if environ is None else environ
    if sys.platform == "win32":
        return (
            home / ".local/bin",
            home / ".cargo/bin",
            Path(env.get("APPDATA", str(home / "AppData/Roaming"))) / "npm",
            Path(env.get("ProgramFiles", "C:/Program Files")) / "Tailscale",
            Path(env.get("ProgramFiles", "C:/Program Files")) / "nodejs",
            Path(env.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "Microsoft/WinGet/Links",
        )
    return (home / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"))


def find_command(name, extra=()):
    if Path(name).suffix.lower() == ".py" and Path(name).is_file():
        return str(name)
    found = shutil.which(name)
    if found:
        return found
    for directory in (*extra, *tool_directories()):
        found = shutil.which(name, path=str(Path(directory).expanduser()))
        if found:
            return found
    return None


def is_link(path):
    # A Windows junction is a directory reparse point, but is_symlink() returns False.
    return path.is_symlink() or path.is_junction()


def windows_component(name):
    """Exclude Win32 device names, alternate streams and trailing-dot/space aliases."""
    return (
        not any(c in '<>:"\\|?*' or ord(c) < 32 for c in name)
        and not name.endswith((" ", "."))
        and name.split(".")[0].rstrip().upper()
        not in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "CONIN$",
            "CONOUT$",
            *(f"{p}{i}" for p in ("COM", "LPT") for i in "0123456789¹²³"),
        }
    )
