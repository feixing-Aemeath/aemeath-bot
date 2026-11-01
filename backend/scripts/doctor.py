"""Read-only environment self-check for the Mac mini (and the subset that applies on Linux/WSL).

Every item reports ok / fail / manual / not_applicable:

- ok: verified automatically on this machine.
- fail: verified automatically and not ready; `fix` says what to do.
- manual: cannot be verified from a script (for example whether QQ is logged in); look yourself.
- not_applicable: the item does not apply here (not macOS, or ruled out by another choice).

Nothing here changes settings, requests a permission, clicks in an app or reads chat content.
macOS state is read through a Probe (commands, files, local HTTP, TCC database) so tests can
replay canned macOS outputs on Linux.
"""

import argparse
import getpass
import importlib.metadata
import json
import os
import platform as platform_module
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LABEL = "local.assistant"
SYSTEM_TCC = "/Library/Application Support/com.apple.TCC/TCC.db"
USER_TCC = "~/Library/Application Support/com.apple.TCC/TCC.db"
# permission -> (TCC service, database, which binaries may hold it)
PERMISSIONS = {
    "screen_recording": ("kTCCServiceScreenCapture", SYSTEM_TCC, ("python", "screen_helper")),
    "accessibility": ("kTCCServiceAccessibility", SYSTEM_TCC, ("python", "screen_helper")),
    "full_disk_access": ("kTCCServiceSystemPolicyAllFiles", SYSTEM_TCC, ("python",)),
    "reminders": ("kTCCServiceReminders", USER_TCC, ("python", "apple_helper")),
    "calendar": ("kTCCServiceCalendar", USER_TCC, ("python", "apple_helper")),
    "photos": ("kTCCServicePhotos", USER_TCC, ("python", "photo_helper")),
}
PERMISSION_TITLES = {
    "screen_recording": "屏幕录制",
    "accessibility": "辅助功能",
    "full_disk_access": "完全磁盘访问",
    "reminders": "提醒事项",
    "calendar": "日历",
    "photos": "照片",
}
STATUS_TITLES = {
    "ok": "通过",
    "fail": "未通过",
    "manual": "需人工确认",
    "not_applicable": "不适用",
}
STATUS_MARKS = {"ok": "✓", "fail": "✗", "manual": "?", "not_applicable": "-"}


@dataclass
class Result:
    code: int
    out: str = ""
    err: str = ""

    @property
    def text(self):
        return self.out + self.err


class SystemProbe:
    """The real machine. Every method returns None instead of raising when it cannot tell."""

    def __init__(self):
        self.platform = sys.platform
        self.home = Path.home()
        self.uid = os.getuid() if hasattr(os, "getuid") else 0
        self.user = getpass.getuser()

    def which(self, name, extra=()):
        if self.platform == "win32":
            from core.platforms import find_command

            return find_command(name, extra)
        found = shutil.which(name)
        if found:
            return found
        for directory in extra:
            candidate = Path(directory).expanduser() / name
            if os.access(candidate, os.X_OK) and candidate.is_file():
                return str(candidate)
        return None

    def run(self, args, timeout=20):
        try:
            if self.platform == "win32":
                from core.helper_process import run_sync

                completed = run_sync(
                    list(args),
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                return Result(completed.returncode, completed.stdout, completed.stderr)
            completed = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return Result(completed.returncode, completed.stdout, completed.stderr)

    def exists(self, path):
        return Path(path).expanduser().exists()

    def realpath(self, path):
        path = Path(path).expanduser()
        return str(path.resolve()) if path.exists() else None

    def http_status(self, url, token=None, timeout=3):
        # Never route the local checks through a proxy from the environment.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with opener.open(
                urllib.request.Request(url, headers=headers), timeout=timeout
            ) as reply:
                return reply.status
        except urllib.error.HTTPError as error:
            return error.code
        except (OSError, ValueError):
            return None

    def tcc_grants(self, database, service):
        """Clients allowed for `service`, or None when this process cannot read the database."""
        path = Path(database).expanduser()
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
                rows = conn.execute(
                    "SELECT client, auth_value FROM access WHERE service=?", (service,)
                ).fetchall()
        except sqlite3.Error:
            return None
        return {client for client, value in rows if value in (2, 3)}  # allowed / limited

    def newest_backup(self, data_dir):
        stamps = [p.stat().st_mtime for p in Path(data_dir).glob("backups/*/COMPLETE")]
        return max(stamps) if stamps else None

    def now(self):
        return time.time()


