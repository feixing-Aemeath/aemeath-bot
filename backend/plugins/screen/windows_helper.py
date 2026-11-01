"""Windows implementation of the screen-helper JSON protocol, one request per process.

Run: python -m plugins.screen.windows_helper < request.json. Only capture_chat sends input;
the requested chat must be in the visible list and its title must match after opening it.
"""

import asyncio
import hashlib
import json
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from core.platforms import is_link
from plugins.screen.layout import normalize
from plugins.screen.regions import Regions

ACTIONS = {
    "preview",
    "capture_chat",
    "capture_announcement",
    "capture_page",
    "recognize",
    "permissions",
    "layout",
}
ERRORS = {
    "screen_invalid_request",
    "screen_unknown_app",
    "screen_app_not_running",
    "screen_window_not_found",
    "screen_recording_denied",
    "screen_accessibility_denied",
    "screen_input_refused",
    "screen_capture_failed",
    "screen_chat_not_in_list",
    "screen_chat_not_open",
    "screen_page_not_open",
    "screen_locked",
    "screen_ocr_language_missing",
    "screen_ocr_image_too_large",
}


def title_match(chat, lines):
    candidates = [*lines, "".join(lines)]
    return next(
        (
            re.sub(r"\s*[(（]\d{1,5}[)）]\s*$", "", line)
            for line in candidates
            if normalize(re.sub(r"\s*[(（]\d{1,5}[)）]\s*$", "", line)) == normalize(chat)
        ),
        None,
    )


def row_match(chat, line):
    key, name = normalize(chat), normalize(line)
    if key == name:
        return True
    if name.startswith(key):
        return bool(
            re.fullmatch(
                r"\d{3,4}|昨天|前天|星期.|周.|\d{1,2}月\d{1,2}日|\d{6,8}", name[len(key) :]
            )
        )
    # A clipped prefix may locate a row, but the full header must still match after clicking.
    return len(name) >= 4 and key.startswith(name)


