"""RMSSD (iOS / watchOS 27): read from exports and uploads, preferred over SDNN when it can be
judged, and only ever compared with its own history."""

import math
from datetime import date, timedelta

import pytest

from core.db import Database
from core.models import HealthBatch
from core.store import Store
from plugins.health.config import HealthConfig
from plugins.health.engine import HealthEngine
from plugins.health.observations import UPLOAD_NORMALIZER, classify_type, convert, normalize_upload
from plugins.health.storage import read_days
from tests.test_health_engine import END, build
from tests.test_health_plugin import night_samples

RMSSD = "HKQuantityTypeIdentifierHeartRateVariabilityRMSSD"


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    """60 days of SDNN; RMSSD on the last 30 nights; the last 3 days stressed."""
    return build(tmp_path_factory.mktemp("rmssd-30"), rmssd_days=30)


@pytest.fixture(scope="module")
def just_upgraded(tmp_path_factory):
    """RMSSD only on the last 5 nights: too few for a reliable baseline of its own."""
    return build(tmp_path_factory.mktemp("rmssd-5"), rmssd_days=5)


def sample(sample_id, value, unit="ms", kind=RMSSD, start="2026-10-11T18:00:00Z"):
    return {"id": sample_id, "type": kind, "value": value, "unit": unit, "start_at": start,
            "end_at": start.replace(":00:00Z", ":00:58Z"), "source": "示例用户的Apple Watch"}  # fmt: skip


def test_type_names_units_and_range():
    assert classify_type(RMSSD, 40) == ("hrv_rmssd", "")
    assert classify_type("hrv_rmssd", 40) == ("hrv_rmssd", "")
    assert convert("hrv_rmssd", 42.5, "ms") == 42.5
    assert convert("hrv_rmssd", 0.0425, "s") == pytest.approx(42.5)
    assert convert("hrv_rmssd", 42.5, "count/min") is None
    assert convert("hrv_rmssd", 600, "ms") is None  # implausible: dropped as an artefact
    row = normalize_upload(sample("r", 41))
    assert row[0] == "hrv_rmssd" and row[3] == 41 and row[6] == "watch"


def test_export_rmssd_has_its_own_metric_and_baseline(upgraded):
    engine, truth = upgraded
    rows = read_days(engine.db)
    today = rows[END]["metrics"]
    assert today["hrv_ln_rmssd"]["method"] == "sleep" and today["hrv_ln_sdnn"]["value"]
    # Nightly geometric mean of the readings; the planted value has 5% reading noise.
    assert math.exp(today["hrv_ln_rmssd"]["value"]) == pytest.approx(
        truth[END]["rmssd_ms"], rel=0.1
    )
    before = rows[END - timedelta(days=31)]["metrics"]
    assert before["hrv_ln_rmssd"] == {"value": None, "quality": "missing"}
    metrics = engine.daily(END, 1, today=END)["days"][-1]["metrics"]
    rmssd, sdnn = metrics["hrv_ln_rmssd"], metrics["hrv_ln_sdnn"]
    assert rmssd["rmssd_ms"] == pytest.approx(math.exp(rmssd["value"]), abs=0.05)
    # Each baseline holds only its own measure: 29 earlier RMSSD nights, SDNN from 30 days.
    assert rmssd["vs_30d"]["n"] == 29 and sdnn["vs_30d"]["n"] == 30
    rmssd_median = math.exp(rmssd["vs_30d"]["median"])
    sdnn_median = math.exp(sdnn["vs_30d"]["median"])
    assert rmssd_median == pytest.approx(45 * 1.3, rel=0.08)
    assert sdnn_median == pytest.approx(45, rel=0.08)
    baselines = engine.baselines(END, today=END)["metrics"]
    assert (
        baselines["hrv_ln_rmssd"]["30d"]["n"] == 29 and baselines["hrv_ln_rmssd"]["30d"]["reliable"]
    )


def test_rmssd_is_preferred_once_it_can_be_judged(upgraded, just_upgraded):
    daily = upgraded[0].daily(END, 1, today=END)["days"][-1]
    assert daily["hrv_preferred"] == "hrv_ln_rmssd"
    hrv_alert = next(a for a in daily["alerts"] if a["rule"] == "hrv_low")
    assert hrv_alert["metric"] == "hrv_ln_rmssd"
    assert "HRV（ln RMSSD）" in hrv_alert["message"] and "RMSSD" in hrv_alert["message"]
    assert hrv_alert["consecutive_days"] == 3

    # Right after the upgrade RMSSD has no reliable baseline yet: SDNN keeps judging the day,
    # RMSSD is still shown with its own (insufficient) baseline, never compared with SDNN.
    daily = just_upgraded[0].daily(END, 1, today=END)["days"][-1]
    assert daily["hrv_preferred"] == "hrv_ln_sdnn"
    rmssd = daily["metrics"]["hrv_ln_rmssd"]
    assert rmssd["vs_30d"]["n"] == 4 and rmssd["vs_30d"]["level"] == "baseline_insufficient"
    hrv_alert = next(a for a in daily["alerts"] if a["rule"] == "hrv_low")
    assert hrv_alert["metric"] == "hrv_ln_sdnn" and "SDNN" in hrv_alert["message"]


def test_explicit_rules_still_name_one_measure(upgraded):
    engine, _ = upgraded
    config = HealthConfig.from_dict(
        {"alerts": [{"id": "sdnn_low", "metric": "hrv_ln_sdnn", "direction": "below", "z": 1}]}
    )
    analyzer = engine.analyzer(END, 95)
    analyzer.config = config
    assert [a["metric"] for a in analyzer.alerts(END)] == ["hrv_ln_sdnn"]


def test_nightly_preference_skips_daytime_values(upgraded):
    engine, _ = upgraded
    analyzer = engine.analyzer(END, 40)
    entry = analyzer.rows[END]["metrics"]["hrv_ln_rmssd"]
    entry["method"] = "day"  # as if RMSSD had only been measured during the day
    assert analyzer.preferred_hrv(END, nightly=True) == "hrv_ln_sdnn"
    assert analyzer.preferred_hrv(END + timedelta(days=1)) is None


def test_uploaded_rmssd_is_read_and_old_uploads_are_read_again(tmp_path):
    db = Database(tmp_path / "assistant.sqlite3")
    store = Store(db)
    engine = HealthEngine(db, tmp_path, HealthConfig())
    store.health(HealthBatch.model_validate({"samples": night_samples()}))
    engine.refresh()
    day = date(2026, 10, 12)
    assert read_days(db)[day]["metrics"]["hrv_ln_rmssd"]["value"] is None

    # An RMSSD sample stored while an older normalizer ignored it: the cursor is past it.
    store.health(HealthBatch.model_validate({"samples": [sample("rmssd-1", 52)]}))
    last = db.rows("SELECT max(rowid) AS n FROM health_samples")[0]["n"]
    engine.put_kv("upload_cursor", last)
    assert engine.refresh()["uploads_used"] == 0
    assert read_days(db)[day]["metrics"]["hrv_ln_rmssd"]["value"] is None

    engine.put_kv("upload_normalizer", UPLOAD_NORMALIZER - 1)
    result = engine.refresh()
    assert result["uploads_used"] == len(night_samples()) + 1  # everything read again once
    entry = read_days(db)[day]["metrics"]["hrv_ln_rmssd"]
    assert entry["method"] == "sleep" and math.exp(entry["value"]) == pytest.approx(52)
    assert engine.refresh()["uploads_used"] == 0
