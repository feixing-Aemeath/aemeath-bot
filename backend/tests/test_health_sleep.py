from datetime import date
from zoneinfo import ZoneInfo

import pytest

from plugins.health.config import HealthConfig
from plugins.health.sleep import (
    day_mask,
    decode_mask,
    encode_mask,
    sleep_periods,
    sleep_regularity_index,
    summarize_night,
)
from plugins.health.summary import clock
from tests.health_fixtures import PHONE, SHANGHAI, at, stage

CONFIG = HealthConfig()


def night_for(records, day, config=CONFIG, zone=SHANGHAI):
    periods = sleep_periods(records, config.source_priority, config.sleep_night_gap_minutes)
    return summarize_night(periods, day, zone, config)


def test_night_crossing_midnight_belongs_to_the_morning_it_ends():
    records = [
        stage("2026-10-11 23:00", "2026-10-11 23:10", "awake"),
        stage("2026-10-11 23:10", "2026-10-12 01:00", "core"),
        stage("2026-10-12 01:00", "2026-10-12 02:00", "deep"),
        stage("2026-10-12 02:00", "2026-10-12 02:05", "awake"),
        stage("2026-10-12 02:05", "2026-10-12 03:00", "rem"),
        stage("2026-10-12 03:00", "2026-10-12 07:00", "core"),
        stage("2026-10-12 07:00", "2026-10-12 07:05", "awake"),
    ]
    assert night_for(records, date(2026, 10, 11)) is None
    night = night_for(records, date(2026, 10, 12))
    assert (night.onset, night.wake) == (at("2026-10-11 23:10"), at("2026-10-12 07:00"))
    assert night.asleep_minutes == 465
    assert (night.core_minutes, night.deep_minutes, night.rem_minutes) == (350, 60, 55)
    assert night.awake_minutes == 20  # Apple-style: awake records of the session
    assert night.segments == 1 and night.naps == 0
    assert clock(night.onset_minutes, 18) == "23:10" and clock(night.wake_minutes, 18) == "07:00"
    assert night.onset_minutes == 310 and night.wake_minutes == 780
    assert night.quality == "ok" and night.has_stages


def test_night_split_in_two_and_naps():
    records = [
        stage("2026-10-11 23:00", "2026-10-12 01:30", "core"),
        # Up for 70 minutes without any record, then back to sleep.
        stage("2026-10-12 02:40", "2026-10-12 07:00", "core"),
        stage("2026-10-12 13:00", "2026-10-12 13:40", "core"),  # afternoon nap
        stage("2026-10-12 18:30", "2026-10-12 19:00", "core"),  # after the cutoff
    ]
    night = night_for(records, date(2026, 10, 12))
    assert night.asleep_minutes == 150 + 260
    assert (night.segments, night.naps, night.nap_minutes) == (2, 1, 40)
    assert night.wake == at("2026-10-12 07:00")
    assert night_for(records, date(2026, 10, 13)).asleep_minutes == 30
    # With a stricter night gap the early part becomes a separate period (counted as a nap).
    strict = HealthConfig.from_dict({"sleep": {"night_gap_minutes": 60}})
    split = night_for(records, date(2026, 10, 12), strict)
    assert split.asleep_minutes == 260 and split.naps == 2 and split.segments == 1


def test_watch_night_wins_over_the_phone_for_the_same_night():
    phone = {"source": PHONE, "device": "phone"}
    records = [
        stage("2026-10-11 22:00", "2026-10-12 08:00", "in_bed", **phone),
        stage("2026-10-11 22:15", "2026-10-12 07:50", "asleep", **phone),
        stage("2026-10-11 23:30", "2026-10-12 06:30", "core"),
    ]
    night = night_for(records, date(2026, 10, 12))
    assert night.device_class == "watch" and night.asleep_minutes == 420
    assert night.onset == at("2026-10-11 23:30") and night.unspecified_minutes == 0
    # A night with only the phone's estimate is kept but marked not comparable.
    phone_only = night_for(records[:2], date(2026, 10, 12))
    assert phone_only.device_class == "phone" and phone_only.quality == "partial"
    assert not phone_only.has_stages
    # In-bed alone never counts as sleep.
    assert night_for(records[:1], date(2026, 10, 12)) is None