class Output:
    def __init__(self, directory=None):
        self.directory = (
            Path(directory) if directory else Path(tempfile.gettempdir()) / "aemeath-screen"
        )
        if not self.directory.is_absolute():
            raise RuntimeError("screen_invalid_request")
        self.directory.mkdir(parents=True, exist_ok=True)
        if any(is_link(path) for path in (self.directory, *self.directory.parents)):
            raise RuntimeError("screen_invalid_request")
        self.directory = self.directory.resolve()

    def contains(self, path):
        path = Path(path)
        return (
            path.is_absolute()
            and not is_link(path)
            and path.suffix.lower() == ".png"
            and path.resolve().parent == self.directory
        )

    def save(self, image, name):
        for path in self.directory.glob("*.png"):
            if not is_link(path) and path.stat().st_mtime < time.time() - 7 * 86400:
                path.unlink(missing_ok=True)
        path = self.directory / f"{name}-{uuid4().hex}.png"
        image.save(path, format="PNG")
        return str(path)

    def screenshot(self, image, app, *, chat=None, page=0):
        path = self.save(image, app)
        at_top = False
        if chat is not None:
            key = hashlib.sha256(f"{app}:{normalize(chat)}".encode()).hexdigest()
            marker = self.directory / f".last-{key}"
            if is_link(marker):
                raise RuntimeError("screen_invalid_request")
            gray = image.convert("L").tobytes()
            fingerprint = hashlib.sha256(
                bytes(v // 8 for v in gray[:: max(1, len(gray) // 4096)])
            ).hexdigest()
            at_top = page > 0 and marker.exists() and marker.read_text() == fingerprint
            marker.write_text(fingerprint, encoding="ascii")
        return {
            "path": path,
            "captured_at": datetime.now().astimezone().isoformat(),
            "chat": chat,
            "at_top": at_top,
        }


class WindowsHelper:
    def __init__(self, desktop, ocr, *, pause=asyncio.sleep):
        self.desktop, self.ocr, self.pause = desktop, ocr, pause

    def shot(self, app, title=None):
        windows = self.desktop.windows(app)
        if title is not None:
            windows = [window for window in windows if title in window.title]
        if not windows:
            raise RuntimeError(
                "screen_page_not_open" if title is not None else "screen_window_not_found"
            )
        window = max(windows, key=lambda w: w.frame.w * w.frame.h)
        image = self.desktop.capture(window)
        regions = Regions.detect(image.convert("L").tobytes(), *image.size)
        return window, image, regions

    async def crop_blocks(self, image, rect, output):
        path = output.save(image.crop(rect.box()), "region")
        try:
            return await self.ocr(Path(path))
        finally:
            Path(path).unlink(missing_ok=True)

    async def open_title(self, chat, image, regions, output):
        blocks = await self.crop_blocks(image, regions.header, output)
        return title_match(chat, [block["text"] for block in blocks])

    async def call(self, request):
        if not isinstance(request, dict) or request.get("action") not in ACTIONS:
            raise RuntimeError("screen_invalid_request")
        action, app = request["action"], request.get("app")
        if action == "permissions":
            unlocked = not self.desktop.locked()
            return {"screen_recording": unlocked, "accessibility": unlocked}
        output = Output(request.get("out_dir"))
        if action == "recognize":
            path = request.get("path")
            if not isinstance(path, str) or not output.contains(path):
                raise RuntimeError("screen_invalid_request")
            return {"blocks": await self.ocr(Path(path))}
        if app not in {"qq", "wechat", "chrome"} or (app == "chrome" and action != "capture_page"):
            raise RuntimeError("screen_unknown_app")
        if self.desktop.locked():
            raise RuntimeError("screen_locked")
        if action == "capture_announcement":
            return {"screenshot": None}
        if action == "capture_page":
            title = request.get("title")
            if app != "chrome" or not isinstance(title, str) or not title.strip():
                raise RuntimeError("screen_invalid_request")
            _, image, _ = self.shot(app, title)
            return output.screenshot(image, app)
        window, image, regions = self.shot(app)
        if action == "preview":
            return output.screenshot(image.crop(regions.list.box()), app)
        if action == "layout":
            width, height = image.size
            return {
                "window": {"width": window.frame.w, "height": window.frame.h},
                "found": regions.found,
                "columns": [x / width for x in regions.columns],
                "lines": [y / height for y in regions.lines],
                **{
                    name: getattr(regions, name).fractions(width, height)
                    for name in ("list", "header", "chat")
                },
                "files": {
                    name: output.save(
                        image.crop(getattr(regions, name).box()), f"{app}-layout-{name}"
                    )
                    for name in ("list", "header", "chat")
                },
            }
        chat, page = request.get("chat"), request.get("page")
        if not isinstance(chat, str) or not chat.strip() or type(page) is not int or page < 0:
            raise RuntimeError("screen_invalid_request")
        # Fallback rectangles are useful for preview/calibration; never use them for input.
        if not {"nav", "list", "header", "input"} <= set(regions.found):
            raise RuntimeError("screen_capture_failed")
        self.desktop.activate(window)
        await self.pause(0.6)
        window, image, regions = self.shot(app)
        title = await self.open_title(chat, image, regions, output)
        if not title:
            if page > 0:
                raise RuntimeError("screen_chat_not_open")
            rows = await self.crop_blocks(image, regions.list, output)
            rows = [row for row in rows if row_match(chat, row["text"])]
            if len(rows) != 1:
                raise RuntimeError("screen_chat_not_in_list")
            row = rows[0]
            if not {"nav", "list", "header", "input"} <= set(regions.found):
                raise RuntimeError("screen_capture_failed")
            point = (
                regions.list.x + (row["x"] + row["w"] / 2) * regions.list.w,
                regions.list.y + (row["y"] + row["h"] / 2) * regions.list.h,
            )
            self.desktop.click_row(window, regions.list, point, image.size)
            await self.pause(1.0)
            window, image, regions = self.shot(app)
            if not await self.open_title(chat, image, regions, output):
                raise RuntimeError("screen_chat_not_open")
        if not {"nav", "list", "header", "input"} <= set(regions.found):
            raise RuntimeError("screen_capture_failed")
        # Mouse wheel ticks vary by the user's scrolling settings. Three ticks is conservative
        # and preserves overlap; page stitching reports a history gap instead of guessing.
        self.desktop.scroll_chat(window, regions.chat, image.size, -120 if page == 0 else 3)
        await self.pause(0.9)
        _, image, regions = self.shot(app)
        title = await self.open_title(chat, image, regions, output)
        if not title:
            raise RuntimeError("screen_chat_not_open")
        return output.screenshot(image.crop(regions.chat.box()), app, chat=title, page=page)


def main():
    try:
        if sys.platform != "win32":
            raise RuntimeError("screen_capture_failed")
        from core.windows_ocr import recognize
        from plugins.screen.windows_native import Desktop

        request = json.loads(sys.stdin.buffer.read(65537))
        reply = asyncio.run(WindowsHelper(Desktop(), recognize).call(request))
        code = 0
    except Exception as error:
        message = str(error)
        reply, code = {"error": message if message in ERRORS else "screen_capture_failed"}, 1
    sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
