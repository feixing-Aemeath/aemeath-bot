"""All monetary calculations use integer cents and the runtime timezone."""

import calendar
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from apscheduler.triggers.cron import CronTrigger

from plugins.finance.categories import CATEGORIES

PRIVATE = "财务主题必须没有读者"
SOURCES = {"wechat": "微信", "alipay": "支付宝", "cmb": "招商银行"}
SUFFIXES = {".csv", ".xlsx", ".zip", ".pdf"}


class FinanceError(ValueError):
    """Fixed public reasons only. Never construct this from input or helper output."""


def money(value, *, signed=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        text = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "")
        amount = Decimal(text)
        if not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError
        cents = int(amount * 100)
        if (not signed and cents <= 0) or abs(cents) > 10**12:
            raise ValueError
        return cents
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise FinanceError("金额格式无效") from None


def yuan(cents):
    return f"{cents / 100:.2f}"


def timestamp(value, zone):
    try:
        text = str(value).strip().replace("/", "-")
        text = re.sub(r"[年月]", "-", text).replace("日", "")
        # Excel's default (1900) date system. 1904 workbooks are rejected by the parser.
        if re.fullmatch(r"\d{5}(?:\.\d+)?", text):
            result = datetime(1899, 12, 30) + timedelta(days=float(text))
        else:
            result = datetime.fromisoformat(text)
        return (result.replace(tzinfo=zone) if result.tzinfo is None else result).astimezone(zone)
    except (ValueError, OverflowError, TypeError):
        raise FinanceError("交易日期格式无效") from None


def anchor(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def shift_month(day, offset, allowance_day):
    index = day.year * 12 + day.month - 1 + offset
    year, month = divmod(index, 12)
    return anchor(year, month + 1, allowance_day)


def cycle(day, allowance_day):
    start = anchor(day.year, day.month, allowance_day)
    if day < start:
        start = shift_month(start, -1, allowance_day)
    return start, shift_month(start, 1, allowance_day) - timedelta(days=1)


def allowance_anchor(day, allowance_day):
    here = anchor(day.year, day.month, allowance_day)
    return min(
        (shift_month(here, i, allowance_day) for i in (-1, 0, 1)), key=lambda d: abs(d - day)
    )


def weekly_minute(value):
    match = re.fullmatch(r"(mon|tue|wed|thu|fri|sat|sun) (\d{2}):(\d{2})", value)
    if not match:
        raise FinanceError("在家时间格式无效")
    weekday, hour, minute = match.groups()
    hour, minute = int(hour), int(minute)
    if hour > 24 or minute > 59 or (hour == 24 and minute):
        raise FinanceError("在家时间格式无效")
    return ("mon tue wed thu fri sat sun".split().index(weekday) * 24 + hour) * 60 + minute


def at_home(at, config):
    start, end = (
        weekly_minute(config.get(k, v)) % 10080
        for k, v in (("home_from", "fri 17:00"), ("home_until", "sun 24:00"))
    )
    now = (at.weekday() * 24 + at.hour) * 60 + at.minute
    return start <= now < end if start < end else now >= start or now < end


def validate(config):
    for key, default in (
        ("cron", "*/5 * * * *"),
        ("reimburse_cron", "0 21 * * sun"),
        ("report_cron", "0 9 * * *"),
    ):
        CronTrigger.from_crontab(config.get(key, default))
    money(config.get("allowance_amount", 2000))
    for key, default in (("home_from", "fri 17:00"), ("home_until", "sun 24:00")):
        weekly_minute(config.get(key, default))
    if any(v not in CATEGORIES or not k.strip() for k, v in config.get("rules", {}).items()):
        raise FinanceError("分类规则无效")
