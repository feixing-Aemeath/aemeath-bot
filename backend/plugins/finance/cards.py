"""记账's cards: what is left of the month's allowance, advice by it, where the money went and
the months before. Every number comes from the ledger, and nothing here goes to a model."""

from datetime import date, timedelta

from plugins.finance.config import SOURCES, cycle, shift_month, yuan
from plugins.finance.reports import coverage_text

# Days of bills a month needs before its pace says anything; until then the advice looks back
# at the month before.
PACE_FROM = 5
# What to try first where most of the money went.
SAVING = {
    "吃饭": "多去食堂，少点外卖",
    "购物": "想买的先放购物车，过几天还想要再买",
    "会员订阅": "看看有没有不用的会员，可以退订",
    "娱乐": "找找免费的活动",
    "交通": "近的地方骑车或坐公交",
}


def whole(cents):
    """「1,161」: whole yuan on the page; the ledger keeps cents."""
    return f"{round(cents / 100):,}"


def month(summary):
    return f"{date.fromisoformat(summary['start']).month}月"


def span(summary):
    start, end = (date.fromisoformat(summary[k]) for k in ("start", "end"))
    return start, end, (end - start).days + 1


def known_days(summary, last, today):
    """The days of a period that every source's bills already tell about."""
    start, end, _ = span(summary)
    if last is None or last < start:
        return 0
    return (min(last, end, today) - start).days + 1


def left_at_end(summary, known):
    """What would be left at the end of the period at the pace of the known days, or None."""
    _, _, length = span(summary)
    if known < min(PACE_FROM, length):
        return None
    return summary["budget_cents"] - summary["spent_cents"] * length // known


def treat(cents):
    """Half of what is spare, in fifties: enough for something nice, the rest kept."""
    return cents // 2 // 5000 * 50


def takeout_line(summary, ending):
    if not summary["takeout_count"]:
        return None
    return f"外卖 {summary['takeout_count']} 次，共 {whole(summary['takeout_cents'])} 元，{ending}"


def biggest_line(summary):
    named = {k: v for k, v in summary["categories"].items() if k != "其他" and v > 0}
    if not named:
        return None
    category = max(named, key=named.get)
    return f"花得最多的是{category}（{whole(named[category])} 元）：" + SAVING.get(
        category, "下个月先从这里省"
    )


def budget(summary, known, last):
    spent, total, remaining = (
        summary[k] for k in ("spent_cents", "budget_cents", "remaining_cents")
    )
    first = f"已花 {whole(spent)} / {whole(total)} 元"
    if summary["days_remaining"]:
        first += f" · 还有 {summary['days_remaining']} 天"
        if remaining > 0:
            first += f"，每天约 {whole(summary['daily_cents'])} 元"
    lines = [first]
    start, _, _ = span(summary)
    if known:
        lines.append(f"账单截至 {last.month}/{last.day}，之后的还没导入")
    elif all(not c["through"] or c["through"] < str(start) for c in summary["coverage"].values()):
        lines.append(f"{month(summary)}的账单还没导入，导入后这里会更新")
    else:
        lines.append(coverage_text(summary["coverage"]))
    if summary["budget_from_settings"]:
        lines.append(f"生活费还没到账，先按设置的 {whole(total)} 元算")
    left = left_at_end(summary, known)
    tone = "alert" if remaining < 0 else "warn" if left is not None and left < 0 else None
    bar = {"max": total / 100, "segments": slices(summary)}
    if tone:
        bar["tone"] = tone
    return {
        "type": "stat",
        "title": f"{month(summary)}还能花",
        "data": {
            "first": True,
            "wide": True,
            "value": whole(remaining),
            "unit": "元",
            "label": "\n".join(lines),
            "bar": bar,
        },
    }


def slices(summary):
    """The categories with money in them, the largest first, as the ring and the bar show them."""
    spent = sorted(
        ((k, v) for k, v in summary["categories"].items() if v > 0), key=lambda kv: -kv[1]
    )
    return [{"x": k, "y": v / 100, "label": f"{whole(v)} 元"} for k, v in spent]


