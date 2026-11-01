"""Readiness calibration: daily check-ins (Apple Fitness readiness, own rating) and the script
that compares them with the computed score. The script only suggests; it never writes config."""

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.models import HealthBatch
from core.store import Store
from plugins.health.calibration import calibrate, predict, ranks, spearman, target_value
from plugins.health.cards import health_cards
from plugins.health.config import HealthConfig
from plugins.health.observations import normalize_upload
from plugins.health.storage import read_days
from scripts import readiness_calibration as script
from tests.test_api import client_for
from tests.test_health_engine import END, build
from tests.test_health_reports import MORNING, enable_health

SHANGHAI = ZoneInfo("Asia/Shanghai")


def checkin(kind, value, day, hour=8, minute=0, number=0):
    moment = datetime.combine(day, time(hour, minute), SHANGHAI).isoformat()
    return {"id": f"{kind}-{day}-{hour}-{minute}-{number}", "type": kind, "value": value,
            "unit": "", "start_at": moment, "end_at": moment, "source": "快捷指令"}  # fmt: skip


def upload(engine, samples):
    Store(engine.db).health(HealthBatch.model_validate({"samples": samples}))
    engine.refresh()


def log_days(engine, apple=None, perceived=None, days=35):
    """Check-ins for the last `days` days, valued by functions of that day's readiness."""
    daily = engine.daily(END, days, today=END + timedelta(days=1))
    samples = []
    for day in daily["days"]:
        when = date.fromisoformat(day["date"])
        for kind, value_of in (
            ("apple_fitness_readiness", apple),
            ("perceived_readiness", perceived),
        ):
            value = value_of(day["readiness"]) if value_of else None
            if value is not None:
                samples.append(checkin(kind, value, when))
    upload(engine, samples)


def run(engine, *args):
    return script.main(["--data-dir", str(engine.db.path.parent), "--end", END.isoformat(), *args])


@pytest.fixture
def engine(tmp_path):
    engine, _ = build(tmp_path, rmssd_days=30)
    return engine


def test_checkin_types_ranges_and_latest_entry_wins(engine):
    assert normalize_upload(checkin("apple_fitness_readiness", 7.5, END))[3] == 7.5
    for kind, value in (
        ("apple_fitness_readiness", 10.5),
        ("apple_fitness_readiness", -1),
        ("perceived_readiness", 6),
        ("perceived_readiness", 3.5),  # ratings are whole numbers
    ):
        assert normalize_upload(checkin(kind, value, END)) is None
    upload(
        engine,
        [
            checkin("apple_fitness_readiness", 6, END, hour=7),
            checkin("apple_fitness_readiness", 7.5, END, hour=9),  # corrected later that day
            checkin("perceived_readiness", 4, END, hour=8),
            checkin("perceived_readiness", 2, END - timedelta(days=1), hour=23, minute=50),
        ],
    )
    rows = read_days(engine.db)
    assert rows[END]["extras"]["checkin"] == {
        "apple_fitness_readiness": 7.5,
        "perceived_readiness": 4,
    }
    assert rows[END - timedelta(days=1)]["extras"]["checkin"] == {"perceived_readiness": 2}
    day = engine.daily(END, 1, today=END)["days"][-1]
    assert day["checkin"] == {"apple_fitness_readiness": 7.5, "perceived_readiness": 4}
    label = {c["title"]: c for c in health_cards(engine, END)}["今日准备度"]["data"]["label"]
    assert label.endswith("你记的健身 App 7.5 · 体感 4")


