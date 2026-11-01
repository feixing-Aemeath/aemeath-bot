"""macOS implementation of ScreenCapture and TextRecognizer: a locally installed helper.

The helper is one executable speaking JSON over stdin/stdout, one request per process:

  {"action": "preview"}                          -> screenshot
  {"action": "capture_chat", "chat": "…", "page": 0} -> screenshot (+ "chat": confirmed title)
  {"action": "capture_announcement", "chat": "…"} -> screenshot, or {"screenshot": null}
  {"action": "capture_page", "title": "…"}       -> screenshot of the browser window whose
                                                     title contains "…" (app "chrome")
  {"action": "recognize", "path": "/…/x.png"}    -> {"blocks": [{"text","x","y","w","h",
                                                     "confidence"}]}

  screenshot = {"path": "/…/x.png", "captured_at": "ISO 8601 with offset",
                "chat": "…" | null, "at_top": false}

Every request also names the app ("qq" / "wechat" / "chrome") and the folder for screenshots
("out_dir", the plugin's data folder). A failed request exits non-zero and prints
{"error": "screen_…"}; a capture that fails while the Mac's screen is locked is reported as
screen_locked, since only unlocking helps.

The helper is backend/helpers/screen-helper (Swift): it finds the conversation list and the
chat area from the window's own dividers, captures them with ScreenCaptureKit and runs Apple
Vision locally. Only capture_chat touches the app: it clicks the chat's row in the list and
scrolls the chat. It must not type, send, add friends or like (see docs/screen-reading.md).
capture_page only captures the browser window of a page the user keeps open (the timetable).
"""

import asyncio
import json
import plistlib
import re
import sys
import weakref
from datetime import datetime
from pathlib import Path

from core import helper_process
from plugins.screen.model import Screenshot, TextBlock

ACTIONS = {
    "preview",
    "capture_chat",
    "capture_announcement",
    "capture_page",
    "recognize",
    "permissions",
    "layout",
}
IOREG = "/usr/sbin/ioreg"
_locks = weakref.WeakKeyDictionary()


async def screen_is_locked():
    """Whether the Mac's screen is locked. ScreenCaptureKit then captures nothing, so every read
    fails until the user unlocks; with 「需要密码」 on in 系统设置 → 锁定屏幕 the display turning
    off at night locks it. False when it cannot tell."""
    try:
        code, output = await helper_process.run([IOREG, "-n", "Root", "-d1", "-a"], None, 10)
        root = plistlib.loads(output) if code == 0 else {}
    except (OSError, TimeoutError, ValueError, plistlib.InvalidFileException):
        return False
    users = root.get("IOConsoleUsers") or []
    return bool(root.get("IOConsoleLocked")) or any(
        isinstance(user, dict) and user.get("CGSSessionScreenIsLocked") for user in users
    )


def screen_lock():
    """One helper at a time, across plugins: when the QQ read and the timetable (both at :00
    and :30) captured the screen at once, one of them hung until its timeout every time."""
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks[loop] = asyncio.Lock()
    return _locks[loop]


class JsonHelper:
    def __init__(self, executable: Path | None = None, timeout=30, app=None, out_dir=None):
        self.executable, self.timeout = executable, timeout
        self.app, self.out_dir = app, out_dir

    async def call(self, payload, timeout=None):
        if payload.get("action") not in ACTIONS:
            raise ValueError("screen_action_not_allowed")
        if sys.platform not in {"darwin", "win32"}:
            raise RuntimeError("macos_required")
        argv = (
            [sys.executable, "-m", "plugins.screen.windows_helper"]
            if sys.platform == "win32" and self.executable is None
            else [str(self.executable)]
        )
        payload = {
            **payload,
            "app": self.app,
            "out_dir": str(self.out_dir) if self.out_dir else None,
        }
        async with screen_lock():
            try:
                returncode, output = await helper_process.run(
                    argv,
                    json.dumps(payload, ensure_ascii=False).encode(),
                    timeout or self.timeout,
                    **(
                        {"cwd": Path(__file__).resolve().parents[2]}
                        if sys.platform == "win32"
                        else {}
                    ),
                )
            except TimeoutError:
                raise RuntimeError("screen_helper_timeout") from None
        if returncode:
            try:
                error = json.loads(output).get("error", "")
            except (ValueError, AttributeError):
                error = ""
            # Only the helper's fixed codes, e.g. screen_window_not_found, never free text.
            code = error if re.fullmatch(r"screen_[a-z_]{1,40}", error) else "screen_helper_failed"
            if (
                sys.platform == "darwin"
                and payload["action"] != "recognize"
                and await screen_is_locked()
            ):
                code = "screen_locked"
            raise RuntimeError(code)
        return json.loads(output)


def screenshot_from(data, kind, page=0):
    return Screenshot(
        path=str(data["path"]),
        kind=kind,
        captured_at=datetime.fromisoformat(data["captured_at"]),
        chat=data.get("chat"),
        page=page,
        at_top=bool(data.get("at_top", False)),
    )


# The first Vision recognition in a new process loads the Chinese model, which took over 30 s
# on the Mac mini; capture_chat runs Vision too (it reads the chat title), plus clicks and waits.
SLOW = 120


class HelperScreenCapture:
    def __init__(self, helper: JsonHelper):
        self.helper = helper

    async def preview(self):
        return screenshot_from(await self.helper.call({"action": "preview"}), "preview")

    async def capture_chat(self, chat, page):
        data = await self.helper.call(
            {"action": "capture_chat", "chat": chat, "page": page}, timeout=SLOW
        )
        return screenshot_from(data, "chat", page)

    async def capture_announcement(self, chat):
        data = await self.helper.call({"action": "capture_announcement", "chat": chat})
        if not data or data.get("screenshot", data) is None:
            return None
        return screenshot_from(data, "announcement")

    async def capture_page(self, title):
        """The browser window whose title contains `title`; it is not clicked or scrolled."""
        data = await self.helper.call({"action": "capture_page", "title": title})
        return screenshot_from(data, "page")


class HelperTextRecognizer:
    def __init__(self, helper: JsonHelper):
        self.helper = helper

    async def recognize(self, screenshot):
        data = await self.helper.call(
            {"action": "recognize", "path": screenshot.path}, timeout=SLOW
        )
        return [TextBlock.from_dict(block) for block in data.get("blocks", [])]