@dataclass
class Check:
    name: str
    title: str
    status: str
    detail: str = ""
    fix: str = ""
    optional: bool = False
    group: str = ""

    def as_dict(self):
        return {
            "name": self.name,
            "title": self.title,
            "group": self.group,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "optional": self.optional,
        }


@dataclass
class Report:
    probe: object
    checks: list = field(default_factory=list)
    group: str = ""

    def add(self, name, title, status, detail="", fix="", optional=False):
        self.checks.append(Check(name, title, status, detail, fix, optional, self.group))

    @property
    def macos(self):
        return self.probe.platform == "darwin"

    @property
    def windows(self):
        return self.probe.platform == "win32"

    def mac_only(self, name, title, optional=False):
        """Record not_applicable off macOS; True when the caller should run the real check."""
        if self.macos:
            return True
        self.add(name, title, "not_applicable", "只在 Mac mini 上检查", optional=optional)
        return False


def pmset_values(text):
    """`pmset -g` lines like ` displaysleep   0 (display sleep prevented by ...)`."""
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            values.setdefault(parts[0], int(parts[1]))
    return values


def load_settings(backend_dir):
    from core.config import Settings

    settings = Settings(_env_file=backend_dir / ".env")
    if not settings.data_dir.is_absolute():
        settings.data_dir = backend_dir / settings.data_dir
    return settings


# Checks --------------------------------------------------------------------------------------


