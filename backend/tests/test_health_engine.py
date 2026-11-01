import json
import re
import sqlite3
import tracemalloc
import zipfile
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from xml.sax.saxutils import quoteattr

import pytest

from core.db import Database
from core.models import HealthBatch
from core.store import Store
from plugins.health.config import HealthConfig
from plugins.health.engine import HealthEngine
from plugins.health.export_xml import ExportReader
from plugins.health.storage import ObservationStore, read_days, read_nights, read_workouts
from scripts.health_synthetic import WATCH, write_export

END = date(2026, 10, 12)
UNITS = {
    "heart_rate": "count/min",
    "resting_heart_rate": "count/min",
    "hrv_sdnn": "ms",
    "hrv_rmssd": "ms",
    "respiratory_rate": "count/min",
    "oxygen_saturation": "%",
    "wrist_temperature": "degC",
    "step_count": "count",
    "active_energy": "kcal",
    "vo2max": "mL/min·kg",
}


def build(directory, days=60, config=None, **options):
    truth = write_export(directory / "export.xml", days=days, end=END, **options)
    engine = HealthEngine(
        Database(directory / "assistant.sqlite3"), directory, config or HealthConfig()
    )
    engine.import_export(directory / "export.xml")
    engine.refresh()
    return engine, truth


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    return build(tmp_path_factory.mktemp("health"))


def as_uploads(path):
    """The same data as HealthSample uploads (what the iPhone App or Shortcuts would send)."""
    samples = []
    for index, (kind, start, end, value, label, source, _) in enumerate(ExportReader(path).rows()):
        if kind == "sleep":
            sample_type, unit, value = f"sleep_{label}", "", 1
        elif kind == "workout":
            sample_type, unit = f"workout:{label}", "min"
        elif kind.startswith("workout_hr"):
            suffix = "average" if kind.endswith("avg") else "max"
            sample_type, unit = f"workout_heart_rate_{suffix}", "count/min"
        else:
            sample_type, unit = kind, UNITS[kind]
        samples.append(
            {
                "id": f"sample-{index}",
                "type": sample_type,
                "value": value,
                "unit": unit,
                "start_at": datetime.fromtimestamp(start, UTC).isoformat(),
                "end_at": datetime.fromtimestamp(end, UTC).isoformat(),
                "source": source,
            }
        )
    return samples


def upload(db, samples):
    store = Store(db)
    for index in range(0, len(samples), 1000):
        store.health(HealthBatch.model_validate({"samples": samples[index : index + 1000]}))


def value(row, metric):
    return row["metrics"][metric]["value"]


def test_planted_values_are_recovered(synthetic):
    engine, truth = synthetic
    rows, nights = read_days(engine.db), read_nights(engine.db)
    assert min(rows) == END - timedelta(days=59) and max(rows) == END
    checked = 0
    for day, planted in truth.items():
        if not planted["worn"] or day == min(rows):
            continue
        row, night = rows[day], nights[day]
        assert value(row, "resting_hr") == pytest.approx(planted["rest_hr"])
        assert row["metrics"]["resting_hr"]["method"] == "apple"
        assert night["asleep_minutes"] == pytest.approx(planted["asleep"])
        for stage in ("deep", "core", "rem", "awake"):
            assert night[f"{stage}_minutes"] == pytest.approx(planted[stage]), (day, stage)
        onset = datetime.fromisoformat(night["onset_at"]).astimezone(engine.zone)
        wake = datetime.fromisoformat(night["wake_at"]).astimezone(engine.zone)
        assert onset.replace(tzinfo=None) == planted["onset"]
        assert wake.replace(tzinfo=None) == planted["wake"]
        assert night["nap_minutes"] == pytest.approx(planted["nap"])
        # The day's sleep counts the naps; stages and times above are the main sleep's.
        assert value(row, "sleep_minutes") == pytest.approx(planted["asleep"] + planted["nap"])
        assert night["device_class"] == "watch" and night["quality"] == "ok"
        # HRV is the mean of ln(SDNN): the geometric mean lands near the planted level.
        hrv = row["extras"]["hrv_sdnn_ms"]
        assert hrv == pytest.approx(planted["hrv_ms"], rel=0.1)
        checked += 1
    assert checked > 50
    split = [day for day, night in nights.items() if night["segments"] == 2]
    assert split and all(nights[day]["quality"] == "ok" for day in split)


def test_days_without_the_watch_are_flagged_and_kept_out_of_baselines(synthetic):
    engine, truth = synthetic
    rows = read_days(engine.db)
    missing = sorted(day for day, planted in truth.items() if not planted["worn"])
    assert len(missing) == 2
    for day in missing:
        assert rows[day]["status"] == "missing"
        assert "watch_not_worn" in rows[day]["issues"]
        assert rows[day]["metrics"]["resting_hr"]["quality"] == "missing"
        # The phone still counted steps, but those are not comparable with watch days.
        steps = rows[day]["metrics"]["steps"]
        assert steps["quality"] == "partial"
        assert steps["value"] == pytest.approx(truth[day]["steps_phone_only"])
    after = missing[-1] + timedelta(days=1)
    analyzer = engine.analyzer(after, 40, today=END)
    assert analyzer.baseline("steps", after, 7)["n"] == 5  # 7 days minus the 2 missing ones