def advice(summary, previous, known):
    """A headline and a few lines by the money left, or by last month's early in a month."""
    total, remaining = summary["budget_cents"], summary["remaining_cents"]
    _, _, length = span(summary)
    left = left_at_end(summary, known)
    daily = max(summary["daily_cents"] or 0, 0)
    spare = max(total // 10, 20000)
    if remaining < 0:
        headline = f"{month(summary)}已经超了 {whole(-remaining)} 元"
        lines = [
            takeout_line(summary, "剩下几天先停一停，去食堂吃") or "剩下几天去食堂吃，能省就省",
            biggest_line(summary),
        ]
        symbol = "takeoutbag.and.cup.and.straw.fill"
    elif left is None:
        return looking_back(summary, previous)
    elif left < -total // 20:
        headline = f"照现在的速度，月底会超 {whole(-left)} 元"
        lines = [
            f"接下来每天控制在 {whole(daily)} 元以内",
            takeout_line(summary, "少点几次就能补回来") or biggest_line(summary),
        ]
        symbol = "takeoutbag.and.cup.and.straw.fill"
    elif left >= spare:
        headline = f"照这个节奏，月底能剩 {whole(left)} 元左右"
        amount = treat(min(left, remaining))
        lines = [
            f"可以拿 {amount} 元买点喜欢的东西，犒劳一下自己" if amount else None,
            "剩下的留着，下个月手头更宽裕",
        ]
        symbol = "gift.fill"
    else:
        headline = f"节奏刚好，月底大约剩 {whole(max(left, 0))} 元"
        lines = [
            f"每天约 {whole(daily)} 元，照这样花就行",
            takeout_line(summary, "想多攒点可以少点几次"),
        ]
        symbol = "checkmark.seal.fill"
    return tip(headline, lines, symbol)


def looking_back(summary, previous):
    total = summary["budget_cents"]
    _, _, length = span(summary)
    per_day = f"这个月每天约 {whole(total // length)} 元"
    if previous["spent_cents"] <= 0:
        return tip(
            "还没有账单",
            ["把微信、招商银行导出的账单放进知识库「财务」，记账会自己导入", per_day],
            "tray.and.arrow.down.fill",
        )
    before, left = month(previous), previous["remaining_cents"]
    if left < 0:
        headline = f"{before}超了 {whole(-left)} 元"
        lines = [takeout_line(previous, "这个月少点几次"), biggest_line(previous), per_day]
        symbol = "takeoutbag.and.cup.and.straw.fill"
    elif left >= max(previous["budget_cents"] // 10, 20000):
        headline = f"{before}剩了 {whole(left)} 元"
        lines = [f"可以拿 {treat(left)} 元买点喜欢的东西，犒劳一下自己", "剩下的留着当这个月的备用"]
        symbol = "gift.fill"
    else:
        headline = f"{before}刚好花完，剩 {whole(left)} 元"
        lines = [per_day, takeout_line(previous, "想多攒点可以少点几次")]
        symbol = "checkmark.seal.fill"
    return tip(headline, lines, symbol)


def tip(headline, lines, symbol):
    return {
        "type": "text",
        "title": "记账的建议",
        "data": {
            "headline": headline,
            "text": "\n".join(f"- {line}" for line in lines if line),
            "symbol": symbol,
        },
    }


def spending(summary):
    start, end, _ = span(summary)
    label = f"{start.month}/{start.day} — {end.month}/{end.day}"
    if summary["takeout_count"]:
        label += f" · 外卖 {summary['takeout_count']} 次 {whole(summary['takeout_cents'])} 元"
    return {
        "type": "chart",
        "title": f"{month(summary)}钱花在哪儿",
        "data": {
            "kind": "donut",
            "unit": "元",
            "value": whole(summary["spent_cents"]),
            "points": slices(summary),
            "label": label,
        },
    }


def months(ledger, today, first, budget_cents):
    """Each of the last six periods the bills fully cover, against the allowance."""
    if first is None:
        return None
    day = ledger.config.get("allowance_day", 1)
    current, _ = cycle(today, day)
    points = []
    for offset in range(5, -1, -1):
        start = shift_month(current, -offset, day)
        if start < first:
            continue
        end = shift_month(start, 1, day) - timedelta(days=1)
        spent = ledger.range_summary(start, end, today)["spent_cents"]
        points.append({"x": f"{start.month}月", "y": spent / 100, "label": whole(spent)})
    if len(points) < 2:
        return None
    return {
        "type": "chart",
        "title": "每月花销",
        "data": {
            "kind": "bar",
            "unit": "元",
            "points": points,
            "target": {"value": budget_cents / 100, "label": f"生活费 {whole(budget_cents)}"},
        },
    }


def cards(ledger, today):
    summary = ledger.summary("cycle", today)
    previous = ledger.summary("previous", today)
    year = ledger.summary("year", today)
    first, last = ledger.covered()
    known = known_days(summary, last, today)
    result = [budget(summary, known, last), advice(summary, previous, known)]
    # Early in a month with no bills yet, last month's ring.
    shown = summary if summary["spent_cents"] > 0 else previous
    if shown["spent_cents"] > 0:
        result.append(spending(shown))
    if history := months(ledger, today, first, summary["budget_cents"]):
        result.append(history)
    assets = ledger.assets()
    if assets["accounts"]:
        dates = {a["as_of"] for a in assets["accounts"]}
        parts = [
            f"{a['account']} {yuan(a['amount_cents'])}"
            + ("" if len(dates) == 1 else f"（{a['as_of'][5:]}）")
            for a in assets["accounts"]
        ]
        label = " · ".join(parts)
        label += f"；截至 {assets['as_of'][5:]}" if len(dates) == 1 else "；括号里是截至哪天"
        if (today - date.fromisoformat(assets["as_of"])).days > 31:
            label += "；有的余额超过一个月了，跟记账说一声现在的"
        result.append(
            {
                "type": "stat",
                "title": "现有资产",
                "data": {"value": yuan(assets["total_cents"]), "unit": "元", "label": label},
            }
        )
    extras = [
        f"{name} {whole(year[key])} 元"
        for name, key in (("红包", "red_packet_cents"), ("分红等", "dividend_cents"))
        if year[key]
    ]
    result.append(
        {
            "type": "stat",
            "title": "今年额外收入",
            "data": {
                "value": whole(year["extra_income_cents"]),
                "unit": "元",
                "label": " · ".join(extras or ["还没有红包和分红"]) + "；不算进生活费",
            },
        }
    )
    bigs = ledger.bigs(today.replace(month=1, day=1), today)
    if bigs:
        result.append(
            {
                "type": "list",
                "title": "今年的大件",
                "data": {
                    "items": [
                        {
                            "title": f"{ledger.day(r)} "
                            + (
                                r["note"]
                                if r["note_override"] and r["note"]
                                else r["counterpart"] or r["item"] or "手动记的一笔"
                            ),
                            "subtitle": f"{yuan(cost)} 元 · 自己的钱，不占生活费",
                        }
                        for r, cost in bigs
                    ]
                },
            }
        )
    items = [
        {"title": SOURCES[s], "subtitle": coverage_text({s: c})}
        for s, c in summary["coverage"].items()
    ]
    items += [
        {"title": r["path"].rsplit("/", 1)[-1], "subtitle": r["error"]}
        for r in ledger.db.rows(
            "SELECT * FROM finance_files WHERE status NOT IN ('archived') ORDER BY updated_at DESC LIMIT 50"
        )
    ]
    if summary["ask_cents"]:
        items.append(
            {
                "title": "在家时的花销",
                "subtitle": f"{yuan(summary['ask_cents'])} 元，算家里的，不占生活费；家里没报的跟记账说",
            }
        )
    if family := summary["pending_reimbursement_cents"] + summary["reimbursed_cents"]:
        items.append({"title": "家里报销", "subtitle": f"{yuan(family)} 元，不占生活费"})
    if summary["family_income_cents"]:
        items.append(
            {
                "title": "家里转来",
                "subtitle": f"{yuan(summary['family_income_cents'])} 元（生活费和报销以外）",
            }
        )
    if summary["unknown_income_cents"]:
        items.append(
            {
                "title": "未分类转入",
                "subtitle": f"{yuan(summary['unknown_income_cents'])} 元；可在对话里确认收入类型",
            }
        )
    result.append({"type": "list", "title": "账单", "data": {"items": items}})
    return result