def check_basics(report, backend_dir, settings, settings_error):
    probe = report.probe
    report.group = "基础环境"
    report.add(
        "python",
        "Python 3.12+",
        "ok" if sys.version_info >= (3, 12) else "fail",
        platform_module.python_version(),
        "cd backend && uv sync --locked",
    )
    try:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE VIRTUAL TABLE test USING fts5(content, tokenize='trigram')")
        report.add("sqlite_fts5", "SQLite FTS5（trigram）", "ok", sqlite3.sqlite_version)
    except sqlite3.Error:
        report.add("sqlite_fts5", "SQLite FTS5（trigram）", "fail", "需要 SQLite 3.34+ 且带 FTS5")
    for package in ("fastapi", "mcp", "apscheduler", "lark-oapi"):
        try:
            report.add(package, f"Python 包 {package}", "ok", importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            report.add(
                package, f"Python 包 {package}", "fail", "未安装", "cd backend && uv sync --locked"
            )
    if report.mac_only("homebrew", "Homebrew"):
        brew = probe.which("brew", ("/opt/homebrew/bin", "/usr/local/bin"))
        report.add(
            "homebrew",
            "Homebrew",
            "ok" if brew else "fail",
            brew or "未安装",
            "bash backend/scripts/install_mac.sh",
        )
    uv = probe.which("uv", ("~/.local/bin", "/opt/homebrew/bin"))
    report.add(
        "uv",
        "uv",
        "ok" if uv else "fail",
        uv or "未安装",
        "winget install --id astral-sh.uv -e"
        if report.windows
        else "brew install uv（或运行安装脚本）",
    )
    if report.mac_only("xcode_clt", "Xcode 命令行工具"):
        result = probe.run(["xcode-select", "-p"])
        ok = bool(result and result.code == 0)
        report.add(
            "xcode_clt",
            "Xcode 命令行工具",
            "ok" if ok else "fail",
            result.out.strip() if ok else "未安装（编译 apple-sync 需要）",
            "xcode-select --install",
        )

    if report.mac_only("doc_text", "资料提取 doc-text"):
        binary = probe.home / "Library/Application Support/AemeathBot/bin/doc-text"
        present = probe.exists(binary)
        report.add(
            "doc_text",
            "资料提取 doc-text",
            "ok" if present else "fail",
            "已安装" if present else "未安装（PDF 和图片暂时无法提取）",
            "bash backend/scripts/install_mac.sh",
        )

    report.group = "后端配置"
    if settings is None:
        report.add(
            "configuration",
            "后端配置（.env）",
            "fail",
            settings_error,
            "运行安装脚本生成 .env，或填写 32 位以上的 ASSISTANT_TOKEN 和本机/Tailscale 地址",
        )
    else:
        report.add(
            "api_binding",
            "监听地址",
            "ok",
            f"{settings.host}:{settings.port}；token 已配置（不显示）",
        )
        real = settings.apple_mode == "real"
        binary_ok = bool(settings.apple_binary and Path(settings.apple_binary).is_file())
        if report.windows:
            report.add(
                "apple_mode",
                "提醒事项同步模式",
                "not_applicable",
                "需要 Mac（macos_required）：Windows 保留待办和待同步队列，不模拟成功",
                "待办仍在 App 显示；将来可接入 Mac 同步桥",
            )
        elif not real:
            report.add(
                "apple_mode",
                "提醒事项同步模式",
                "manual",
                "fake：写入本地文件，还没有写真实的提醒事项",
                "在 Mac 上验证 apple-sync 后，在 .env 设 ASSISTANT_APPLE_MODE=real 和 "
                "ASSISTANT_APPLE_BINARY",
            )
        else:
            report.add(
                "apple_mode",
                "提醒事项同步模式",
                "ok" if binary_ok else "fail",
                f"real：{settings.apple_binary}" if binary_ok else "real，但 apple-sync 不存在",
                "按 docs/deployment.md 编译 apple-sync 并填写绝对路径",
            )
    env_file = backend_dir / ".env"
    if not env_file.exists():
        report.add(
            "secrets",
            "密钥文件 .env",
            "fail",
            "backend/.env 不存在",
            "bash backend/scripts/install_mac.sh（只在缺失时生成，不覆盖）",
        )
    else:
        mode = env_file.stat().st_mode & 0o777
        private = os.name == "nt" or not mode & 0o077
        ignored = probe.run(["git", "-C", str(backend_dir), "check-ignore", "-q", ".env"])
        tracked_risk = ignored is not None and ignored.code == 1
        problems = []
        if not private:
            problems.append(f"权限 {mode:o}，其他用户可读")
        if tracked_risk:
            problems.append("没有被 .gitignore 忽略")
        report.add(
            "secrets",
            "密钥文件 .env",
            "fail" if problems else "manual" if report.windows else "ok",
            "；".join(problems)
            or (
                "Git 已忽略；NTFS ACL 需确认仅本人和系统可读"
                if report.windows
                else "仅本人可读，已被 Git 忽略"
            ),
            "在文件属性 → 安全中检查 .env 和数据目录的 NTFS 权限"
            if report.windows
            else "chmod 600 backend/.env",
        )


def check_system(report):
    probe = report.probe
    report.group = "Mac 系统设置"
    applicable = report.macos
    for name, title in (
        ("power_restore", "断电恢复后自动开机"),
        ("system_sleep", "系统不睡眠"),
        ("display_awake", "显示器不睡眠"),
        ("auto_login", "自动登录"),
        ("filevault", "FileVault（建议关闭）"),
        ("lock_screen", "不锁屏"),
        ("screensaver", "屏幕保护程序不启动"),
        ("display_attached", "接有显示器或 HDMI 欺骗器"),
    ):
        if not applicable:
            report.mac_only(name, title)
    if not applicable:
        return

    pmset = probe.run(["pmset", "-g"])
    values = pmset_values(pmset.out) if pmset and pmset.code == 0 else {}

    def power(name, title, key, fix):
        if key not in values:
            report.add(name, title, "manual", f"pmset -g 没有 {key} 项", fix)
        else:
            report.add(
                name, title, "ok" if values[key] == 0 else "fail", f"{key}={values[key]}", fix
            )

    if "autorestart" in values:
        report.add(
            "power_restore",
            "断电恢复后自动开机",
            "ok" if values["autorestart"] == 1 else "fail",
            f"autorestart={values['autorestart']}",
            "sudo pmset -a autorestart 1（或 系统设置 → 能源 → 断电后自动启动）",
        )
    else:
        report.add(
            "power_restore",
            "断电恢复后自动开机",
            "manual",
            "pmset -g 没有 autorestart 项",
            "系统设置 → 能源 → 打开「断电后自动启动」",
        )
    power("system_sleep", "系统不睡眠", "sleep", "sudo pmset -a sleep 0")
    power(
        "display_awake",
        "显示器不睡眠",
        "displaysleep",
        "sudo pmset -a displaysleep 0（或 系统设置 → 锁定屏幕 → 不活跃时关闭显示器：永不）",
    )

    # FileVault is the user's trade-off (docs/mac-setup.md): off lets the Mac log in by itself
    # after a power cut; on keeps the disk locked until someone types the password, and then
    # macOS does not allow automatic login at all.
    vault = probe.run(["fdesetup", "status"])
    vault_on = None
    if vault is None or vault.code != 0:
        report.add("filevault", "FileVault（建议关闭）", "manual", "无法读取 fdesetup status")
    elif "FileVault is Off" in vault.out:
        vault_on = False
        report.add(
            "filevault",
            "FileVault（建议关闭）",
            "ok",
            "FileVault 已关闭：断电后能自动登录；代价是机器丢失时数据不受密码保护",
        )
    else:
        vault_on = True
        report.add(
            "filevault",
            "FileVault（建议关闭）",
            "manual",
            "FileVault 已开启：断电重启后要有人在 Mac 前输入密码，后端才会启动",
            "如果这是你的选择，确认已接受这一点或配了不间断电源；否则在 系统设置 → 隐私与安全性 → "
            "FileVault 关闭（利弊见 docs/mac-setup.md）",
        )

    login = probe.run(
        ["defaults", "read", "/Library/Preferences/com.apple.loginwindow", "autoLoginUser"]
    )
    user = login.out.strip() if login and login.code == 0 else ""
    if vault_on:
        report.add(
            "auto_login",
            "自动登录",
            "not_applicable",
            "FileVault 开启时系统不允许自动登录；开机解锁时输入密码就会直接登录",
        )
    else:
        report.add(
            "auto_login",
            "自动登录",
            "ok" if user == probe.user else "fail",
            f"自动登录为当前用户 {user}"
            if user == probe.user
            else (
                f"自动登录的是 {user}，不是运行后端的 {probe.user}" if user else "未开启自动登录"
            ),
            "系统设置 → 用户与群组 → 「自动以此身份登录」选当前用户（需先关闭 FileVault）",
        )
    lock = probe.run(["sysadminctl", "-screenLock", "status"])
    lock_text = lock.text.lower() if lock else ""
    if "screenlock is off" in lock_text:
        report.add("lock_screen", "不锁屏", "ok", "屏幕锁定已关闭")
    elif "delay is" in lock_text or "immediate" in lock_text:
        report.add(
            "lock_screen",
            "不锁屏",
            "fail",
            "屏幕保护或关闭显示器后需要密码",
            "系统设置 → 锁定屏幕 → 「屏幕保护程序启动或显示器关闭后需要密码」选「永不」",
        )
    else:
        report.add(
            "lock_screen",
            "不锁屏",
            "manual",
            "sysadminctl 没有给出可识别的结果",
            "系统设置 → 锁定屏幕 → 「屏幕保护程序启动或显示器关闭后需要密码」选「永不」",
        )
    saver = probe.run(["defaults", "-currentHost", "read", "com.apple.screensaver", "idleTime"])
    idle = saver.out.strip() if saver and saver.code == 0 else ""
    saver_fix = "系统设置 → 锁定屏幕 → 「不活跃时启动屏幕保护程序」选「永不」"
    if idle.lstrip("-").isdigit():
        report.add(
            "screensaver",
            "屏幕保护程序不启动",
            "ok" if int(idle) == 0 else "fail",
            "永不启动" if int(idle) == 0 else f"{int(idle) // 60} 分钟后启动",
            saver_fix,
        )
    else:
        report.add(
            "screensaver", "屏幕保护程序不启动", "manual", "没有读到 idleTime 设置", saver_fix
        )
    displays = probe.run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=30)
    count = None
    if displays and displays.code == 0:
        try:
            gpus = json.loads(displays.out).get("SPDisplaysDataType", [])
            count = sum(len(gpu.get("spdisplays_ndrvs", [])) for gpu in gpus)
        except (ValueError, AttributeError):
            count = None
    if count is None:
        report.add("display_attached", "接有显示器或 HDMI 欺骗器", "manual", "无法读取显示器信息")
    else:
        report.add(
            "display_attached",
            "接有显示器或 HDMI 欺骗器",
            "ok" if count else "fail",
            f"检测到 {count} 个显示器" if count else "没有检测到显示器，截图会失败",
            "不接显示器时插一个 HDMI 欺骗器（4K/60Hz 规格即可）",
        )