def test_watch_and_phone_steps_are_merged_without_double_counting(synthetic):
    engine, truth = synthetic
    rows = read_days(engine.db)
    for day, planted in truth.items():
        if not planted["worn"] or day not in rows:
            continue
        steps = value(rows[day], "steps")
        # Phone samples are shifted by 20 s, so only a sliver of each is outside the watch's.
        lower = planted["steps_watch"] + planted["steps_phone_only"]
        assert lower <= steps <= lower * 1.08, day


def test_workouts_trimp_and_maximum_heart_rate(synthetic):
    engine, truth = synthetic
    workouts = read_workouts(engine.db)
    planted = [day for day, t in truth.items() if "workout_minutes" in t]
    assert sorted({w["date"] for w in workouts}) == sorted(d.isoformat() for d in planted)
    assert len(workouts) == len(planted)  # the Strava copies were dropped
    assert all(w["device_class"] == "watch" and w["hr_method"] == "samples" for w in workouts)
    assert all(w["trimp"] > 30 and w["hr_coverage"] > 0.9 for w in workouts)
    hr_max = engine.hr_max()
    assert hr_max["source"] == "observed" and 160 <= hr_max["value"] < 200  # spike of 228 ignored
    rows = read_days(engine.db)
    rest_day = next(
        d for d, r in rows.items() if r["extras"]["workouts"] == 0 and r["status"] == "complete"
    )
    assert rows[rest_day]["metrics"]["trimp"] == {"value": 0, "quality": "ok"}
    load = engine.daily(END, 1, today=END)["training_load"]
    assert load["chronic_reliable"] and load["acwr_reference"] is not None
    assert "争议" in load["note"]


def test_alerts_fire_only_after_three_stressed_days(synthetic):
    engine, _ = synthetic
    daily = engine.daily(END, 14, today=END)
    by_day = {day["date"]: {a["rule"] for a in day["alerts"]} for day in daily["days"]}
    assert by_day[END.isoformat()] == {"resting_hr_high", "hrv_low"}
    assert all(not rules for day, rules in by_day.items() if day < END.isoformat())
    for alert in daily["days"][-1]["alerts"]:
        assert alert["message"].endswith("建议关注。") and alert["consecutive_days"] == 3


def test_summary_contains_aggregates_only(synthetic):
    engine, _ = synthetic
    payloads = [
        engine.daily(END, 7, today=END),
        engine.sleep(END, 7, today=END),
        engine.baselines(END, today=END),
    ]
    text = json.dumps(payloads, ensure_ascii=False)
    for private in ("示例用户", "Apple Watch", "iPhone", "Strava", "1999-09-09", "HKBloodType"):
        assert private not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:", text)  # no individual timestamps
    assert all(p["notice"].startswith("以下是可穿戴设备按天汇总") for p in payloads)
    sleep = payloads[1]
    assert sleep["regularity"]["sri_7d"]["reliable"] and sleep["regularity"]["sri_7d"]["value"] > 80
    night = sleep["nights"][-1]
    assert re.fullmatch(r"\d\d:\d\d", night["onset"]) and night["stages_minutes"]["deep"] > 0
    baselines = payloads[2]["metrics"]
    assert (
        baselines["resting_hr"]["30d"]["reliable"]
        and baselines["resting_hr"]["30d"]["method"] == "apple"
    )
    assert payloads[0]["recompute_pending"] is False


def test_reimport_is_idempotent_and_personal_info_is_never_stored(synthetic, tmp_path):
    engine, _ = synthetic
    before = read_days(engine.db)
    again = engine.import_export(engine.observations.path.parents[1] / "export.xml")
    assert again["inserted"] == 0
    engine.recompute()
    assert read_days(engine.db) == before
    for path in (engine.observations.path, engine.db.path):
        with closing(sqlite3.connect(path)) as conn:
            dump = "\n".join(conn.iterdump())
        assert "1999-09-09" not in dump and "HKBloodType" not in dump and "Biological" not in dump


def test_zip_export_with_localized_name(synthetic, tmp_path):
    engine, _ = synthetic
    archive = tmp_path / "导出.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.write(
            engine.observations.path.parents[1] / "export.xml", "apple_health_export/导出.xml"
        )
        zipped.writestr("apple_health_export/导出_cda.xml", "<ClinicalDocument/>")
    rows = sum(1 for _ in ExportReader(archive).rows())
    assert rows == sum(
        1 for _ in ExportReader(engine.observations.path.parents[1] / "export.xml").rows()
    )


def test_parsing_streams_with_flat_memory(tmp_path):
    path = tmp_path / "export.xml"
    write_export(path, days=45)
    reader = ExportReader(path)
    tracemalloc.start()
    try:
        count = sum(1 for _ in reader.rows())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count > 15_000
    assert peak < 2_000_000 and peak < path.stat().st_size / 4
    assert reader.counts["records"] > 15_000 and reader.export_date.endswith("+0800")


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    directory = tmp_path_factory.mktemp("health-small")
    # The last 10 nights also have RMSSD, so both paths are compared with it too.
    engine, _ = build(directory, days=20, missing=(8,), stress_days=0, rmssd_days=10)
    return engine, directory


