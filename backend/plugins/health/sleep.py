"""Nightly sleep from HealthKit sleep-stage records.

A sleep day D runs from (D-1) at the cutoff hour (default 18:00) to D at the cutoff hour, local
wall clock, so a night that crosses midnight belongs to the morning it ends on.
"""

import base64
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from plugins.health.cleaning import stream_rank
from plugins.health.intervals import Coverage
from plugins.health.observations import ASLEEP_STAGES

MINUTES_PER_DAY = 1440
# Within one source a specific stage wins over "awake", and "awake" over an "asleep" blanket
# (some apps write one long asleep record plus shorter awake records inside it).
STAGE_PRECEDENCE = {"deep": 0, "rem": 1, "core": 2, "awake": 3, "asleep": 4}


@dataclass(frozen=True)
class Piece:
    start: int
    end: int
    stage: str


@dataclass
class Period:
    """One sleep period (night or nap) after choosing a single device class."""

    pieces: list
    device_class: str
    asleep: list = field(init=False)

    def __post_init__(self):
        self.asleep = [p for p in self.pieces if p.stage in ASLEEP_STAGES]

    @property
    def onset(self):
        return self.asleep[0].start

    @property
    def wake(self):
        return self.asleep[-1].end

    def seconds(self, *stages):
        return sum(p.end - p.start for p in self.pieces if p.stage in stages)


@dataclass
class Night:
    sleep_date: date
    onset: int
    wake: int
    onset_minutes: float  # wall-clock minutes after (sleep_date - 1) at the cutoff hour
    wake_minutes: float
    asleep_minutes: float  # the main sleep only; total_minutes adds the naps
    deep_minutes: float
    core_minutes: float
    rem_minutes: float
    unspecified_minutes: float
    awake_minutes: float
    segments: int
    naps: int
    nap_minutes: float
    device_class: str
    has_stages: bool
    quality: str
    mask: int  # bit m = asleep during wall-clock minute m of the sleep day, naps included

    @property
    def total_minutes(self):
        """Sleep of the whole sleep day: the main sleep plus its naps. Sleep split into two
        long stretches is common; the longer one alone would look like a short night."""
        return self.asleep_minutes + self.nap_minutes


def sleep_day_start(day, zone, cutoff_hour):
    """Local wall-clock start of sleep day `day`."""
    return datetime.combine(day - timedelta(days=1), time(cutoff_hour), zone)


def sleep_day_of(moment, zone, cutoff_hour):
    local = datetime.fromtimestamp(moment, zone).replace(tzinfo=None)
    return (local - timedelta(hours=cutoff_hour)).date() + timedelta(days=1)


def wall_minutes(moment, day, zone, cutoff_hour):
    base = sleep_day_start(day, zone, cutoff_hour).replace(tzinfo=None)
    local = datetime.fromtimestamp(moment, zone).replace(tzinfo=None)
    return (local - base).total_seconds() / 60


def resolve_pieces(records, priority):
    """Non-overlapping stage pieces: per time slice the best-ranked stream wins."""
    covered, pieces = Coverage(), []
    ordered = sorted(
        records,
        key=lambda r: (stream_rank(r, priority), STAGE_PRECEDENCE[r.label], r.start, r.end),
    )
    for record in ordered:
        for start, end in covered.gaps(record.start, record.end):
            pieces.append(Piece(start, end, record.label))
        covered.add(record.start, record.end)
    return sorted(pieces, key=lambda p: p.start)


def clusters(records, gap_minutes):
    """Group records whose gaps are at most `gap_minutes` (records sorted by start)."""
    groups, current, current_end = [], [], 0
    for record in records:
        if current and record.start - current_end > gap_minutes * 60:
            groups.append(current)
            current = []
        current_end = max(current_end, record.end) if current else record.end
        current.append(record)
    if current:
        groups.append(current)
    return groups


