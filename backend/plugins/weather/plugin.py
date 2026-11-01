"""天气: the forecast for where the user is, what to take and wear, and a morning reminder.

Part of 助理Bot (it has her sticker), the personal assistant: the forecast is read every
two hours (forecast.py, numbers and advice by code); every morning (`brief_cron`) an Agent,
DeepSeek first by default, reads the forecast, today's todos and courses and writes a short
reminder with weather_brief_write. Cards, 天气.md in the day's folder and two MCP tools.
"""

import asyncio
import contextlib
from datetime import date, datetime, timedelta

from core.plugins import Plugin as BasePlugin
from core.prompts import render
from plugins.school.common import arguments_of, text, zone_of
from plugins.weather import forecast

BOT = "助理Bot"
FORECAST, BRIEFS = "forecast", "briefs"
PROBLEMS = {
    "weather_unreachable": "连不上天气服务（open-meteo.com），稍后会再读。",
    "weather_request_failed": "天气服务这次没有回答，稍后会再读。",
    "weather_bad_answer": "天气服务的回答不完整，稍后会再读。",
    "weather_place_missing": "还没设置地点：在插件设置里填 latitude、longitude（地点的经纬度）。",
}


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.place = config.get("place", "上海")
        self.coordinates = (
            (config["latitude"], config["longitude"])
            if "latitude" in config and "longitude" in config
            else None
        )
        self.brief_agent = config.get("brief_agent", "deepseek")
        self.brief_cron = config.get("brief_cron", "30 6 * * *")
        self.reading = asyncio.create_task(self.first_read())

    async def first_read(self):
        with contextlib.suppress(Exception):
            await self.refresh()

    async def teardown(self):
        task = getattr(self, "reading", None)
        if task:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    def now(self):
        return datetime.now(zone_of(self.context))

    def jobs(self):
        return {
            "forecast": {"cron": "5 */2 * * *", "callback": self.refresh},
            "brief": {"cron": self.brief_cron, "callback": self.brief},
        }

    def mcp_tools(self):
        return {"weather_forecast": self.weather_forecast, "weather_brief_write": self.brief_write}

    # Forecast -----------------------------------------------------------------------------

    async def refresh(self):
        stored = self.context.get(FORECAST, {})
        now = self.now()
        try:
            if self.coordinates is None:
                raise forecast.WeatherError("weather_place_missing")
            data = await asyncio.to_thread(
                forecast.fetch, *self.coordinates, self.context.store.settings.timezone
            )
        except forecast.WeatherError as error:
            code = str(error)
            self.context.put(FORECAST, {**stored, "error": code, "checked_at": now.isoformat()})
            if code != stored.get("error"):
                raise
            return
        self.context.put(FORECAST, {"data": data, "read_at": now.isoformat(), "error": None})

    def days(self):
        data = self.context.get(FORECAST, {}).get("data")
        return {day.date: day for day in forecast.days(data)} if data else {}

    # Morning reminder ---------------------------------------------------------------------

    def briefs(self):
        return self.context.get(BRIEFS, {})

    async def brief(self):
        """The Agent writes today's reminder (once a day; a failed day is tried again at the
        next scheduled time or when run by hand)."""
        today = self.now().date()
        if today.isoformat() in self.briefs():
            return
        if self.context.run_agent is None:
            raise RuntimeError("agent_runner_unavailable")
        if today not in self.days():
            await self.refresh()
        prompt = render(
            "weather_brief",
            date=today.isoformat(),
            place=self.place,
            local_now=self.now().isoformat(),
        )
        await self.context.run_agent(
            "plugin:weather:brief",
            prompt,
            succeeded=lambda: today.isoformat() in self.briefs(),
            incomplete_error="brief_not_written",
            harness=self.brief_agent,
        )

    async def brief_write(self, arguments):
        """Write the morning reminder of a day, shown first on 助理Bot's page and in the
        day's folder. arguments: {"date": "2026-10-26", "text": "Markdown, at most 400
        characters"}."""
        arguments_of(arguments, {"date", "text"}, required=("date", "text"))
        try:
            day = date.fromisoformat(text(arguments, "date", 10))
        except ValueError as error:
            raise ValueError("date must be like 2026-10-26") from error
        # The prompt asks for at most 260; the sections' line breaks and marks take room too.
        body = text(arguments, "text", 400)
        briefs = self.briefs()
        briefs[day.isoformat()] = {"text": body, "written_at": self.now().isoformat()}
        # A month is enough to look back on.
        keep = sorted(briefs)[-31:]
        self.context.put(BRIEFS, {key: briefs[key] for key in keep})
        return {"accepted": True}

    async def weather_forecast(self, arguments):
        """The forecast for today, tomorrow and the day after where the user is, with the
        advice the program worked out (umbrella, clothes, sunscreen, wind). All numbers are
        already computed; use them as they are. arguments: {} (none)."""
        arguments_of(arguments or {}, set())
        stored = self.context.get(FORECAST, {})
        return {
            "place": self.place,
            "read_at": stored.get("read_at"),
            "problem": stored.get("error"),
            "days": [day.view() for day in self.days().values()],
        }

    # Cards and the day's file -------------------------------------------------------------

    async def cards(self):
        return await asyncio.to_thread(self._cards, self.now().date())

    def _cards(self, today):
        stored = self.context.get(FORECAST, {})
        days, cards = self.days(), []
        brief = self.briefs().get(today.isoformat())
        if brief:
            cards.append({"type": "text", "title": "今日提醒", "data": {"text": brief["text"]}})
        if stored.get("error") and (today not in days):
            cards.append(
                {
                    "type": "text",
                    "title": "天气没读到",
                    "data": {"text": PROBLEMS.get(stored["error"], "读取失败。")},
                }
            )
            return cards
        if today not in days:
            cards.append(
                {"type": "text", "title": "天气", "data": {"text": "还没读到天气，稍等一下。"}}
            )
            return cards
        day = days[today]
        cards.append(
            {
                "type": "stat",
                "title": f"{self.place}今天",
                "data": {
                    "value": day.temperatures,
                    "unit": "°C",
                    "label": f"{day.text} · 降水概率 {day.rain_chance}%",
                    "symbol": day.symbol,
                },
            }
        )
        cards.append(
            {
                "type": "list",
                "title": "出门提醒",
                "data": {
                    "items": [{"title": t, "subtitle": d} for t, d in day.advice()],
                },
            }
        )
        tomorrow = days.get(today + timedelta(days=1))
        if tomorrow:
            umbrella = tomorrow.umbrella()
            cards.append(
                {
                    "type": "stat",
                    "title": "明天",
                    "data": {
                        "value": tomorrow.temperatures,
                        "unit": "°C",
                        "label": f"{tomorrow.text} · 降水概率 {tomorrow.rain_chance}%"
                        + (f" · {umbrella[0]}" if umbrella else ""),
                        "symbol": tomorrow.symbol,
                    },
                }
            )
        return cards

    async def daily(self, day):
        """天气.md in the day's folder (core/daily.py)."""
        return await asyncio.to_thread(self._daily, day)

    def _daily(self, day):
        found = self.days().get(day)
        brief = self.briefs().get(day.isoformat())
        if not found and not brief:
            return None
        lines = []
        if brief:
            lines += ["## 今日提醒", "", brief["text"], ""]
        if found:
            lines += [
                f"## {self.place}的天气",
                f"- {found.text}，{found.temperatures}°C，体感 {found.felt_low:.0f}–"
                f"{found.felt_high:.0f}°C，降水概率 {found.rain_chance}%",
                *(f"- {title}：{detail}" for title, detail in found.advice()),
            ]
        umbrella = found.umbrella() if found else None
        summary = (
            f"{found.text} {found.temperatures}°C" + (f"，{umbrella[0]}" if umbrella else "")
            if found
            else "今日提醒已写好"
        )
        return {"file": "天气.md", "bot": BOT, "summary": summary, "body": "\n".join(lines)}
