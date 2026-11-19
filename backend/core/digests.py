"""Digests in sections: a bold name line such as 「**新待办**」, then one 「- 」 item per line.

prompts/extract_todos.md asks for this form. A day's batches merge section by section, so the
App shows one short list per day instead of a paragraph per batch. Text in any other form (a
report, a one-line usage note) is appended as before.

What the App and the watch show of a day is `brief()` of that: items whose dates have all gone
by are left out, and 「新待办」 keeps the two most pressing first, the rest counted in one line.
The stored digest stays whole.
"""

import re
from datetime import datetime, time, timedelta

# What a batch writes when it found nothing worth telling; dropped once the day has more.
NOTHING_NEW = "没有新的重要消息。"
HEADING = re.compile(r"\*\*(.+?)\*\*")
# prompts/extract_todos.md's sections, in reading order whichever batch wrote one first.
ORDER = ("新待办", "通知", "还不确定")
TODOS = "新待办"
# 「新待办」 items shown; the rest are on the todo list and in Reminders.
FIRST = 2
# 「11月3日」, 「11月3日 23:59」, 「11月6日19点」, and 「11/10」 as the prompt's change lines write it.
DATE = re.compile(
    r"(?<!\d)(\d{1,2})月(\d{1,2})[日号](?:\s*(\d{1,2})(?:[:：](\d{2})|点))?"
    r"|(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])"
)


def sections(text):
    """[(heading line, [item lines])], or None when a line is neither a heading nor an item."""
    result = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if HEADING.fullmatch(line):
            result.append((line, []))
        elif line.startswith("- ") and result:
            result[-1][1].append(line)
        else:
            return None
    return result or None


def render(parts):
    return "\n\n".join("\n".join([heading, *items]) for heading, items in parts)


def merge(old, new):
    """`new` added to `old`: items under the same section name join it, new names follow."""
    if new.strip() == NOTHING_NEW or ("\n" + new.strip() + "\n") in ("\n" + old.strip() + "\n"):
        return old
    if old.strip() == NOTHING_NEW:
        return new
    before, after = sections(old), sections(new)
    if before is None or after is None:
        return old + "\n" + new
    merged = [(heading, list(items)) for heading, items in before]
    names = [HEADING.match(heading)[1] for heading, _ in merged]
    for heading, items in after:
        name = HEADING.match(heading)[1]
        if name not in names:
            merged.append((heading, []))
            names.append(name)
        kept = merged[names.index(name)][1]
        kept.extend(item for item in items if item not in kept)
    merged.sort(
        key=lambda part: (
            ORDER.index(name) if (name := HEADING.match(part[0])[1]) in ORDER else len(ORDER)
        )
    )
    return render(merged)


def combine(texts):
    """Several digests (a day's message sources) as one text, section by section."""
    result = ""
    for text in texts:
        result = merge(result, text) if result else text
    return result


def latest_date(item, now):
    """The latest moment `item` names (the end of a day named without a time), in `now`'s zone
    and in the year that puts it nearest to `now`; None when it names no date."""
    found = []
    for match in DATE.finditer(item):
        month, day = int(match[1] or match[5]), int(match[2] or match[6])
        hour, minute = (int(match[3]), int(match[4] or 0)) if match[3] else (None, 0)
        if hour is not None and not (0 <= hour <= 23 and 0 <= minute <= 59):
            hour = None
        moments = []
        for year in (now.year - 1, now.year, now.year + 1):
            try:
                day_start = datetime(year, month, day, tzinfo=now.tzinfo)
            except ValueError:
                continue
            if hour is None:
                moments.append(day_start + timedelta(days=1) - timedelta(seconds=1))
            else:
                moments.append(datetime.combine(day_start.date(), time(hour, minute), now.tzinfo))
        if moments:
            found.append(min(moments, key=lambda moment: abs(moment - now)))
    return max(found) if found else None


def brief(text, now):
    """`text` as the App shows it at `now`: past items left out, 「新待办」 soonest first and cut
    to FIRST with a count of the rest; text in another form unchanged."""
    parts = sections(text) if text.strip() else None
    if parts is None:
        return text
    kept = []
    for heading, items in parts:
        upcoming = [
            (when, index, item)
            for index, item in enumerate(items)
            if (when := latest_date(item, now)) is None or when >= now
        ]
        if HEADING.match(heading)[1] == TODOS:
            # Dated ones by date, undated ones after them in the order they came.
            upcoming.sort(key=lambda entry: (entry[0] is None, entry[0] or now, entry[1]))
            shown = [item for _, _, item in upcoming[:FIRST]]
            if len(upcoming) > FIRST:
                shown.append(f"- 还有 {len(upcoming) - FIRST} 件，在待办列表里")
        else:
            shown = [item for _, _, item in upcoming]
        if shown:
            kept.append((heading, shown))
    return render(kept) if kept else NOTHING_NEW