def service_clients(probe, backend_dir, settings):
    """Binaries that macOS may hold responsible for the backend's permission requests."""
    clients = {"python": probe.realpath(backend_dir / ".venv" / "bin" / "python")}
    clients["photo_helper"] = probe.realpath(photo_export_binary(probe, settings))
    if settings and settings.apple_binary:
        clients["apple_helper"] = probe.realpath(settings.apple_binary)
    else:
        clients["apple_helper"] = probe.realpath(
            backend_dir / "helpers" / "apple-sync" / ".build" / "release" / "apple-sync"
        )
    clients["screen_helper"] = None
    if settings:
        try:
            database = settings.data_dir / "assistant.sqlite3"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                row = conn.execute(
                    "SELECT config_json FROM plugins WHERE id='qq_screen'"
                ).fetchone()
            helper = json.loads(row[0]).get("helper") if row else None
            clients["screen_helper"] = probe.realpath(helper) if helper else None
        except (sqlite3.Error, ValueError):
            pass
    return {key: value for key, value in clients.items() if value}


def check_permissions(report, backend_dir, settings):
    probe = report.probe
    report.group = "系统权限"
    if not report.macos:
        for permission, title in PERMISSION_TITLES.items():
            report.mac_only(f"permission:{permission}", title)
        return
    clients = service_clients(probe, backend_dir, settings)
    python = clients.get("python", "backend/.venv/bin/python 的真实路径")
    for permission, (service, database, holders) in PERMISSIONS.items():
        title = PERMISSION_TITLES[permission]
        candidates = {clients[h] for h in holders if h in clients}
        # The screen helper asks for these permissions itself (it disclaims Python).
        holder = clients.get("screen_helper") if "screen_helper" in holders else None
        fix = (
            f"系统设置 → 隐私与安全性 → {title} → 点 + 添加 {holder or python}"
            "（在文件选择框里按 ⌘⇧G 粘贴路径）"
        )
        if permission in ("reminders", "calendar"):
            fix = "在 Mac 桌面的终端里运行一次 apple-sync，按提示允许（见 docs/mac-setup.md）"
        if permission == "photos":
            fix = "在 Mac 桌面的终端里运行 photo-export authorize（见 docs/webdav.md）"
            if clients.get("photo_helper"):
                candidates.add("local.assistant.photo-export")
        grants = probe.tcc_grants(database, service)
        if grants is None:
            report.add(
                f"permission:{permission}",
                title,
                "manual",
                "读不到权限数据库：给运行 doctor 的终端授予完全磁盘访问后可自动核对，"
                "或在系统设置里目视确认",
                fix,
            )
        elif candidates & grants:
            report.add(f"permission:{permission}", title, "ok", "已授权给后端进程")
        else:
            report.add(
                f"permission:{permission}",
                title,
                "fail",
                "后端使用的程序都没有这项权限" if candidates else "还没有安装后端（.venv 不存在）",
                fix,
            )


