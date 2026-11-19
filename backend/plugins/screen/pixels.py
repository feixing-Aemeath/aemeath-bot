"""Whether two screenshots of one chat overlap, judged by their pixels.

Where the overlap of two screens is mostly pictures, or cards of small text, Vision reads it
differently on each screen and too few lines match by text, although the capture scrolled
exactly as asked (QQ groups full of homework cards, 2026-11-15: of the rows that tell one place
from another, 95 to 100 % came back the same at the scroll distance, none anywhere else). A
chat scrolled by whole pixels repaints a row byte for byte the same, apart from the scroll bar,
what the pointer hovers and anything animating, so most rows must match. The same pixels show
when a scroll did not move the chat at all (the top of its history), and which rows of the
conversation list carry an unread badge, whose digits Vision does not read.

Screenshots stay on this machine and nothing here is logged. PNGs are read through macOS's
sips (into a temporary BMP that is deleted); elsewhere only BMPs are read, and other files are
simply not judged by pixels.
"""

import struct
import subprocess
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

SIPS = "/usr/bin/sips"
SCROLLBAR = 0.05  # the right edge, where QQ's scroll bar moves with the content
# The rows that tell one place in the chat from another (those seen once on their screen, so
# not blank and not a border repeated down the screen) must mostly come back, and enough of them.
MOSTLY = 0.8
FEWEST = 20
# A screen that did not scroll keeps at least half of those rows in place, even while animated
# stickers repaint (2026-11-18, an acm group at the top of its history: 77 %); a screen that
# scrolled keeps none (0 of 500 to 1900 on the 20 pages before it).
STILL = 0.5
# An unread badge: pixels at the list's right edge whose 7 x 7 neighbourhood is one colour.
# Badges on this Mac's Retina list showed 68 to 184 of them; rows without one, none (eight
# lists, 2026-11-15 to 11-18).
BADGE_LEFT = 0.86
SOLID = 3
BADGE_SOLID = 30


def overlap(newer, older, scroll, tolerance):
    """How far down the older screenshot shows what the newer one shows at its top, as a
    fraction of the height, when they overlap within `tolerance` of `scroll`; else None."""
    a, b = rows(newer), rows(older)
    if not a or not b or len(a) != len(b):
        return None
    height = len(b)
    new, old = telling(a), telling(b)
    low = max(1, round((scroll - tolerance) * height))
    high = min(height - 1, round((scroll + tolerance) * height))
    best = None
    for offset in range(low, high + 1):
        useful = matched = 0
        for row in range(offset, height):
            if old[row] is None:
                continue
            useful += 1
            matched += old[row] == new[row - offset]
        if matched >= FEWEST and matched >= MOSTLY * useful:
            if best is None or matched > best[0]:
                best = (matched, offset)
    return best[1] / height if best else None


def unmoved(newer, older):
    """Whether the newer screenshot shows the chat where the older one did: the scroll did not
    move it (the top of the history), although an animated sticker or a hover time changed a
    few rows, so the two are not the same image byte for byte."""
    a, b = rows(newer), rows(older)
    if not a or not b or len(a) != len(b):
        return False
    old = telling(b)
    useful = [row for row, value in enumerate(old) if value is not None]
    matched = [row for row in useful if old[row] == a[row]]
    if len(matched) < FEWEST or len(matched) < STILL * len(useful):
        return False
    # A stationary pinned banner alone can outweigh all the text below it. When a half
    # has enough evidence of its own, at least some of it must have stayed in place.
    middle = len(old) // 2
    return (sum(y < middle for y in useful) < FEWEST or matched[0] < middle) and (
        sum(y >= middle for y in useful) < FEWEST or matched[-1] >= middle
    )


def telling(hashes):
    counts = Counter(hashes)
    return [value if counts[value] == 1 else None for value in hashes]


@lru_cache(maxsize=8)
def rows(path):
    """A hash of each pixel row of the screenshot, top to bottom, the scroll bar left out; None
    when it cannot be read."""
    image = bitmap(path)
    if image is None:
        return None
    width, size, lines = image
    used = int(width * (1 - SCROLLBAR)) * size
    return tuple(hash(line[:used]) for line in lines)


def badge(path, top, bottom):
    """Whether the conversation-list row whose preview line spans `top` to `bottom` (fractions
    of the height) shows an unread badge at its right edge: a pill filled with one colour, grey
    for a muted chat and red otherwise. Vision does not read its white digits, and the muted
    bell or the end of a preview is drawn in thin strokes, never a filled patch."""
    image = bitmap(path)
    if image is None or bottom <= top:
        return False
    width, size, lines = image
    pad = (bottom - top) * 0.6
    left, right = int(BADGE_LEFT * width), int(0.995 * width)
    first = max(0, int((top - pad) * len(lines)))
    last = min(len(lines), int((bottom + pad) * len(lines)) + 1)

    def at(x, y):
        return lines[y][x * size : x * size + 3]

    area = [at(x, y) for y in range(first, last) for x in range(left, right)]
    if not area:
        return False
    background = Counter(area).most_common(1)[0][0]
    solid = 0
    for y in range(first + SOLID, last - SOLID):
        for x in range(left + SOLID, right - SOLID):
            colour = at(x, y)
            if sum(abs(a - b) for a, b in zip(colour, background, strict=True)) < 40:
                continue
            solid += all(
                at(x + dx, y + dy) == colour
                for dy in range(-SOLID, SOLID + 1)
                for dx in range(-SOLID, SOLID + 1)
            )
            if solid >= BADGE_SOLID:
                return True
    return False


@lru_cache(maxsize=2)
def bitmap(path):
    """(width, bytes per pixel, pixel rows top to bottom) of the screenshot; None when it cannot
    be read. Two kept: one screenshot's pixels are megabytes."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data.startswith(b"BM"):
        data = as_bmp(path)
    return decode(data) if data else None


def as_bmp(path):
    if not Path(SIPS).exists():
        return None
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "screen.bmp"
        try:
            subprocess.run(
                [SIPS, "-s", "format", "bmp", str(path), "--out", str(out)],
                capture_output=True,
                timeout=10,
                check=True,
            )
            return out.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None


def decode(data):
    try:
        (start,) = struct.unpack_from("<I", data, 10)
        width, height = struct.unpack_from("<ii", data, 18)
        (bits,) = struct.unpack_from("<H", data, 28)
        (compression,) = struct.unpack_from("<I", data, 30)
    except struct.error:
        return None
    # Uncompressed 24- or 32-bit pixels (3: 32-bit with channel masks, laid out the same).
    if width <= 0 or height == 0 or bits not in (24, 32) or compression not in (0, 3):
        return None
    size = bits // 8
    stride = (width * size + 3) & ~3
    count = abs(height)
    if start + stride * count > len(data):
        return None
    # A negative height stores the rows top down, a positive one bottom up.
    order = range(count) if height < 0 else range(count - 1, -1, -1)
    lines = tuple(data[start + y * stride : start + y * stride + width * size] for y in order)
    return width, size, lines
