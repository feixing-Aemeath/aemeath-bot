"""Health metric cards: built from the daily/sleep summaries, existing card types only."""

import copy
import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.db import Database
from core.models import Card, PluginPatch
from plugins.health.cards import EMPTY, health_cards, metric_cards
from plugins.health.config import HealthConfig
from plugins.health.engine import HealthEngine
from plugins.health.reports import hours_minutes
from tests.test_api import client_for
from tests.test_health_engine import END, build
from tests.test_health_reports import enable_health

TITLES = [
    "健康预警",
    "今日准备度",
    "昨晚睡眠",
    "最近 7 晚睡眠分期",
    "作息规律",
    "静息心率",
    "HRV",
    "训练负荷",
]


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    engine, _ = build(tmp_path_factory.mktemp("cards"))
    return engine


def by_title(cards):
    return {card["title"]: card for card in cards}


def test_cards_show_the_summaries_numbers(synthetic):
    cards = health_cards(synthetic, END)
    assert [c["title"] for c in cards] == TITLES
    for card in cards:
        Card.model_validate(card)
    daily = synthetic.daily(END, 1, today=END)
    sleep = synthetic.sleep(END, 7, today=END)
    today, night = daily["days"][-1]["metrics"], sleep["nights"][-1]
    shown = by_title(cards)

    # The synthetic history ends with three stressed days (see test_health_engine).
    alerts = shown["健康预警"]["data"]
    assert alerts["count"] == 2
    assert [i["title"] for i in alerts["items"]] == ["静息心率高于基线", "HRV 低于基线"]
    assert all(i["subtitle"].endswith("建议关注。") for i in alerts["items"])
    assert all(i["detail"] == "连续 3 天" for i in alerts["items"])

    readiness = shown["今日准备度"]["data"]
    score = daily["days"][-1]["readiness"]["score"]
    assert readiness["value"] == score and readiness["unit"] == "/ 10"
    for part in ("夜间 HRV", "静息心率", "睡眠", "训练负荷"):
        assert part in readiness["label"]
    yesterday = synthetic.daily(END - timedelta(days=1), 1, today=END)["days"][-1]["readiness"]
    assert readiness["delta"] == f"昨天 {yesterday['score']}"

    rhr = shown["静息心率"]["data"]
    assert rhr["value"] == today["resting_hr"]["value"] and rhr["unit"] == "次/分"
    assert rhr["trend"] == "up" and rhr["delta"] == f"{today['resting_hr']['vs_30d']['delta']:+.1f}"
    assert rhr["label"].startswith("今天 · 近 30 天中位数")
    hrv = shown["HRV"]["data"]
    assert hrv["value"] == round(today["hrv_ln_sdnn"]["sdnn_ms"]) and hrv["trend"] == "down"
    assert re.fullmatch(r"-\d+%", hrv["delta"])

    last = shown["昨晚睡眠"]["data"]
    assert last["label"].startswith(f"{night['onset']} 入睡 · {night['wake']} 醒来")
    hours, minutes = re.fullmatch(r"(\d+)小时(\d+)分", last["value"]).groups()
    assert int(hours) * 60 + int(minutes) == night["total_minutes"] == night["asleep_minutes"]
    assert last["trend"] in {"up", "down", "flat"} and "30 天中位数" in last["delta"]

    stages = shown["最近 7 晚睡眠分期"]["data"]
    assert stages["kind"] == "bar" and stages["unit"] == "小时"
    # The synthetic history has one 35-minute nap in the window, stacked on its day.
    assert [s["name"] for s in stages["series"]] == ["深睡", "核心", "REM", "清醒", "小睡"]
    assert all(len(s["points"]) == 7 for s in stages["series"])
    assert stages["series"][0]["points"][-1]["x"] == f"{END.month}/{END.day}"
    for index, shown_night in enumerate(sleep["nights"]):
        total = sum(s["points"][index]["y"] for s in stages["series"])
        assert total * 60 == pytest.approx(
            shown_night["total_minutes"] + shown_night["stages_minutes"]["awake"], abs=3
        )
    assert stages["series"][-1]["points"][4]["y"] == round(35 / 60, 2)

    regularity = shown["作息规律"]["data"]
    assert regularity["value"] == sleep["regularity"]["sri_7d"]["value"]
    assert regularity["unit"] == "SRI" and "入睡时间波动 ±" in regularity["label"]

    load = daily["training_load"]
    training = shown["训练负荷"]["data"]
    assert training["value"] == round(load["acute_7d_trimp"])
    assert f"急慢性负荷比 {load['acwr_reference']}（仅供参考）" in training["label"]


def test_cards_hold_aggregates_only(synthetic):
    text = json.dumps(health_cards(synthetic, END), ensure_ascii=False)
    for private in ("示例用户", "Apple Watch", "Strava"):  # source names never show
        assert private not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:", text)  # no individual timestamps


def test_next_morning_before_any_upload_keeps_yesterday(synthetic):
    cards = by_title(health_cards(synthetic, END + timedelta(days=1)))
    alerts = cards["健康预警"]["data"]["items"]
    assert len(alerts) == 2
    assert all("（昨天 " in i["subtitle"] and "今日" not in i["subtitle"] for i in alerts)
    assert cards["昨晚睡眠"]["data"]["value"] == "没有记录"
    assert "最近一晚（昨天醒来）睡了" in cards["昨晚睡眠"]["data"]["label"]
    assert cards["静息心率"]["data"]["label"].startswith("昨天 · ")
    assert cards["HRV"]["data"]["label"].startswith("昨天 · ")