APPLE_SYNC = "~/Library/Application Support/AemeathBot/bin/apple-sync"


def icloud_state(probe, settings):
    """(status, detail, fix). Best from `apple-sync status`, which knows the account new
    reminders go to; without access granted yet, from the Apple account preferences (macOS 27
    no longer writes MobileMeAccounts, so their absence proves nothing)."""
    fix = "系统设置 → Apple 账户 → iCloud → 打开提醒事项和日历"
    binary = Path(settings.apple_binary if settings and settings.apple_binary else APPLE_SYNC)
    result = probe.run([str(binary.expanduser()), "status"], timeout=20)
    try:
        state = json.loads(result.out) if result and result.code == 0 else None
    except ValueError:
        state = None
    if state and all(
        state.get(k, {}).get("access") == "granted" for k in ("reminders", "calendar")
    ):
        accounts = {
            state[k].get("default_account", {}).get("type") for k in ("reminders", "calendar")
        }
        if accounts == {"caldav"}:
            names = "、".join(sorted({state[k]["default_account"]["title"] for k in state}))
            return "ok", f"提醒事项和日历默认存进 {names}，「AI 待办」会同步到 iPhone", fix
        return (
            "fail",
            "新的提醒事项或日程默认存在这台 Mac 上（不是 iCloud），不会同步到 iPhone",
            fix + "；在「提醒事项」「日历」App 的设置里把默认账户改成 iCloud",
        )
    account = probe.run(["defaults", "read", "MobileMeAccounts", "Accounts"])
    if account and account.code == 0 and "AccountID" in account.out:
        return "manual", "已登录 iCloud；请确认「提醒事项」「日历」的 iCloud 同步已打开", fix
    return (
        "manual",
        "还没法确认：apple-sync 还没得到提醒事项和日历权限，系统设置里也读不到 Apple 账户",
        "先按 docs/mac-setup.md 运行一次 apple-sync 并允许，再运行 doctor；" + fix,
    )