def test_helpers():
    assert ranks([3, 1, 2, 2]) == [4, 1, 2.5, 2.5]
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1)
    assert spearman([1, 2], [1, 2]) is None and spearman([1, 1, 1], [1, 2, 3]) is None
    assert target_value({"apple_fitness_readiness": 6, "perceived_readiness": 5}, "both") == 8
    assert target_value({"apple_fitness_readiness": None, "perceived_readiness": 1}, "both") == 0
    assert (
        target_value({"apple_fitness_readiness": 6, "perceived_readiness": None}, "perceived")
        is None
    )
    weights = {"hrv": 0.5, "resting_hr": 0.5, "sleep": 0, "training_load": 0}
    parts = {"hrv": 8, "resting_hr": 4, "sleep": 10, "training_load": None}
    assert predict(parts, weights, 2) == 6  # zero-weight parts are left out, as in the score
    assert predict(parts, weights, 3) is None


def test_a_target_that_follows_hrv_suggests_more_weight_on_hrv(engine):
    log_days(engine, apple=lambda r: r["components"]["hrv"]["score"])
    result = calibrate(engine.daily(END, 60, today=END + timedelta(days=1)),
                       engine.config.readiness, "apple")  # fmt: skip
    assert result["status"] == "suggest_change" and result["days"] >= 21
    assert result["fitted_weights"]["hrv"] >= 0.8
    assert result["loo_mae"] < result["current_mae"] * 0.9
    assert sum(result["fitted_weights"].values()) == pytest.approx(1)
    assert result["correlation"]["parts_vs_target"]["hrv"] == pytest.approx(1)


def test_a_target_equal_to_the_score_keeps_the_weights(engine):
    log_days(
        engine,
        apple=lambda r: r["score"],
        perceived=lambda r: (
            None if r["score"] is None else min(5, max(1, round(r["score"] / 2.5) + 1))
        ),
    )
    result = calibrate(engine.daily(END, 60, today=END + timedelta(days=1)),
                       engine.config.readiness)  # fmt: skip
    assert result["status"] == "keep"
    assert result["correlation"]["score_vs_apple"] == pytest.approx(1)
    counts = result["counts"]
    assert counts["apple_fitness_readiness"] == counts["perceived_readiness"] >= 30


def test_script_reports_suggests_and_never_writes_config(engine, capsys):
    before = engine.db.rows("SELECT * FROM plugins")
    log_days(engine, apple=lambda r: r["components"]["hrv"]["score"], days=10)
    assert run(engine) == 0
    text = capsys.readouterr().out
    assert "只给建议，不会修改任何配置" in text and "还不够：至少要 21 天" in text

    log_days(engine, apple=lambda r: r["components"]["hrv"]["score"])
    assert run(engine, "--target", "apple") == 0
    text = capsys.readouterr().out
    assert "建议：改用拟合权重" in text and '"readiness": {"weights": {"hrv": ' in text
    assert run(engine, "--json", "--target", "apple") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "suggest_change"
    assert engine.db.rows("SELECT * FROM plugins") == before  # nothing was written
    assert HealthConfig.from_dict({}).readiness.weights == engine.config.readiness.weights


def test_script_needs_a_database(tmp_path, capsys):
    assert script.main(["--data-dir", str(tmp_path / "nope")]) == 1
    assert "找不到数据库" in capsys.readouterr().err


async def test_app_flow_over_http(service):
    await enable_health(service)
    try:
        client = TestClient(create_app(service=service), headers=client_for(service).headers)
        moment = MORNING.isoformat()
        batch = {
            "samples": [
                {"id": "app-1", "type": "apple_fitness_readiness", "value": 6.5, "unit": "",
                 "start_at": moment, "end_at": moment, "source": "24小时助手 App"},
                {"id": "app-2", "type": "perceived_readiness", "value": 4, "unit": "",
                 "start_at": moment, "end_at": moment, "source": "24小时助手 App"},
            ]
        }  # fmt: skip
        assert client.post("/api/v1/health/samples", json=batch).json()["accepted"] == 2
        response = client.post("/api/v1/jobs/plugin:health:refresh/run")
        assert response.status_code == 202
        await service.jobs.submit("plugin:health:refresh")
        cards = {c["title"]: c for c in await service.plugins.cards("health")}
        assert cards["今日准备度"]["data"]["label"].endswith("你记的健身 App 6.5 · 体感 4")
    finally:
        await service.close()
