"""Source de-duplication shared by every metric.

A stream is one source name from one origin. Streams are ranked by device class priority
(watch, phone, other by default), then native Apple sources before third-party apps, then
exports before uploads, then name, so every choice below is deterministic.
"""

from plugins.health.intervals import Coverage, overlap

ORIGIN_ORDER = {"export": 0, "upload": 1}


def stream_rank(observation, priority):
    name = observation.source.lower()
    native = "apple watch" in name or "iphone" in name
    return (
        priority.index(observation.device_class),
        0 if native else 1,
        ORIGIN_ORDER.get(observation.origin, 2),
        observation.source,
    )


def best_points(samples, priority):
    """Instantaneous readings: drop the same reading delivered twice (export and upload carry
    different IDs and source names), then keep only the best device class present."""
    unique = {}
    for sample in sorted(samples, key=lambda s: stream_rank(s, priority)):
        unique.setdefault((sample.start, round(sample.value, 3)), sample)
    if not unique:
        return []
    best = min(priority.index(s.device_class) for s in unique.values())
    return sorted(
        (s for s in unique.values() if priority.index(s.device_class) == best),
        key=lambda s: (s.start, s.end, s.value),
    )


def cumulative_total(samples, start, end, priority):
    """Sum a cumulative quantity (steps, energy) over [start, end) without double counting.

    Fine-grained samples are merged per time slice: a lower-ranked stream only contributes the
    part of a sample that no higher-ranked stream already covers, pro rata by duration. A sample
    covering at least 80% of the window is a pre-merged daily total (HealthKit statistics from
    the App) and is used alone, because pro-rating sparse activity against it would overcount.
    Returns (total or None, device classes that contributed).
    """
    window = end - start
    daily = [s for s in samples if overlap(s.start, s.end, start, end) >= 0.8 * window]
    if daily:
        best = min(daily, key=lambda s: (stream_rank(s, priority), -s.value))
        share = overlap(best.start, best.end, start, end) / max(1, best.end - best.start)
        return best.value * share, {best.device_class}
    covered, total, classes = Coverage(), 0.0, set()
    for sample in sorted(samples, key=lambda s: (stream_rank(s, priority), s.start, s.end)):
        if sample.end == sample.start:
            if start <= sample.start < end and not covered.contains(sample.start):
                total += sample.value
                classes.add(sample.device_class)
            continue
        low, high = max(sample.start, start), min(sample.end, end)
        if high <= low:
            continue
        free = sum(b - a for a, b in covered.gaps(low, high))
        if free > 0:
            total += sample.value * free / (sample.end - sample.start)
            classes.add(sample.device_class)
        covered.add(low, high)
    return (total if classes else None), classes


def dedup_workouts(workouts, priority):
    """The same session written by the watch and by a third-party app is kept once."""
    kept = []
    for workout in sorted(workouts, key=lambda w: (stream_rank(w, priority), w.start)):
        duration = max(1, workout.end - workout.start)
        if any(
            overlap(workout.start, workout.end, k.start, k.end)
            > 0.5 * min(duration, max(1, k.end - k.start))
            for k in kept
        ):
            continue
        kept.append(workout)
    return sorted(kept, key=lambda w: w.start)