def check_accounts(report, backend_dir, settings):
    probe = report.probe
    report.group = "账号与服务"
    extra = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")

    claude = probe.which("claude", extra)
    if not claude:
        report.add(
            "claude",
            "Claude Code 已登录",
            "fail",
            "没有找到 claude",
            "按 docs/windows.md 安装 Claude Code"
            if report.windows
            else "brew install --cask claude-code",
        )
    else:
        result = probe.run([claude, "auth", "status", "--json"], timeout=30)
        try:
            logged_in = bool(json.loads(result.out).get("loggedIn")) if result else None
        except (ValueError, AttributeError):
            logged_in = None
        if logged_in is None:
            report.add(
                "claude",
                "Claude Code 已登录",
                "manual",
                "claude auth status 没有返回可识别的结果",
                "claude auth login",
            )
        else:
            report.add(
                "claude",
                "Claude Code 已登录",
                "ok" if logged_in else "fail",
                "已登录" if logged_in else "未登录",
                "claude auth login",
            )

    codex = probe.which("codex", extra)
    if not codex:
        report.add(
            "codex",
            "Codex 已登录",
            "fail",
            "没有找到 codex",
            "npm install -g @openai/codex" if report.windows else "brew install --cask codex",
        )
    else:
        result = probe.run([codex, "login", "status"], timeout=30)
        report.add(
            "codex",
            "Codex 已登录",
            "ok" if result and result.code == 0 else "fail",
            "已登录" if result and result.code == 0 else "未登录",
            "codex login",
        )

    port = settings.port if settings else 8765
    tailscale = probe.which("tailscale", ("/usr/local/bin", "/opt/homebrew/bin"))
    if not tailscale and report.windows:
        candidate = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale/tailscale.exe"
        )
        if probe.exists(candidate):
            tailscale = str(candidate)
    if (
        not tailscale
        and report.macos
        and probe.exists("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    ):
        tailscale = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if not tailscale:
        report.add(
            "tailscale",
            "Tailscale 已连接",
            "fail",
            "没有找到 tailscale",
            "brew install --cask tailscale-app"
            if report.macos
            else "按 https://tailscale.com/download 安装",
        )
        report.add(
            "tailscale_serve",
            "tailscale serve 提供 HTTPS",
            "fail",
            "Tailscale 未安装",
            f"tailscale serve --bg --https=443 http://127.0.0.1:{port}",
        )
    else:
        state = probe.run([tailscale, "status", "--json"])
        try:
            backend_state = json.loads(state.out).get("BackendState") if state else None
        except (ValueError, AttributeError):
            backend_state = None
        report.add(
            "tailscale",
            "Tailscale 已连接",
            "ok" if backend_state == "Running" else "fail",
            f"状态 {backend_state or '未知'}",
            "打开 Tailscale 登录，或 tailscale up",
        )
        serve = probe.run([tailscale, "serve", "status", "--json"])
        try:
            config = json.loads(serve.out or "{}") if serve and serve.code == 0 else None
        except ValueError:
            config = None
        fix = f"tailscale serve --bg --https=443 http://127.0.0.1:{port}"
        if config is None:
            report.add(
                "tailscale_serve", "tailscale serve 提供 HTTPS", "manual", "读不到 serve 配置", fix
            )
        else:
            targets = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"127.0.0.1:{port}"}
            served = [
                host
                for host, web in (config.get("Web") or {}).items()
                for handler in (web.get("Handlers") or {}).values()
                if handler.get("Proxy", "").rstrip("/") in targets
            ]
            funnel = [host for host, on in (config.get("AllowFunnel") or {}).items() if on]
            if funnel:
                report.add(
                    "tailscale_serve",
                    "tailscale serve 提供 HTTPS",
                    "fail",
                    "开启了 Funnel，后端暴露到了公网",
                    "立即运行 tailscale funnel reset，只用 tailscale serve",
                )
            else:
                report.add(
                    "tailscale_serve",
                    "tailscale serve 提供 HTTPS",
                    "ok" if served else "fail",
                    f"https://{served[0].removesuffix(':443')}/ → 127.0.0.1:{port}"
                    if served
                    else f"没有把 HTTPS 转发到 127.0.0.1:{port}",
                    fix,
                )

    if report.mac_only("launchd", "launchd 常驻服务"):
        result = probe.run(["launchctl", "print", f"gui/{probe.uid}/{LABEL}"])
        if not result or result.code != 0:
            report.add(
                "launchd",
                "launchd 常驻服务",
                "fail",
                "服务没有注册",
                "bash backend/scripts/install_mac.sh",
            )
        else:
            running = "state = running" in result.out
            report.add(
                "launchd",
                "launchd 常驻服务",
                "ok" if running else "fail",
                "正在运行" if running else "已注册但没有运行，看 .state/logs/service-error.log",
                f"launchctl kickstart -k gui/$(id -u)/{LABEL}",
            )

    if settings is None:
        report.add("backend_api", "后端 HTTP 接口", "fail", "配置无效，无法检查")
    else:
        token = settings.token.get_secret_value()
        status = probe.http_status(f"{settings.base_url}/api/v1/status", token)
        detail = {
            200: f"{settings.base_url} 正常响应",
            401: "后端在运行，但 .env 的 token 和运行中的服务不一致；重启服务",
            None: f"{settings.base_url} 无法连接",
        }.get(status, f"HTTP {status}")
        report.add(
            "backend_api",
            "后端 HTTP 接口",
            "ok" if status == 200 else "fail",
            detail,
            f"launchctl kickstart -k gui/$(id -u)/{LABEL}"
            if report.macos
            else "cd backend && uv run python -m core",
        )

    for name, title, app, process, optional in (
        ("qq_login", "QQ 保持登录", "/Applications/QQ.app", "QQ", False),
        (
            "wechat_login",
            "微信保持登录（QQ 稳定后再接）",
            "/Applications/WeChat.app",
            "WeChat",
            True,
        ),
    ):
        if report.windows or not report.mac_only(name, title, optional):
            continue
        if not probe.exists(app):
            report.add(name, title, "fail", f"没有安装 {app}", "从官网安装并登录", optional)
            continue
        running = probe.run(["pgrep", "-x", process])
        if running and running.code == 0:
            report.add(name, title, "manual", "正在运行；是否已登录请看一眼窗口", optional=optional)
        else:
            report.add(name, title, "fail", "没有运行", f"打开 {process} 并登录", optional)

    model = None
    for url, server in (
        ("http://127.0.0.1:1234/v1/models", "LM Studio"),
        ("http://127.0.0.1:11434/api/tags", "Ollama"),
    ):
        if probe.http_status(url) == 200:
            model = server
            break
    report.add(
        "local_model",
        "本地模型服务（阶段三用）",
        "ok" if model else "fail",
        f"{model} 正在提供 OpenAI 兼容接口"
        if model
        else "LM Studio（1234）和 Ollama（11434）都没有响应",
        "安装 LM Studio 或 Ollama 并设为登录时启动；阶段三之前可以先不装",
        optional=True,
    )

    if report.mac_only("icloud", "iCloud 同步提醒事项和日历"):
        report.add("icloud", "iCloud 同步提醒事项和日历", *icloud_state(probe, settings))

    report.group = "数据备份"
    if settings is None:
        report.add("backups", "每日备份", "fail", "配置无效，无法检查")
    else:
        newest = probe.newest_backup(settings.data_dir)
        if newest is None:
            report.add("backups", "每日备份", "manual", "还没有完整备份；后端每天 03:00 自动备份")
        else:
            age_hours = (probe.now() - newest) / 3600
            report.add(
                "backups",
                "每日备份",
                "ok" if age_hours <= 48 else "fail",
                f"最近一次完整备份在 {age_hours:.0f} 小时前",
                "确认后端在运行；手动备份：curl -X POST …/api/v1/jobs/backup/run",
            )


def check_videos(report, settings):
    """Optional local video tools; never run a downloader or fetch a model in doctor."""
    from plugins.video_notes.media import setup_issues

    report.group = "视频资料（可选）"
    config = {}
    if settings:
        database = settings.data_dir / "assistant.sqlite3"
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                row = conn.execute(
                    "SELECT config_json FROM plugins WHERE id='video_notes' AND enabled=1 AND deleted=0"
                ).fetchone()
            if row is None:
                return
            config = json.loads(row[0])
        except (sqlite3.Error, ValueError):
            return
    else:
        return
    issues = setup_issues(config, report.probe.which)
    report.add(
        "video_notes",
        "视频下载与本地识别",
        "manual" if issues else "ok",
        "；".join(issues) if issues else "命令和本地模型已存在（未执行识别）",
        "按 docs/video-notes.md 安装后重新运行；有字幕的视频不需要语音模型",
        optional=True,
    )


def check_finance(report, settings):
    """Read-only inspection of the enabled plugin's topic and persisted ACL."""
    if not settings:
        return
    from core.knowledge_access import safe_path

    database = settings.data_dir / "assistant.sqlite3"
    if not database.is_file():
        return
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT config_json FROM plugins WHERE id='finance' AND enabled=1 AND deleted=0"
            ).fetchone()
            if row is None:
                return
            topic = json.loads(row[0]).get("topic", "财务")
            from core.knowledge import Knowledge

            Knowledge.validate_topic(topic)
            root = settings.vault_dir or settings.data_dir / "vault"
            path = safe_path(root.resolve(), "知识库/" + topic)
            acl = conn.execute(
                "SELECT readers FROM knowledge_areas WHERE path=?", ("知识库/" + topic,)
            ).fetchone()
            valid = path.is_dir() and acl is not None and json.loads(acl[0]) == []
    except (sqlite3.Error, ValueError, OSError):
        valid = False
    report.group = "记账"
    report.add(
        "finance_topic",
        "财务私人主题",
        "ok" if valid else "fail",
        "主题存在且没有读者" if valid else "财务主题必须没有读者，且主题必须存在",
        "在知识库中创建配置的主题，并把读者清空；不要给任何 Bot 或 Agent 勾选",
    )


