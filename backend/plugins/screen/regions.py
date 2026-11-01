"""WindowShot's divider rules, independent of image libraries and OS APIs.

All rectangles use screenshot pixels, measured from the top left. Input coordinates are
converted using the captured window's frame, so monitor scaling does not enter the rules.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self):
        return self.x + self.w / 2, self.y + self.h / 2

    def contains(self, point):
        x, y = point
        return self.x < x < self.x + self.w and self.y < y < self.y + self.h

    def box(self):
        return tuple(round(v) for v in (self.x, self.y, self.x + self.w, self.y + self.h))

    def fractions(self, width, height):
        return {
            "x": self.x / width,
            "y": self.y / height,
            "w": self.w / width,
            "h": self.h / height,
        }


def screen_point(point, frame, size):
    return (frame.x + point[0] * frame.w / size[0], frame.y + point[1] * frame.h / size[1])


def merged(hits):
    result = []
    for value in hits:
        if not result or value - result[-1] > 3:
            result.append(value)
    return result


def dividers(gray, width, height, *, vertical, start, end):
    total, across = (width, height) if vertical else (height, width)
    samples = range(max(0, start), min(across, end), 2)
    if not samples:
        return []
    hits = []
    for position in range(1, total):
        changed = sum(
            abs(gray[s * width + position] - gray[s * width + position - 1]) >= 2
            if vertical
            else abs(gray[position * width + s] - gray[(position - 1) * width + s]) >= 2
            for s in samples
        )
        if changed >= 0.6 * len(samples):
            hits.append(position)
    return merged(hits)


@dataclass(frozen=True)
class Regions:
    list: Rect
    header: Rect
    chat: Rect
    found: list[str]
    columns: list[int]
    lines: list[int]

    @classmethod
    def detect(cls, gray, width, height):
        if width < 10 or height < 10 or len(gray) != width * height:
            raise ValueError("screen_capture_failed")
        columns = dividers(
            gray, width, height, vertical=True, start=int(height * 0.15), end=int(height * 0.95)
        )
        interior = [x for x in columns if 0.015 < x / width < 0.985]
        nav = next((x for x in interior if x / width < 0.16), None)
        right = next(
            (x for x in interior if 0.15 < x / width < 0.65 and x > (nav or 0) + int(0.08 * width)),
            None,
        )
        found = [key for key, value in (("nav", nav), ("list", right)) if value is not None]
        left, right = nav or int(0.06 * width), right or int(0.32 * width)
        panel = next((x for x in interior if x > right + int(0.25 * width)), None)
        if panel is not None:
            found.append("panel")
        end = panel or width
        lines = dividers(gray, width, height, vertical=False, start=right + 4, end=end - 4)
        interior_lines = [y for y in lines if 0.015 < y / height < 0.985]
        header = next((y for y in reversed(interior_lines) if 0.03 < y / height < 0.25), None)
        input_top = next((y for y in reversed(interior_lines) if y / height > 0.45), None)
        found += [
            key for key, value in (("header", header), ("input", input_top)) if value is not None
        ]
        top, bottom = header or int(0.09 * height), input_top or int(0.74 * height)
        return cls(
            Rect(left + 1, 0, right - left - 1, height),
            Rect(right + 1, 0, end - right - 1, top),
            Rect(right + 1, top + 1, end - right - 1, bottom - top - 1),
            found,
            columns,
            lines,
        )
