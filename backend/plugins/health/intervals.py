"""Sorted, disjoint half-open [start, end) intervals in epoch seconds."""

import bisect


class Coverage:
    def __init__(self):
        self.starts, self.ends = [], []

    def add(self, start, end):
        if end <= start:
            return
        # Touching intervals merge, so the lists stay disjoint and sorted by both columns.
        low = bisect.bisect_left(self.ends, start)
        high = bisect.bisect_right(self.starts, end)
        if low < high:
            start, end = min(start, self.starts[low]), max(end, self.ends[high - 1])
        self.starts[low:high] = [start]
        self.ends[low:high] = [end]

    def gaps(self, start, end):
        """Uncovered pieces of [start, end)."""
        pieces, cursor = [], start
        index = bisect.bisect_right(self.ends, start)
        while index < len(self.starts) and self.starts[index] < end:
            if self.starts[index] > cursor:
                pieces.append((cursor, self.starts[index]))
            cursor = max(cursor, self.ends[index])
            index += 1
        if cursor < end:
            pieces.append((cursor, end))
        return pieces

    def contains(self, moment):
        index = bisect.bisect_right(self.starts, moment) - 1
        return index >= 0 and moment < self.ends[index]

    @property
    def total(self):
        return sum(end - start for start, end in zip(self.starts, self.ends, strict=True))


def overlap(a_start, a_end, b_start, b_end):
    return max(0, min(a_end, b_end) - max(a_start, b_start))