def comparable(rows):
    return {
        day: (
            row["status"],
            {m: e["value"] and round(e["value"], 6) for m, e in row["metrics"].items()},
        )
        for day, row in rows.items()
    }


def test_uploads_give_the_same_rows_as_the_export(small, tmp_path):
    exported, directory = small
    db = Database(tmp_path / "assistant.sqlite3")
    upload(db, as_uploads(directory / "export.xml"))
    uploaded = HealthEngine(db, tmp_path, HealthConfig())
    result = uploaded.refresh()
    assert result["uploads_ignored"] == 0 and result["days"] == 20
    assert comparable(read_days(db)) == comparable(read_days(exported.db))
    assert read_nights(db).keys() == read_nights(exported.db).keys()
    # Processing is incremental: nothing new, nothing recomputed.
    assert uploaded.refresh()["days"] == 0


def test_export_plus_overlapping_uploads_do_not_double_count(small, tmp_path):
    exported, directory = small
    db = Database(tmp_path / "assistant.sqlite3")
    both = HealthEngine(db, tmp_path, HealthConfig())
    both.import_export(directory / "export.xml")
    upload(db, as_uploads(directory / "export.xml"))
    both.refresh()
    assert comparable(read_days(db)) == comparable(read_days(exported.db))
    assert len(read_workouts(db)) == len(read_workouts(exported.db))


def test_a_watch_named_without_watch_keeps_its_nights(small, tmp_path):
    # Sleep and resting heart rate have no HKDevice; heart-rate readings name the model.
    exported, directory = small
    text = (directory / "export.xml").read_text(encoding="utf-8")
    renamed = tmp_path / "export.xml"
    renamed.write_text(text.replace(quoteattr(WATCH), quoteattr("小蓝")), encoding="utf-8")
    engine = HealthEngine(Database(tmp_path / "assistant.sqlite3"), tmp_path, HealthConfig())
    engine.import_export(renamed)
    engine.refresh()
    assert comparable(read_days(engine.db)) == comparable(read_days(exported.db))

    def nights(db):
        return {day: {**row, "updated_at": None} for day, row in read_nights(db).items()}

    assert nights(engine.db) and nights(engine.db) == nights(exported.db)


def test_uploads_from_a_watch_named_without_watch_count_as_the_watch(tmp_path):
    # The App uploads only the source's name. The export showed 「红线所系之处」 to be a
    # watch, so its uploaded nights, HRV and resting heart rate are the watch's, not "other".
    store = ObservationStore(tmp_path / "observations.sqlite3")
    night = (1_800_000_000, 1_800_003_600)
    store.add([("heart_rate", night[0], night[0], 60.0, "", "红线所系之处", "watch")], "export")
    store.add(
        [
            ("sleep", *night, 3.0, "deep", "红线所系之处", "other"),
            ("sleep", *night, 3.0, "deep", "别的 App", "other"),
        ],
        "upload",
    )
    found = store.fetch({"sleep": night})
    assert {(o.source, o.device_class, o.origin) for o in found} == {
        ("红线所系之处", "watch", "upload"),
        ("别的 App", "other", "upload"),
    }


def test_short_history_has_no_reliable_baseline(tmp_path):
    # Three earlier days: below the default minimum of 4 for the 7-day baseline.
    engine, _ = build(tmp_path, days=4, missing=(), stress_days=0)
    daily = engine.daily(END, 1, today=END)
    resting = daily["days"][0]["metrics"]["resting_hr"]
    assert resting["vs_7d"]["n"] == 3 and resting["vs_7d"]["z"] is None
    assert resting["vs_7d"]["level"] == resting["vs_30d"]["level"] == "baseline_insufficient"
    assert daily["days"][0]["alerts"] == []
    assert daily["training_load"]["acwr_reference"] is None
    assert not daily["training_load"]["chronic_reliable"]
    sri = engine.sleep(END, 4, today=END)["regularity"]["sri_30d"]
    assert sri["valid_day_pairs"] == 3 and not sri["reliable"]
    assert not engine.baselines(END, today=END)["metrics"]["sleep_minutes"]["30d"]["reliable"]


def test_changing_time_zone_rebuilds_every_day(small, tmp_path):
    exported, directory = small
    london = HealthEngine(
        exported.db, directory, HealthConfig.from_dict({"timezone": "Europe/London"})
    )
    assert london.stale
    assert london.refresh(incremental_only=True)["days"] == 0  # tool calls never rebuild all
    london.refresh()
    assert not london.stale
    night = london.sleep(END, 1, today=END)["nights"][0]
    # Shanghai 23:xx is 16:xx in London (BST), before the 18:00 cutoff of the same date.
    assert night["onset"].startswith(("15:", "16:"))
    exported.refresh()  # restore the module fixture
    assert not exported.stale
