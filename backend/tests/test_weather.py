"""天气 (plugins/weather): the forecast, the advice worked out by code, and the morning reminder."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from core.mcp_server import create_mcp
from core.models import PluginPatch
from plugins.weather import forecast
from tests.test_school import call

ZONE = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 26, 6, 30, tzinfo=ZONE)
HEFEI = {"place": "上海", "latitude": 31.86, "longitude": 117.25}


def answer(rain=(0, 0, 80), codes=(2, 61, 63), lows=(9, 12, 14), highs=(21, 18, 17)):
    hours = [f"2026-10-{26 + d}T{h:02d}:00" for d in range(3) for h in range(24)]
    chances = [0] * 72
    for hour in (15, 16, 17):  # tomorrow afternoon
        chances[24 + hour] = 70
    return {
        "daily": {
            "time": ["2026-10-26", "2026-10-27", "2026-10-28"],
            "weather_code": list(codes),
            "temperature_2m_max": list(highs),
            "temperature_2m_min": list(lows),
            "apparent_temperature_max": [h - 1 for h in highs],
            "apparent_temperature_min": [low - 2 for low in lows],
            "precipitation_probability_max": [10, 70, rain[2]],
            "precipitation_sum": [0, 3.2, 8],
            "uv_index_max": [7, 2, 1],
            "wind_speed_10m_max": [12, 20, 40],
        },
        "hourly": {"time": hours, "precipitation_probability": chances},
    }


def test_the_advice_comes_from_rules():
    days = forecast.days(answer())
    today, tomorrow, after = days
    assert today.text == "多云" and today.temperatures == "9–21"
    assert today.umbrella() is None
    assert today.advice() == [
        ("不用带伞", "降水概率 10%"),
        ("外套或卫衣", "体感 7–20°C，早晚温差大，带件外套"),
        ("注意防晒", "紫外线指数 7"),
    ]
    assert tomorrow.umbrella() == ("要带伞", "15:00–18:00，降水概率 70%")
    assert after.advice()[-1] == ("风大", "最大风速 40 公里/时")
    assert forecast.hour_ranges((8, 9, 13, 20, 21)) == "08:00–10:00、13:00–14:00、20:00–22:00"
    possible = forecast.days(answer(rain=(0, 0, 35)))[2]
    possible = forecast.Day(**{**possible.__dict__, "rain_mm": 0})
    assert possible.umbrella() == ("带把折叠伞", "可能下雨，降水概率 35%")
    drizzle = forecast.Day(**{**possible.__dict__, "rain_chance": 10, "rain_mm": 1.9})
    assert drizzle.umbrella() == ("带把折叠伞", "预报有零星小雨，全天约 1.9 毫米")


def test_fetch_asks_open_meteo_and_rejects_bad_answers():
    asked = []

    def get(url, params, timeout):
        asked.append((url, params))
        return httpx.Response(200, json=answer())

    assert (
        forecast.fetch(31.86, 117.25, "Asia/Shanghai", get=get)["daily"]["time"][0] == "2026-10-26"
    )
    url, params = asked[0]
    assert url == forecast.URL and params["timezone"] == "Asia/Shanghai"
    assert params["forecast_days"] == 3 and "precipitation_probability_max" in params["daily"]
    with pytest.raises(forecast.WeatherError, match="^weather_request_failed$"):
        forecast.fetch(0, 0, "UTC", get=lambda *a, **k: httpx.Response(500))
    with pytest.raises(forecast.WeatherError, match="^weather_bad_answer$"):
        forecast.fetch(0, 0, "UTC", get=lambda *a, **k: httpx.Response(200, json={}))


@pytest.fixture
def weather(monkeypatch):
    shown = {"data": answer()}

    def fetch(latitude, longitude, timezone, get=None):
        if isinstance(shown["data"], str):
            raise forecast.WeatherError(shown["data"])
        return shown["data"]

    monkeypatch.setattr(forecast, "fetch", fetch)
    return shown


async def enabled(service, config=HEFEI):
    await service.plugins.patch("weather", PluginPatch(enabled=True, config=config))
    plugin = service.plugins.instances["weather"]
    await plugin.reading
    plugin.now = lambda: NOW
    return plugin


async def test_cards_tools_and_the_days_file(service, weather):
    mcp = create_mcp(service)
    await service.start()
    try:
        plugin = await enabled(service)
        cards = await plugin.cards()
        assert [c["title"] for c in cards] == ["上海今天", "出门提醒", "明天"]
        assert cards[0]["data"] == {
            "value": "9–21",
            "unit": "°C",
            "label": "多云 · 降水概率 10%",
            "symbol": "cloud.sun.fill",
        }
        assert cards[1]["data"]["items"][0] == {"title": "不用带伞", "subtitle": "降水概率 10%"}
        assert cards[2]["data"]["label"] == "小雨 · 降水概率 70% · 要带伞"

        result = await call(mcp, "weather_forecast", {})
        assert result["place"] == "上海" and len(result["days"]) == 3
        assert result["days"][1]["rain_hours"] == "15:00–18:00"
        assert result["days"][0]["temperatures"] == "9–21"  # rounded, as on the card

        written = await call(
            mcp,
            "weather_brief_write",
            {"date": "2026-10-26", "text": "今天多云，9–21°C，不用带伞。"},
        )
        assert written == {"accepted": True}
        # The sections' marks and line breaks fit; an essay does not.
        with pytest.raises(Exception, match="text"):
            await plugin.brief_write({"date": "2026-10-26", "text": "字" * 401})
        assert [c["title"] for c in await plugin.cards()][0] == "今日提醒"

        await service.daily.write(date(2026, 10, 26))
        today = service.memory.read("每日/2026-10-26/今天.md")["content"]
        assert "- 助理Bot · [天气](天气.md)：多云 9–21°C" in today
        assert today.index("[待办]") < today.index("[天气]")
        file = service.memory.read("每日/2026-10-26/天气.md")["content"]
        assert "## 今日提醒\n\n今天多云" in file and "- 注意防晒：紫外线指数 7" in file
    finally:
        await service.close()


async def test_the_morning_brief_runs_the_chosen_agent_once_a_day(service, weather):
    await service.start()
    try:
        plugin = await enabled(service, {**HEFEI, "brief_agent": "codex"})
        calls = []

        async def run_agent(job, prompt, succeeded=None, incomplete_error=None, harness=None):
            calls.append((job, harness, prompt))
            await plugin.brief_write({"date": "2026-10-26", "text": "早上好"})
            assert succeeded()
            return harness

        plugin.context.run_agent = run_agent
        await plugin.brief()
        await plugin.brief()  # written already: not again
        assert len(calls) == 1
        job, harness, prompt = calls[0]
        assert (job, harness) == ("plugin:weather:brief", "codex")
        assert "2026-10-26" in prompt and "上海" in prompt and "weather_brief_write" in prompt
    finally:
        await service.close()


async def test_problems_show_on_the_cards_and_are_logged_once(service, weather):
    await service.start()
    try:
        weather["data"] = "weather_unreachable"
        plugin = await enabled(service)
        cards = await plugin.cards()
        assert cards[0]["title"] == "天气没读到" and "open-meteo" in cards[0]["data"]["text"]
        await plugin.refresh()  # the same problem again: no second failure
        without = await enabled(service, {"place": "上海"})
        assert "经纬度" in (await without.cards())[0]["data"]["text"]
    finally:
        await service.close()


def test_the_cards_draw_the_days_sky():
    def sky(text):
        return forecast.Day(date(2026, 10, 28), text, 20, 10, 20, 10, 0, 0, 0, 0, ()).symbol

    assert sky("晴") == "sun.max.fill" and sky("晴间多云") == "cloud.sun.fill"
    assert sky("小雨") == "cloud.drizzle.fill" and sky("中阵雨") == "cloud.sun.rain.fill"
    assert sky("雷阵雨伴冰雹") == "cloud.bolt.rain.fill" and sky("未知") == "cloud.sun.fill"
