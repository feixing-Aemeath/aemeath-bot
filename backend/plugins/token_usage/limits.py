"""Claude's and Codex's 5-hour and weekly usage limits, the numbers their own apps show.

Codex: a short-lived `codex app-server` answers account/rateLimits/read live without using any
tokens (the Codex app and menu bar tools such as CodexBar and iCodex's Mac helper read it the
same way). When that fails, the last rate_limits a Codex session wrote into its rollout file
stand in, marked with their time.

Claude: Claude Code's /usage asks GET api.anthropic.com/api/oauth/usage with the OAuth token
Claude Code keeps in the login Keychain (item "Claude Code-credentials", read through
/usr/bin/security, which Claude Code uses to store it). The endpoint is undocumented, so fields
it does not know are skipped. The token only goes to api.anthropic.com; it is never stored,
logged or refreshed here (a refresh would replace Claude Code's own login). Claude Code renews
its login whenever it runs, so when the token has expired the plugin runs the smallest request
Claude Code can make (renew_claude), at most every half hour, and reads again.
"""

import contextlib
import json
import os
import queue
import selectors
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from core import helper_process
from core.platforms import command_argv, find_command

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_KEYCHAIN_ITEM = "Claude Code-credentials"
SECURITY = "/usr/bin/security"
TIMEOUT = 20
MODEL_NAMES = {"opus": "Opus", "sonnet": "Sonnet", "fable": "Fable"}
PLANS = {"plus": "Plus", "pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise"}


class LimitError(Exception):
    """A fixed code (codex_timeout, claude_http_500…), never anything secret."""


def window_name(minutes):
    if minutes == 300:
        return "5 小时"
    if minutes == 7 * 24 * 60:
        return "本周"
    if minutes % (24 * 60) == 0:
        return f"{minutes // (24 * 60)} 天"
    return f"{minutes // 60} 小时" if minutes % 60 == 0 else f"{minutes} 分钟"


def window(name, used, resets_at, minutes):
    return {
        "name": name,
        "used_percent": round(float(used), 1),
        "resets_at": resets_at,
        "minutes": minutes,
    }


def epoch(text):
    """Seconds since 1970 from an ISO time (with or without fractions), or None."""
    if not isinstance(text, str):
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


# Codex ---------------------------------------------------------------------------------------


def codex_windows(snapshot):
    """Windows from a rate-limit snapshot: app-server (camelCase) or a session file (snake_case)."""
    windows = []
    for key in ("primary", "secondary"):
        part = snapshot.get(key)
        if not isinstance(part, dict):
            continue
        used = part.get("usedPercent", part.get("used_percent"))
        minutes = part.get("windowDurationMins", part.get("window_minutes"))
        resets = part.get("resetsAt", part.get("resets_at"))
        if isinstance(used, int | float) and isinstance(minutes, int) and minutes > 0:
            resets = int(resets) if isinstance(resets, int | float) else None
            windows.append(window(window_name(minutes), used, resets, minutes))
    return sorted(windows, key=lambda w: w["minutes"])


def plan_name(plan):
    return PLANS.get(plan, plan.title()) if isinstance(plan, str) and plan else None


def json_lines(stream, deadline):
    """JSON objects from a pipe, one per line, until `deadline` (time.monotonic)."""
    if sys.platform == "win32":
        # Windows selectors accept sockets only, not subprocess pipes.
        lines = queue.Queue()

        def read():
            try:
                for line in stream:
                    lines.put(line)
            except (OSError, ValueError):
                pass  # The timeout cleanup may close the pipe while this thread reads it.
            finally:
                lines.put(None)

        threading.Thread(target=read, daemon=True).start()
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                return
            if line is None:
                return
            with contextlib.suppress(ValueError):
                yield json.loads(line)
        return
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)
    buffer = b""
    try:
        while (left := deadline - time.monotonic()) > 0:
            if not selector.select(left):
                return
            chunk = os.read(stream.fileno(), 65536)
            if not chunk:
                return
            *lines, buffer = (buffer + chunk).split(b"\n")
            for line in lines:
                with contextlib.suppress(ValueError):
                    yield json.loads(line)
    finally:
        selector.close()


