"""The two macOS-only seams of screen reading: capturing a screenshot and recognizing its text.

Everything else (turning text blocks into messages, stitching overlapping screenshots,
remembering where each chat was read up to) is plain Python in this package and runs on Linux.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class TextBlock:
    """One OCR result. Coordinates are fractions of the captured area, origin at the TOP-left.

    Apple Vision reports bounding boxes with the origin at the bottom-left; the macOS helper
    converts them (y = 1 - vision_y - vision_height) before returning blocks.
    """

    text: str
    x: float
    y: float
    w: float
    h: float
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, data):
        block = cls(
            text=str(data["text"]),
            x=float(data["x"]),
            y=float(data["y"]),
            w=float(data["w"]),
            h=float(data["h"]),
            confidence=float(data.get("confidence", 1.0)),
        )
        numbers = (block.x, block.y, block.w, block.h, block.confidence)
        if not all(math.isfinite(n) for n in numbers):
            raise ValueError("invalid_text_block")
        if block.w <= 0 or block.h <= 0 or not 0 <= block.confidence <= 1:
            raise ValueError("invalid_text_block")
        if block.x < -0.01 or block.y < -0.01 or block.right > 1.01 or block.bottom > 1.01:
            raise ValueError("text_block_outside_capture")
        return block

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h


@dataclass(frozen=True)
class Screenshot:
    """A local screenshot. `path` never leaves the Mac; only recognized text does."""

    path: str
    kind: str  # preview (conversation list) / chat / announcement / page (a browser window)
    captured_at: datetime  # timezone-aware
    chat: str | None = None  # chat title the helper confirmed is open (None for preview)
    page: int = 0  # 0 = newest screen of the chat; n = after scrolling up n steps
    at_top: bool = False  # the helper could not scroll any further up

    def __post_init__(self):
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")


class ScreenCapture(Protocol):
    """Read-only UI operations. There is deliberately no typing, sending or clicking API."""

    async def preview(self) -> Screenshot:
        """Capture the conversation list without opening any chat."""

    async def capture_chat(self, chat: str, page: int) -> Screenshot:
        """Page 0: switch to `chat` and scroll to the newest message. Page n: scroll up one
        step from page n-1, keeping part of the previous screen visible (overlap)."""

    async def capture_announcement(self, chat: str) -> Screenshot | None:
        """Open the group announcement of the current chat, capture it and close it again.
        None when the chat has no announcement panel."""


class TextRecognizer(Protocol):
    async def recognize(self, screenshot: Screenshot) -> list[TextBlock]:
        """Text blocks of a screenshot, recognized on this machine."""
