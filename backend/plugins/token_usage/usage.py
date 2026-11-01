"""Cards and the daily summary from run_usage, the tokens each Agent run of the core reported,
and from local_usage, your own use of the CLIs read from their session files (sessions.py).

A run's tokens are its input (cached input included) plus its output, the same way for both
CLIs (harness.base.Usage). Days are local days in the core's time zone. A balance is what was
topped up minus the cost of every run since that day: Claude Code reports a cost; for Codex the
cost is estimated from the configured prices, and runs without either count as free. Your own
use is usually on a subscription, so it never counts against a balance. The all-time total adds
the counts you enter yourself for models nothing here reads (manual_totals, such as DeepSeek).
"""

from datetime import UTC, date, datetime, time, timedelta

from plugins.token_usage import sessions

HARNESSES = {"claude_code": "Claude Code", "codex": "Codex"}
# The model families on the all-time ring, in its order.
PROVIDERS = {"claude_code": "Claude", "codex": "GPT", "deepseek": "DeepSeek"}
BALANCE_TITLES = {"claude_code": "Claude 余额", "codex": "Codex 余额"}
JOBS = {
    "extract_todos": "提炼待办",
    "plugin:health:morning_report": "晨间简报和周报",
    "plugin:health:weekly_report": "晨间简报和周报",
    "own:claude_code": "自己用 Claude Code",
    "own:codex": "自己用 Codex",
}
SERIES = (("助手任务", False), ("自己用的", True))
# How far back the cards look: this month and the same days of last month.
HISTORY_DAYS = 62
CHART_DAYS = 7
EMPTY = (
    "还没有 token 记录。助手的 Agent 完成一次任务、或者本机的 Claude Code、Codex 会话记录被读到之后，"
    "这里会显示每天的 token 消耗。"
)


def job_name(job):
    """A task's name for people; every Bot's chat replies (chat:<bot_id>) count as one task."""
    if job in JOBS:
        return JOBS[job]
    return "和 Bot 聊天" if job.startswith("chat:") else job


def short(n):
    """210k, 21.5k, 1.82M, 4.65B: three significant digits at most."""
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= size:
            value = n / size
            digits = 0 if value >= 100 else 1 if value >= 10 else 2
            text = f"{value:.{digits}f}"
            return (text.rstrip("0").rstrip(".") if digits else text) + suffix
    return str(n)


def money(value):
    return f"-${-value:.2f}" if value < 0 else f"${value:.2f}"


def percent(new, old):
    change = round((new - old) / old * 100)
    return f"+{change}%" if change > 0 else f"{change}%"


def tokens(row):
    return row["input_tokens"] + row["output_tokens"]


def cost(row, prices):
    if row["cost_usd"] is not None:
        return row["cost_usd"]
    price = prices.get(row["harness"])
    if not price:
        return 0.0
    cached = row["cached_input_tokens"]
    return (
        (row["input_tokens"] - cached) * price["input"]
        + cached * price.get("cached_input", price["input"])
        + row["output_tokens"] * price["output"]
    ) / 1_000_000


def card(card_type, title, **data):
    return {
        "type": card_type,
        "title": title,
        "data": {k: v for k, v in data.items() if v is not None},
    }


