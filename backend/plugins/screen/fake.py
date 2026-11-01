"""Linux stand-ins for the macOS helper, fed with simulated OCR output.

Input document (JSON or the equivalent dict):

    {
      "captured_at": "2026-10-15T21:00:00+08:00",
      "preview": {"blocks": [...]},
      "chats": {
        "软件工程2班": {
          "frames": [{"blocks": [...]}, ...],      # page 0 = newest screen, then scrolling up
          "announcement": {"blocks": [...]}         # optional
        }
      }
    }

Each block is {"text", "x", "y", "w", "h", "confidence"?} in fractions of the captured area
with the origin at the top-left. Asking for a page past the last frame returns the last frame
again with at_top=True, like an app that cannot scroll further.
"""

import json
from datetime import datetime
from pathlib import Path

from plugins.screen.model import Screenshot, TextBlock


class FakeTextRecognizer:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def register(self, path, blocks):
        self.results[path] = [
            b if isinstance(b, TextBlock) else TextBlock.from_dict(b) for b in blocks
        ]

    async def recognize(self, screenshot):
        self.calls.append(screenshot.path)
        return list(self.results.get(screenshot.path, []))


class FakeScreenCapture:
    def __init__(self, document, recognizer, confirmed_chat=None):
        self.document, self.recognizer = document, recognizer
        self.captured_at = datetime.fromisoformat(document["captured_at"])
        self.confirmed_chat = confirmed_chat  # simulate the helper opening another chat
        self.operations = []

    async def preview(self):
        self.operations.append(("preview",))
        path = "fake/preview.png"
        self.recognizer.register(path, self.document.get("preview", {}).get("blocks", []))
        return Screenshot(path, "preview", self.captured_at)

    async def capture_chat(self, chat, page):
        self.operations.append(("capture_chat", chat, page))
        frames = self.document["chats"][chat]["frames"]
        last = len(frames) - 1
        index = min(page, last)
        path = f"fake/{chat}/{index}.png"
        self.recognizer.register(path, frames[index]["blocks"])
        return Screenshot(
            path,
            "chat",
            self.captured_at,
            chat=self.confirmed_chat or chat,
            page=page,
            at_top=page > last or (page == last and frames[last].get("at_top", False)),
        )

    async def capture_announcement(self, chat):
        self.operations.append(("capture_announcement", chat))
        announcement = self.document["chats"][chat].get("announcement")
        if announcement is None:
            return None
        path = f"fake/{chat}/announcement.png"
        self.recognizer.register(path, announcement["blocks"])
        return Screenshot(path, "announcement", self.captured_at, chat=chat)


def load_fake_screen(source):
    """(capture, recognizer) from a document dict or a JSON file path."""
    if isinstance(source, str | Path):
        source = json.loads(Path(source).read_text(encoding="utf-8"))
    recognizer = FakeTextRecognizer()
    return FakeScreenCapture(source, recognizer), recognizer