def sleep_periods(records, priority, night_gap_minutes):
    """Cluster sleep/awake records of all sources that are at most `night_gap_minutes` apart.

    Each cluster then keeps only the best device class it contains, so on a night recorded by
    both the watch and the phone only the watch is used, while a phone-only night still counts.
    The kept records are clustered again, so a long phone record cannot join the watch's night
    to a later nap. In-bed records never define sleep.
    """
    states = sorted(
        (r for r in records if r.label in STAGE_PRECEDENCE and r.end > r.start),
        key=lambda r: (r.start, r.end),
    )
    periods = []
    for cluster in clusters(states, night_gap_minutes):
        best = min(priority.index(r.device_class) for r in cluster)
        chosen = [r for r in cluster if priority.index(r.device_class) == best]
        for group in clusters(chosen, night_gap_minutes):
            period = Period(resolve_pieces(group, priority), priority[best])
            if period.asleep:
                periods.append(period)
    return periods


def count_segments(period, gap_minutes):
    segments, last_end = 0, None
    for piece in period.asleep:
        if last_end is None or piece.start - last_end > gap_minutes * 60:
            segments += 1
        last_end = piece.end if last_end is None else max(last_end, piece.end)
    return segments


def day_mask(periods, day, zone, cutoff_hour):
    """Minute-level sleep/wake for one sleep day, from every period that reaches into it."""
    mask = 0
    for period in periods:
        for piece in period.asleep:
            first = wall_minutes(piece.start, day, zone, cutoff_hour)
            last = wall_minutes(piece.end, day, zone, cutoff_hour)
            # Minute m is asleep when its midpoint m + 0.5 lies inside [first, last).
            low = max(0, math.ceil(first - 0.5))
            high = min(MINUTES_PER_DAY, math.ceil(last - 0.5))
            if high > low:
                mask |= ((1 << (high - low)) - 1) << low
    return mask


def summarize_night(periods, day, zone, config):
    """Main sleep of sleep day `day` (the period with most sleep) plus its naps, or None."""
    cutoff = config.sleep_day_cutoff_hour
    mine = [p for p in periods if sleep_day_of(p.wake, zone, cutoff) == day]
    if not mine:
        return None
    main = max(mine, key=lambda p: (p.seconds(*ASLEEP_STAGES), p.wake))
    naps = [p for p in mine if p is not main]
    asleep = main.seconds(*ASLEEP_STAGES) / 60
    has_stages = any(p.stage in {"deep", "core", "rem"} for p in main.pieces)
    ok = main.device_class == config.source_priority[0] and asleep >= config.min_sleep_minutes
    return Night(
        sleep_date=day,
        onset=main.onset,
        wake=main.wake,
        onset_minutes=wall_minutes(main.onset, day, zone, cutoff),
        wake_minutes=wall_minutes(main.wake, day, zone, cutoff),
        asleep_minutes=asleep,
        deep_minutes=main.seconds("deep") / 60,
        core_minutes=main.seconds("core") / 60,
        rem_minutes=main.seconds("rem") / 60,
        unspecified_minutes=main.seconds("asleep") / 60,
        awake_minutes=main.seconds("awake") / 60,
        segments=count_segments(main, config.sleep_segment_gap_minutes),
        naps=len(naps),
        nap_minutes=sum(p.seconds(*ASLEEP_STAGES) for p in naps) / 60,
        device_class=main.device_class,
        has_stages=has_stages,
        quality="ok" if ok else "partial",
        mask=day_mask(periods, day, zone, cutoff),
    )


def encode_mask(mask):
    return base64.b64encode(mask.to_bytes(MINUTES_PER_DAY // 8, "big")).decode()


def decode_mask(text):
    return int.from_bytes(base64.b64decode(text), "big")


def sleep_regularity_index(masks):
    """Phillips et al. (2017): SRI = 200 * P(same state at t and t + 24 h) - 100.

    `masks` are consecutive sleep days, None where the night was not measured. A pair with a
    missing day is skipped rather than read as a sleepless night. Returns (sri or None, pairs).
    """
    same = pairs = 0
    for first, second in zip(masks, masks[1:], strict=False):
        if first is None or second is None:
            continue
        pairs += 1
        same += MINUTES_PER_DAY - (first ^ second).bit_count()
    if not pairs:
        return None, 0
    return 200 * same / (pairs * MINUTES_PER_DAY) - 100, pairs