def test_a_long_phone_record_does_not_join_the_night_to_a_later_nap():
    records = [
        stage("2026-10-11 22:00", "2026-10-12 10:00", "asleep", source=PHONE, device="phone"),
        stage("2026-10-11 23:30", "2026-10-12 06:30", "core"),
        stage("2026-10-12 08:40", "2026-10-12 09:30", "core"),  # back to sleep after 130 min
    ]
    night = night_for(records, date(2026, 10, 12))
    assert night.wake == at("2026-10-12 06:30") and night.asleep_minutes == 420
    assert (night.naps, night.nap_minutes) == (1, 50)


def test_awake_inside_an_asleep_blanket_and_exact_duplicates():
    app = {"source": "Pillow", "device": "other"}
    blanket = [
        stage("2026-10-11 23:00", "2026-10-12 07:00", "asleep", **app),
        stage("2026-10-12 02:00", "2026-10-12 02:30", "awake", **app),
    ]
    night = night_for(blanket, date(2026, 10, 12), HealthConfig.from_dict({}))
    assert (night.asleep_minutes, night.awake_minutes, night.has_stages) == (450, 30, False)
    deep = stage("2026-10-12 01:00", "2026-10-12 02:00", "deep")
    copy = stage("2026-10-12 01:00", "2026-10-12 02:00", "deep", source="Apple Watch")
    upload = stage("2026-10-12 01:00", "2026-10-12 02:00", "deep", origin="upload")
    assert night_for([deep, copy, upload], date(2026, 10, 12)).deep_minutes == 60


def test_short_record_is_partial():
    night = night_for([stage("2026-10-12 02:00", "2026-10-12 03:30", "core")], date(2026, 10, 12))
    assert night.asleep_minutes == 90 and night.quality == "partial"


def test_time_zone_decides_the_day_and_clock():
    records = [stage("2026-10-11 23:10", "2026-10-12 07:00", "core")]
    new_york = ZoneInfo("America/New_York")
    config = HealthConfig.from_dict({"timezone": "America/New_York"})
    # 23:10-07:00 in Shanghai is 11:10-19:00 the previous day in New York; it ends after
    # the 18:00 cutoff, so it is the sleep day after 2026-10-11 there.
    assert night_for(records, date(2026, 10, 11), config, new_york) is None
    night = night_for(records, date(2026, 10, 12), config, new_york)
    assert clock(night.onset_minutes, 18) == "11:10" and clock(night.wake_minutes, 18) == "19:00"


def test_daylight_saving_night_counts_elapsed_minutes():
    zone = ZoneInfo("America/New_York")
    config = HealthConfig.from_dict({"timezone": "America/New_York"})
    records = [stage(at("2026-03-07 23:00", zone), at("2026-03-08 07:00", zone), "core")]
    night = night_for(records, date(2026, 3, 8), config, zone)
    assert night.asleep_minutes == 420  # clocks jumped from 02:00 to 03:00
    assert clock(night.onset_minutes, 18) == "23:00" and clock(night.wake_minutes, 18) == "07:00"


def test_minute_mask_uses_minute_midpoints():
    base = at("2026-10-11 18:00")
    piece = stage(base + 630, base + 690, "core")  # 10.5 to 11.5 minutes after the cutoff
    periods = sleep_periods([piece], CONFIG.source_priority, 120)
    mask = day_mask(periods, date(2026, 10, 12), SHANGHAI, 18)
    assert mask == 1 << 10
    assert decode_mask(encode_mask(mask)) == mask


def mask(start, end):
    return ((1 << (end - start)) - 1) << start


def test_sleep_regularity_index():
    regular = mask(300, 780)
    sri, pairs = sleep_regularity_index([regular] * 7)
    assert (sri, pairs) == (100, 6)
    # Alternating schedules 12 hours apart agree only while awake in both: 480 of 1440 minutes.
    shifted = mask(1020, 1440) | mask(0, 60)
    sri, pairs = sleep_regularity_index([regular, shifted] * 3)
    assert pairs == 5 and sri == pytest.approx(200 / 3 - 100)
    # Unmeasured nights are skipped instead of being read as "awake all night".
    assert sleep_regularity_index([regular, None, regular, regular]) == (100, 1)
    assert sleep_regularity_index([regular, None]) == (None, 0)