def test_no_data_gives_one_card(tmp_path):
    engine = HealthEngine(Database(tmp_path / "assistant.sqlite3"), tmp_path, HealthConfig())
    assert health_cards(engine, END) == [
        {"type": "list", "title": "健康数据", "data": {"items": [EMPTY]}}
    ]


def test_missing_baselines_and_stages(synthetic):
    daily = synthetic.daily(END, 3, today=END)
    sleep = synthetic.sleep(END, 7, today=END)
    daily, sleep = copy.deepcopy(daily), copy.deepcopy(sleep)
    for day in daily["days"]:
        day["alerts"] = []
        for metric in ("resting_hr", "hrv_ln_sdnn"):
            day["metrics"][metric]["vs_30d"] = {"level": "baseline_insufficient", "n": 5}
        day["metrics"]["resting_hr"]["method"] = "sleep_hr"
    night = sleep["nights"][-1]
    night["stages_minutes"], night["vs_30d"] = None, {}
    sleep["nights"][-2] = {"date": sleep["nights"][-2]["date"], "quality": "missing"}
    sleep["regularity"]["sri_7d"] = {"value": None, "valid_day_pairs": 2, "reliable": False}
    daily["training_load"].update(
        acute_reliable=False, acute_missing_days=3, chronic_reliable=False, acwr_reference=None
    )
    cards = by_title(metric_cards(daily, sleep))

    assert cards["健康预警"]["data"] == {"items": ["目前没有需要关注的指标。"], "count": 0}
    rhr = cards["静息心率"]["data"]
    assert "trend" not in rhr and "delta" not in rhr and rhr["label"].endswith("按睡眠心率估算")
    assert "trend" not in cards["HRV"]["data"]
    stages = {s["name"]: s["points"] for s in cards["最近 7 晚睡眠分期"]["data"]["series"]}
    assert stages["未分期"][-1]["y"] == round(night["asleep_minutes"] / 60, 2)
    assert stages["深睡"][-1]["y"] == 0 and stages["深睡"][-2]["y"] == 0
    assert cards["作息规律"]["data"]["value"] == "数据不足"
    assert cards["训练负荷"]["data"]["label"] == "近 7 天有 3 天缺少数据 · 28 天的数据还不够"


def test_a_day_with_naps_shows_the_total_and_the_main_sleep_apart(synthetic):
    daily = copy.deepcopy(synthetic.daily(END, 1, today=END))
    sleep = copy.deepcopy(synthetic.sleep(END, 7, today=END))
    night = sleep["nights"][-1]
    night["naps"] = {"count": 1, "minutes": 95}
    night["total_minutes"] = night["asleep_minutes"] + 95
    last = by_title(metric_cards(daily, sleep))["昨晚睡眠"]["data"]
    main = hours_minutes(night["asleep_minutes"])
    assert last["value"] == hours_minutes(night["total_minutes"])
    assert last["label"].startswith(
        f"主睡眠 {main}（{night['onset']}–{night['wake']}） · 小睡 1 次 1小时35分"
    )


async def test_cards_over_http_with_the_briefing(service):
    await enable_health(service)
    try:
        cards = await service.plugins.cards("health")
        assert [c["title"] for c in cards] == TITLES  # no briefing written yet
        assert by_title(cards)["静息心率"]["data"]["value"] == 55
        assert by_title(cards)["昨晚睡眠"]["data"]["value"] == "7小时40分"
        client = TestClient(create_app(service=service), headers=client_for(service).headers)
        assert client.get("/api/v1/plugins/health/cards").json() == cards
        today = client.get("/api/v1/today").json()
        assert [c["title"] for c in today["cards"]] == TITLES
        await service.plugins.patch("health", PluginPatch(enabled=False))
        assert client.get("/api/v1/plugins/health/cards").json() == []
    finally:
        await service.close()


def test_hrv_card_follows_the_preferred_measure(tmp_path):
    engine, _ = build(tmp_path, rmssd_days=30)
    today = engine.daily(END, 1, today=END)["days"][-1]
    assert today["hrv_preferred"] == "hrv_ln_rmssd"
    shown = by_title(health_cards(engine, END))
    hrv = shown["HRV"]["data"]
    assert hrv["unit"] == "毫秒（RMSSD）"
    assert hrv["value"] == round(today["metrics"]["hrv_ln_rmssd"]["rmssd_ms"])
    titles = [item["title"] for item in shown["健康预警"]["data"]["items"]]
    assert titles == ["静息心率高于基线", "HRV 低于基线"]
    assert "RMSSD" in shown["健康预警"]["data"]["items"][1]["subtitle"]


def test_readiness_card_without_enough_parts(synthetic):
    daily = copy.deepcopy(synthetic.daily(END, 3, today=END))
    sleep = synthetic.sleep(END, 7, today=END)
    today = daily["days"][-1]["readiness"]
    today["score"] = None
    for name in ("hrv", "resting_hr", "training_load"):
        today["components"][name].update(score=None, reason="baseline_insufficient")
    card = by_title(metric_cards(daily, sleep))["今日准备度"]["data"]
    assert card["value"] == "数据不足" and "delta" not in card
    assert card["label"].startswith("至少要 2 项有数据 · 夜间 HRV 基线数据还不够")
