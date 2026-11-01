"""The forecast from Open-Meteo (open-meteo.com: free, no key) and what to do about it.

Numbers come from the forecast and the advice from fixed rules here, never from a model:
take an umbrella when rain is likely, what to wear by the felt temperature, sunscreen when the
UV index is high. The Agent only words the morning reminder from these (plugin.py).
"""

from dataclasses import dataclass
from datetime import date, datetime

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
    "apparent_temperature_min,precipitation_probability_max,precipitation_sum,uv_index_max,"
    "wind_speed_10m_max"
)
HOURLY = "precipitation_probability,temperature_2m"

# WMO weather interpretation codes (open-meteo.com/en/docs).
CODES = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨", 85: "阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}  # fmt: skip

# The day's sky as an SF Symbol for the cards, by the words in its text above (first match).
SYMBOLS = (
    ("雷", "cloud.bolt.rain.fill"),
    ("雪", "cloud.snow.fill"),
    ("冻", "cloud.sleet.fill"),
    ("毛毛雨", "cloud.drizzle.fill"),
    ("小雨", "cloud.drizzle.fill"),
    ("阵雨", "cloud.sun.rain.fill"),
    ("雨", "cloud.rain.fill"),
    ("雾", "cloud.fog.fill"),
    ("阴", "cloud.fill"),
    ("云", "cloud.sun.fill"),
    ("晴", "sun.max.fill"),
)

# What to wear by the day's felt temperature (the mean of its felt high and low), from warm to
# cold: (at least, advice).
CLOTHES = (
    (28, "短袖短裤，注意防暑"),
    (23, "短袖或薄长袖"),
    (18, "长袖衬衫或薄外套"),
    (13, "外套或卫衣"),
    (8, "毛衣加厚外套"),
    (3, "棉服或羽绒服"),
    (-100, "厚羽绒服，戴围巾手套"),
)
RAIN_LIKELY, RAIN_POSSIBLE = 50, 30  # precipitation probability, %
WIDE_SWING = 10  # °C between the day's high and low
STRONG_UV = 6
STRONG_WIND = 38  # km/h
DAYTIME = range(7, 23)  # hours whose rain matters for going out


def http_get(url, **options):
    return httpx.get(url, **options)


class WeatherError(RuntimeError):
    """A fixed code for runs and the cards."""


@dataclass(frozen=True)
class Day:
    date: date
    text: str
    high: float
    low: float
    felt_high: float
    felt_low: float
    rain_chance: int
    rain_mm: float
    uv: float
    wind: float
    rain_hours: tuple[int, ...]  # daytime hours with a likely chance of rain

    @property
    def symbol(self):
        return next((symbol for word, symbol in SYMBOLS if word in self.text), "cloud.sun.fill")

    @property
    def temperatures(self):
        return f"{self.low:.0f}–{self.high:.0f}"

    def umbrella(self):
        """(advice, detail) or None when no umbrella is needed. A likely chance of rain means an
        umbrella; a possible one, or a day the model expects a little rain on although the
        chance stays low (drizzle), a folding one."""
        hours = hour_ranges(self.rain_hours)
        if self.rain_chance >= RAIN_LIKELY:
            when = f"{hours}，" if hours else ""
            return "要带伞", f"{when}降水概率 {self.rain_chance}%"
        if self.rain_chance >= RAIN_POSSIBLE:
            return "带把折叠伞", f"可能下雨，降水概率 {self.rain_chance}%"
        if self.rain_mm >= 1:
            return "带把折叠伞", f"预报有零星小雨，全天约 {self.rain_mm:.1f} 毫米"
        return None

    def clothes(self):
        felt = (self.felt_high + self.felt_low) / 2
        advice = next(text for floor, text in CLOTHES if felt >= floor)
        detail = f"体感 {self.felt_low:.0f}–{self.felt_high:.0f}°C"
        if self.high - self.low >= WIDE_SWING:
            detail += "，早晚温差大，带件外套"
        return advice, detail

    def advice(self):
        """[(title, detail)] for going out: umbrella, clothes, sunscreen, wind."""
        items = []
        if umbrella := self.umbrella():
            items.append(umbrella)
        else:
            items.append(("不用带伞", f"降水概率 {self.rain_chance}%"))
        items.append(self.clothes())
        if self.uv >= STRONG_UV:
            items.append(("注意防晒", f"紫外线指数 {self.uv:.0f}"))
        if self.wind >= STRONG_WIND:
            items.append(("风大", f"最大风速 {self.wind:.0f} 公里/时"))
        return items

    def view(self):
        return {
            "date": self.date.isoformat(),
            "weather": self.text,
            "temperatures": self.temperatures,
            "temperature_c": {"low": self.low, "high": self.high},
            "felt_c": {"low": self.felt_low, "high": self.felt_high},
            "rain_chance_percent": self.rain_chance,
            "rain_mm": self.rain_mm,
            "rain_hours": hour_ranges(self.rain_hours),
            "uv_index": self.uv,
            "wind_kmh": self.wind,
            "advice": [{"title": t, "detail": d} for t, d in self.advice()],
        }


def hour_ranges(hours):
    """(15, 16, 17, 20) -> "15:00–18:00、20:00–21:00"."""
    ranges, start, last = [], None, None
    for hour in sorted(hours):
        if start is None:
            start = last = hour
        elif hour == last + 1:
            last = hour
        else:
            ranges.append((start, last))
            start = last = hour
    if start is not None:
        ranges.append((start, last))
    return "、".join(f"{a:02d}:00–{b + 1:02d}:00" for a, b in ranges)


def fetch(latitude, longitude, timezone, get=None):
    """The raw forecast for three days (Open-Meteo's JSON)."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": DAILY,
        "hourly": HOURLY,
        "timezone": timezone,
        "forecast_days": 3,
    }
    try:
        response = (get or http_get)(URL, params=params, timeout=20)
    except httpx.HTTPError as error:
        raise WeatherError("weather_unreachable") from error
    if response.status_code != 200:
        raise WeatherError("weather_request_failed")
    data = response.json()
    if not isinstance(data.get("daily"), dict) or not data["daily"].get("time"):
        raise WeatherError("weather_bad_answer")
    return data


def days(data):
    """The forecast's days, with each day's rainy daytime hours."""
    daily, hourly = data["daily"], data.get("hourly") or {}
    rainy = {}
    for stamp, chance in zip(
        hourly.get("time") or [], hourly.get("precipitation_probability") or [], strict=False
    ):
        moment = datetime.fromisoformat(stamp)
        if chance is not None and chance >= RAIN_LIKELY and moment.hour in DAYTIME:
            rainy.setdefault(moment.date(), []).append(moment.hour)

    def value(name, index, default=0.0):
        found = (daily.get(name) or [])[index : index + 1]
        return float(found[0]) if found and found[0] is not None else default

    result = []
    for index, stamp in enumerate(daily["time"]):
        day = date.fromisoformat(stamp)
        code = int(value("weather_code", index, -1))
        result.append(
            Day(
                date=day,
                text=CODES.get(code, "未知"),
                high=value("temperature_2m_max", index),
                low=value("temperature_2m_min", index),
                felt_high=value("apparent_temperature_max", index),
                felt_low=value("apparent_temperature_min", index),
                rain_chance=int(value("precipitation_probability_max", index)),
                rain_mm=value("precipitation_sum", index),
                uv=value("uv_index_max", index),
                wind=value("wind_speed_10m_max", index),
                rain_hours=tuple(rainy.get(day, [])),
            )
        )
    return result