def photo_export_binary(probe, settings):
    binary = probe.home / "Library/Application Support/AemeathBot/bin/photo-export"
    if settings:
        try:
            database = settings.data_dir / "assistant.sqlite3"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                row = conn.execute(
                    "SELECT config_json FROM plugins WHERE id='class_photos'"
                ).fetchone()
            if row and json.loads(row[0]).get("helper"):
                binary = Path(json.loads(row[0])["helper"]).expanduser()
        except (sqlite3.Error, ValueError):
            pass
    return binary


def check_photo_export(report, settings):
    report.group = "系统权限"
    if not report.mac_only("photo_export", "课堂照片 photo-export"):
        return
    probe = report.probe
    binary = photo_export_binary(probe, settings)
    fix = (
        "打开 iCloud 照片；在桌面终端运行 photo-export authorize，允许读取照片（见 docs/webdav.md）"
    )
    if not probe.exists(binary):
        report.add(
            "photo_export",
            "课堂照片 photo-export",
            "fail",
            "未安装",
            "bash backend/scripts/install_mac.sh",
        )
        return
    result = probe.run([str(binary), "status"])
    try:
        permission = (
            json.loads(result.out)["permission"] if result and result.code == 0 else "unavailable"
        )
    except (ValueError, KeyError, TypeError):
        permission = "unavailable"
    report.add(
        "photo_export",
        "课堂照片 photo-export",
        "ok" if permission in {"authorized", "limited"} else "fail",
        "照片权限：" + str(permission),
        fix,
    )