def codex_live(executable=None, timeout=TIMEOUT):
    """account/rateLimits/read from a `codex app-server` started for this one question."""
    executable = executable or find_command("codex")
    if not executable:
        raise LimitError("codex_not_found")
    process = subprocess.Popen(
        command_argv([executable, "app-server"]),
        **helper_process.process_options(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout
    replies = json_lines(process.stdout, deadline)

    def send(*messages):
        process.stdin.write(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
        process.stdin.flush()

    def answer(request_id):
        for message in replies:
            if message.get("id") == request_id:
                if "result" not in message:
                    raise LimitError("codex_request_failed")
                return message["result"]
        raise LimitError("codex_timeout")

    try:
        helper_process.attach_job(process)
        client = {"name": "aemeath_bot", "version": "0.1.0"}
        send({"id": 1, "method": "initialize", "params": {"clientInfo": client}})
        answer(1)
        send({"method": "initialized"}, {"id": 2, "method": "account/rateLimits/read"})
        return answer(2)
    except OSError as error:
        raise LimitError("codex_app_server_failed") from error
    finally:
        helper_process.stop_sync(process)
        replies.close()
        process.stdin.close()
        process.stdout.close()


def codex_from_sessions(dirs, newest=30):
    """(time, snapshot) of the newest rate_limits in Codex rollout files, or None."""
    files = []
    for folder in dirs:
        root = Path(folder).expanduser()
        if root.is_dir():
            files.extend(root.glob("**/rollout-*.jsonl"))
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[:newest]:
        found = None
        with contextlib.suppress(OSError), path.open(encoding="utf-8") as lines:
            for line in lines:
                if '"rate_limits"' not in line:
                    continue
                try:
                    event = json.loads(line)
                    snapshot = event["payload"]["rate_limits"]
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(snapshot, dict):
                    found = (epoch(event.get("timestamp")), snapshot)
        if found:
            return found
    return None


def read_codex(session_dirs, live=codex_live, now=time.time):
    try:
        result = live()
        snapshot = result.get("rateLimits") or {}
        windows = codex_windows(snapshot)
        if windows:
            return {
                "status": "ok",
                "source": "live",
                "plan": plan_name(snapshot.get("planType")),
                "windows": windows,
                "as_of": int(now()),
            }
        error = "codex_no_limits"
    except LimitError as failure:
        error = str(failure)
    found = codex_from_sessions(session_dirs)
    if found and (windows := codex_windows(found[1])):
        return {
            "status": "ok",
            "source": "session",
            "plan": plan_name(found[1].get("plan_type")),
            "windows": windows,
            "as_of": found[0],
            "error": error,
        }
    raise LimitError(error)


# Claude --------------------------------------------------------------------------------------


def keychain_secret(item=CLAUDE_KEYCHAIN_ITEM):
    """Claude Code's stored login (JSON text), or None when it is not logged in here."""
    if sys.platform == "win32":
        from core.credentials import windows_secret

        value = windows_secret(item)
        if value:
            return value
        try:
            # Claude Code also stores its own login here on Windows. Read it locally;
            # token_usage never refreshes or rewrites this file.
            return (Path.home() / ".claude/.credentials.json").read_text(encoding="utf-8")
        except OSError:
            return None
    if not os.path.exists(SECURITY):
        return None
    try:
        done = subprocess.run(
            [SECURITY, "find-generic-password", "-s", item, "-w"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise LimitError("keychain_timeout") from error
    return done.stdout.decode().strip() if done.returncode == 0 else None


def http_get(url, headers):
    response = httpx.get(url, headers=headers, timeout=TIMEOUT)
    return response.status_code, (response.json() if response.status_code == 200 else None)


def claude_windows(body):
    """five_hour, seven_day, then per-model weekly windows (seven_day_opus…) with a value."""
    found = []
    for key, value in body.items():
        if not isinstance(value, dict) or not isinstance(value.get("utilization"), int | float):
            continue
        if key == "five_hour":
            rank, name, minutes = 0, "5 小时", 300
        elif key == "seven_day":
            rank, name, minutes = 1, "本周", 7 * 24 * 60
        elif key.startswith("seven_day_"):
            model = key.removeprefix("seven_day_")
            rank, name, minutes = 2, f"本周 · {MODEL_NAMES.get(model, model.title())}", 7 * 24 * 60
        else:
            continue
        found.append(
            (rank, key, window(name, value["utilization"], epoch(value.get("resets_at")), minutes))
        )
    return [entry for *_, entry in sorted(found, key=lambda f: f[:2])]


def read_claude(secret=keychain_secret, get=http_get, now=time.time):
    text = secret()
    try:
        login = json.loads(text)["claudeAiOauth"] if text else None
        token = login["accessToken"] if login else None
    except (ValueError, KeyError, TypeError):
        token = None
    if not token:
        return {"status": "login_needed"}
    plan = plan_name(login.get("subscriptionType"))
    expires = login.get("expiresAt")
    if isinstance(expires, int | float) and expires / 1000 <= now():
        return {"status": "expired", "plan": plan}
    headers = {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"}
    try:
        status, body = get(CLAUDE_USAGE_URL, headers)
    except (httpx.HTTPError, ValueError) as error:
        raise LimitError("claude_request_failed") from error
    if status in (401, 403):
        return {"status": "expired", "plan": plan}
    if status != 200 or not isinstance(body, dict):
        raise LimitError(f"claude_http_{status}")
    windows = claude_windows(body)
    if not windows:
        raise LimitError("claude_no_limits")
    return {"status": "ok", "source": "live", "plan": plan, "windows": windows, "as_of": int(now())}


# One word under a one-line system prompt, without tools, MCP servers, settings or a session
# file: a handful of tokens. Claude Code refreshes and stores its own login on the way; nothing
# here sees the token.
CLAUDE_RENEW = (
    "-p", "ok", "--model", "sonnet", "--system-prompt", "Reply with the single word ok.",
    "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
)  # fmt: skip
RENEW_EVERY = 30 * 60


def renew_claude(run=subprocess.run):
    """Let Claude Code renew its expired login by running it once; True when it finished."""
    executable = find_command("claude")
    if not executable:
        return False
    try:
        if sys.platform == "win32" and run is subprocess.run:
            return (
                helper_process.run_sync(
                    [executable, *CLAUDE_RENEW],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=90,
                    cwd=Path.home(),
                ).returncode
                == 0
            )
        result = run(
            command_argv([executable, *CLAUDE_RENEW]),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=90,
            cwd=Path.home(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def read_claude_renewing(last_renewal, read=None, renew=None, now=time.time):
    """read_claude, renewing an expired login first when the last try is half an hour old.
    Returns (entry, time of this renewal or the last one)."""
    read, renew = read or read_claude, renew or renew_claude
    entry = read()
    if entry["status"] != "expired" or now() - (last_renewal or 0) < RENEW_EVERY:
        return entry, last_renewal
    if renew():
        entry = read()
    return entry, now()


# Stored state and cards ----------------------------------------------------------------------


def update(previous, read, now=time.time):
    """The new stored entry; a failed read keeps the last numbers it had, marked stale."""
    try:
        entry = read()
    except LimitError as error:
        entry = {"status": "failed", "error": str(error)}
    entry["checked_at"] = int(now())
    if entry["status"] != "ok" and previous and previous.get("windows"):
        entry.update(
            windows=previous["windows"],
            as_of=previous.get("as_of"),
            plan=entry.get("plan") or previous.get("plan"),
            stale=True,
        )
    return entry


def percent(value):
    return f"{value:.0f}%" if value == int(value) else f"{value:.1f}%"


def clock(moment, zone, now):
    """ "14:20", "明天 14:20", "周五 14:20" (within a week) or "10月30日 14:20"."""
    local, today = datetime.fromtimestamp(moment, zone), datetime.fromtimestamp(now, zone).date()
    days = (local.date() - today).days
    time_text = local.strftime("%H:%M")
    if days == 0:
        return time_text
    if days == 1:
        return f"明天 {time_text}"
    if 1 < days < 7:
        return f"周{'一二三四五六日'[local.weekday()]} {time_text}"
    return f"{local.month}月{local.day}日 {time_text}"


def window_item(entry, zone, now):
    """A window's row with a bar of what was used; one whose reset time has passed no longer
    says what was used."""
    resets_at = entry["resets_at"]
    if resets_at is not None and resets_at <= now:
        return {
            "title": entry["name"],
            "subtitle": "新的用量等下次读取",
            "detail": "已重置",
            "progress": {"value": 0, "max": 100},
        }
    return {
        "title": entry["name"],
        "subtitle": f"{clock(resets_at, zone, now)} 重置" if resets_at is not None else None,
        "detail": f"已用 {percent(entry['used_percent'])}",
        "progress": bar(entry["used_percent"]),
    }


# A window this full is drawn in orange, then red (the list row's progress `tone`).
WARN_PERCENT, ALERT_PERCENT = 80, 95


def bar(used):
    progress = {"value": min(max(used, 0), 100), "max": 100}
    if used >= ALERT_PERCENT:
        progress["tone"] = "alert"
    elif used >= WARN_PERCENT:
        progress["tone"] = "warn"
    return progress


TITLES = {"claude": "Claude 用量限额", "codex": "Codex 用量限额"}
STATUS_NOTES = {
    "login_needed": ("还没登录 Claude Code", "在终端运行 claude 登录后，这里显示 5 小时和每周限额"),
    "expired": ("Claude Code 的登录已过期", "自动续登录没有成功；在终端运行一次 claude 就能续上"),
}


FAILURES = {
    "codex_not_found": "本机找不到 codex",
    "codex_timeout": "codex 没有及时回答",
    "keychain_timeout": "钥匙串没有回应",
    "claude_request_failed": "连不上 Anthropic",
}


def failure_text(error):
    if error.startswith("claude_http_"):
        return f"Anthropic 返回 {error.removeprefix('claude_http_')}"
    return FAILURES.get(error, "读取出错")


def limit_card(provider, entry, zone, now):
    plan = entry.get("plan")
    title = TITLES[provider] + (f" · {plan}" if plan else "")
    items = [window_item(w, zone, now) for w in entry.get("windows", [])]
    as_of = entry.get("as_of")
    since = f"{clock(as_of, zone, now)} 的数" if as_of else "以前的数"
    if entry["status"] in STATUS_NOTES:
        note, hint = STATUS_NOTES[entry["status"]]
        items.append({"title": note, "subtitle": f"上面是 {since}" if items else hint})
    elif entry["status"] == "failed":
        why = failure_text(entry.get("error", ""))
        items.append(
            {"title": "这次没读到", "subtitle": f"{why}；上面是 {since}" if items else why}
        )
    elif entry.get("source") == "session":
        items.append({"title": f"来自 {since}", "subtitle": "实时读取失败，用的是 Codex 会话记录"})
    if not items:
        return None
    return {"type": "list", "title": title, "data": {"items": items}}


def limit_cards(state, zone, now=None):
    now = time.time() if now is None else now
    cards = []
    for provider in ("claude", "codex"):
        if provider in state and (card := limit_card(provider, state[provider], zone, now)):
            cards.append(card)
    return cards