class Ledger:
    def __init__(self, db, zone, today, config):
        self.db, self.zone, self.today = db, zone, today
        self.balances = config.get("balances", {})
        self.prices = config.get("prices", {})
        self.low_balance = config.get("low_balance_usd", 15)
        start = today - timedelta(days=HISTORY_DAYS)
        for balance in self.balances.values():
            start = min(start, date.fromisoformat(balance["as_of"]))
        since = self.utc(start)
        agent = [{**row, "own": False} for row in db.usage_since(since)]
        own = [
            {**row, "own": True, "job": f"own:{row['harness']}", "cost_usd": None}
            for row in sessions.usage_since(db, since)
        ]
        self.rows = sorted(
            ({**row, "day": self.day(row["recorded_at"])} for row in agent + own),
            key=lambda row: row["recorded_at"],
        )
        self.has_own = bool(own)
        self.manual = config.get("manual_totals", [])

    def utc(self, day):
        return datetime.combine(day, time(), self.zone).astimezone(UTC).isoformat()

    def day(self, text):
        return datetime.fromisoformat(text).astimezone(self.zone).date()

    def total(self, first, last, harness=None, own=None):
        return sum(
            tokens(r)
            for r in self.rows
            if first <= r["day"] <= last
            and harness in (None, r["harness"])
            and own in (None, r["own"])
        )

    # Cards

    def cards(self):
        everything = self.everything()
        if not self.rows and not self.balances and not everything:
            return [card("list", "Token 消耗", items=[EMPTY])]
        cards = [self.total_card(everything)] if everything else []
        cards += [self.balance_card(h) for h in HARNESSES if h in self.balances]
        cards.append(self.month_card())
        if self.has_own:
            cards.append(self.own_card())
        return [*cards, self.chart_card()]

    def counted(self, since=""):
        """Tokens per model family recorded at or after the UTC time `since`: the assistant's
        runs and your own use."""
        found = {}
        agent = self.db.rows(
            "SELECT harness, sum(input_tokens+output_tokens) AS n FROM run_usage "
            "WHERE recorded_at>=? GROUP BY harness",
            (since,),
        )
        for harness, n in [
            *((row["harness"], row["n"]) for row in agent),
            *sessions.totals(self.db, since).items(),
        ]:
            name = PROVIDERS.get(harness, harness)
            found[name] = found.get(name, 0) + n
        return found

    def everything(self):
        """All-time tokens per model family, with what you entered yourself; families with
        none are left out.

        A hand-entered total for a family counted here (such as GPT from the ChatGPT profile
        page) already includes what was counted before its as_of day, so it replaces that; only
        what was counted from as_of on is added. Without as_of it replaces all of it.
        """
        found = {name: 0 for name in PROVIDERS.values()}
        for name, n in self.counted().items():
            found[name] = found.get(name, 0) + n
        families = {name.casefold(): name for name in PROVIDERS.values()}
        for entry in self.manual:
            name = families.get(entry["name"].casefold(), entry["name"])
            if name in PROVIDERS.values():
                since = self.utc(date.fromisoformat(entry["as_of"])) if "as_of" in entry else "~"
                found[name] = self.counted(since).get(name, 0)
            found[name] = found.get(name, 0) + entry["tokens"]
        return {name: n for name, n in found.items() if n}

    def total_card(self, everything):
        return card(
            "chart",
            "总共使用的 token",
            kind="donut",
            value=short(sum(everything.values())),
            unit="tokens",
            label=" · ".join(f"{name} {short(n)}" for name, n in everything.items()),
            points=[{"x": name, "y": n} for name, n in everything.items()],
        )

    def balance(self, harness):
        """Remaining balance and the average daily cost over the last 7 full days."""
        since = date.fromisoformat(self.balances[harness]["as_of"])
        spent = sum(
            cost(r, self.prices)
            for r in self.rows
            if r["harness"] == harness and not r["own"] and r["day"] >= since
        )
        first = max(since, self.today - timedelta(days=CHART_DAYS))
        days = (self.today - first).days
        recent = sum(
            cost(r, self.prices)
            for r in self.rows
            if r["harness"] == harness and not r["own"] and first <= r["day"] < self.today
        )
        return self.balances[harness]["amount_usd"] - spent, recent / days if days else 0.0

    def runway(self, remaining, rate):
        if remaining <= 0:
            return "余额已用完"
        if rate <= 0:
            return "最近 7 天没有花费"
        return f"按最近 7 天的用量还能用 {round(remaining / rate)} 天左右"

    def balance_card(self, harness):
        remaining, rate = self.balance(harness)
        return card(
            "stat",
            BALANCE_TITLES[harness],
            value=money(remaining),
            label=self.runway(remaining, rate),
            delta=f"每天约 -${rate:.2f}" if rate > 0 else None,
            trend="down" if rate > 0 else None,
        )

    def month_card(self, own=None, title="本月总消耗"):
        first = self.today.replace(day=1)
        total = self.total(first, self.today, own=own)
        parts = [
            f"{name} {short(n)}"
            for harness, name in HARNESSES.items()
            if (n := self.total(first, self.today, harness, own))
        ]
        last_first = (first - timedelta(days=1)).replace(day=1)
        last_end = min(last_first + timedelta(days=self.today.day - 1), first - timedelta(days=1))
        before = self.total(last_first, last_end, own=own)
        delta = trend = None
        if before:
            delta = "比上月同期 " + percent(total, before)
            trend = "up" if total > before else "down" if total < before else "flat"
        return card(
            "stat",
            title,
            value=short(total),
            unit="tokens",
            label=" · ".join(parts) or "本月还没有消耗",
            delta=delta,
            trend=trend,
        )

    def own_card(self):
        """The part of the month you used yourself, read from the CLIs' session files."""
        return self.month_card(own=True, title="本月自己用的")

    def chart_card(self):
        days = [self.today - timedelta(days=n) for n in range(CHART_DAYS - 1, -1, -1)]

        # Thousands, or millions once a day reaches ten million.
        peak = max(self.total(d, d) for d in days)
        size, unit = (1_000_000, "百万 tokens") if peak >= 10_000_000 else (1_000, "千 tokens")

        def points(own=None):
            return [
                {"x": f"{d.month}/{d.day}", "y": round(self.total(d, d, own=own) / size, 1)}
                for d in days
            ]

        title = f"最近 {CHART_DAYS} 天消耗"
        if not self.has_own:
            return card("chart", title, kind="line", unit=unit, points=points())
        series = [{"name": name, "points": points(own)} for name, own in SERIES]
        return card("chart", title, kind="line", unit=unit, series=series)

    # Daily summary

    def summary(self):
        """Today's digest: yesterday's total, quota fallbacks yesterday and low balances."""
        yesterday = self.today - timedelta(days=1)
        lines = []
        total = self.total(yesterday, yesterday)
        if total:
            before = self.total(yesterday - timedelta(days=1), yesterday - timedelta(days=1))
            line = f"昨天一共用了 **{short(total)}** tokens"
            if self.has_own:
                line += f"（自己用的 {short(self.total(yesterday, yesterday, own=True))}）"
            if before:
                change = round((total - before) / before * 100)
                if change:
                    line += f"，比前天{'多' if change > 0 else '少'} {abs(change)}%"
            lines.append(line + "。")
        lines += self.fallbacks(yesterday)
        for harness in HARNESSES:
            if harness not in self.balances:
                continue
            remaining, rate = self.balance(harness)
            if remaining < self.low_balance:
                name = BALANCE_TITLES[harness].removesuffix("余额")
                lines.append(
                    f"{name}余额低于 {money(self.low_balance)}，{self.runway(remaining, rate)}。"
                )
        return "\n".join(lines)

    def fallbacks(self, day):
        """Runs that ran out of quota on `day`, and whether another Agent finished the job."""
        start, end = self.utc(day), self.utc(day + timedelta(days=1))
        runs = self.db.rows(
            "SELECT job,harness,status,error FROM runs "
            "WHERE harness IS NOT NULL AND started_at>=? AND started_at<? ORDER BY started_at",
            (start, end),
        )
        lines, seen = [], set()
        for run in runs:
            if run["error"] != "quota_exhausted" or (run["job"], run["harness"]) in seen:
                continue
            seen.add((run["job"], run["harness"]))
            times = sum(
                1
                for r in runs
                if (r["job"], r["harness"], r["error"])
                == (run["job"], run["harness"], "quota_exhausted")
            )
            helper = next(
                (
                    r["harness"]
                    for r in runs
                    if r["job"] == run["job"]
                    and r["harness"] != run["harness"]
                    and r["status"] == "succeeded"
                ),
                None,
            )
            job = job_name(run["job"])
            name = HARNESSES.get(run["harness"], run["harness"])
            then = (
                f"自动换成了 {HARNESSES.get(helper, helper)}"
                if helper
                else "没有其他 Agent 接手完成"
            )
            lines.append(f"{job}时 {name} 额度用完了 {times} 次，{then}。")
        return lines