def check_environment(probe=None, backend_dir=BACKEND, settings=None):
    probe = probe or SystemProbe()
    report = Report(probe)
    settings_error = ""
    if settings is None:
        try:
            settings = load_settings(backend_dir)
        except Exception:
            settings_error = "缺少 .env、ASSISTANT_TOKEN 少于 32 位，或监听地址不是本机/Tailscale"
    check_basics(report, backend_dir, settings, settings_error)
    check_system(report)
    if probe.platform == "win32":
        from scripts.windows_doctor import check_windows

        check_windows(report, backend_dir)
    check_permissions(report, backend_dir, settings)
    check_photo_export(report, settings)
    check_accounts(report, backend_dir, settings)
    check_videos(report, settings)
    check_finance(report, settings)
    checks = [check.as_dict() for check in report.checks]
    summary = {status: 0 for status in STATUS_TITLES}
    for check in checks:
        summary[check["status"]] += 1
    required = [c for c in checks if not c["optional"]]
    return {
        "platform": probe.platform,
        "checks": checks,
        "summary": summary,
        # ready: nothing required failed; verified: and nothing required is left to look at.
        "ready": not any(c["status"] == "fail" for c in required),
        "verified": not any(c["status"] in {"fail", "manual"} for c in required),
    }


def render(report):
    lines = [f"24 小时助手环境自检（{report['platform']}）"]
    for status in ("ok", "fail", "manual", "not_applicable"):
        items = [c for c in report["checks"] if c["status"] == status]
        if not items:
            continue
        lines.append("")
        lines.append(f"{STATUS_TITLES[status]}（{len(items)}）")
        for check in items:
            optional = "［可选］" if check["optional"] else ""
            detail = f"：{check['detail']}" if check["detail"] else ""
            lines.append(f"  {STATUS_MARKS[status]} {check['title']}{optional}{detail}")
            if status in {"fail", "manual"} and check["fix"]:
                lines.append(f"      → {check['fix']}")
    required = [c for c in report["checks"] if not c["optional"]]
    failed = sum(1 for c in required if c["status"] == "fail")
    manual = sum(1 for c in required if c["status"] == "manual")
    lines.append("")
    if failed:
        lines.append(f"结论：未就绪，{failed} 项未通过，{manual} 项需人工确认（可选项不计）。")
    elif manual:
        lines.append(f"结论：自动检查全部通过，还有 {manual} 项需要你目视确认。")
    else:
        lines.append("结论：全部通过。")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="后端运行环境自检（只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict", action="store_true", help="需人工确认的项目也算失败")
    args = parser.parse_args(argv)
    report = check_environment()
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    if not report["ready"] or (args.strict and not report["verified"]):
        raise SystemExit(1)


if __name__ == "__main__":
    # Also support the `python scripts/doctor.py` entrypoint.
    sys.path.insert(0, str(BACKEND))
    main()
