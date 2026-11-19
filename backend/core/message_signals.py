"""Local roster rules. Never return the configured identities or the matching identity."""

import re
import unicodedata

PENDING_WORDS = (
    "未提交",
    "未完成",
    "未报名",
    "未签到",
    "未交",
    "未填",
    "没提交",
    "没完成",
    "没报名",
    "没签到",
    "没交",
    "没填",
    "没有提交",
    "没有完成",
    "没有报名",
    "没有签到",
    "没有交",
    "没有填",
    "缺交",
    "漏交",
    "还差",
    "还缺",
)
LIST_WORDS = (*PENDING_WORDS, "以下同学", "名单")
CHAIN_WORDS = ("接龙", "报名", "收集表")
URGENT_FLAGS = {"self_listed", "self_missing"}
SELF_MARK = "[本人]"
NUMBERED = re.compile(r"(?<!\d)\d{1,3}\s*[.、)）．]\s*([^\n]+?)(?=\s+\d{1,3}\s*[.、)）．]|\n|$)")
ELLIPSES = ("…", "...", "⋯", "..")
DONE_WORDS = ("已提交", "已完成", "已报名", "已签到", "已交", "已填")
HAN = "\u3400-\u9fff"
# One more character after a name makes a longer name (张三丰), unless it often follows a
# name (张三等五人). Two or more are running text (张三还没交).
AFTER_NAME = "等和及与也都的要没未已还你"


def spaced(words):
    return "|".join(r"\s*".join(word) for word in words)


PENDING = spaced(PENDING_WORDS)
# 已交名单：张三, 报名名单：张三, 已交的同学有：张三, 已交的有张三
FINISHED_ROSTER = re.compile(
    rf"(?:{spaced((*DONE_WORDS, '报名'))})\s*(?:"
    r"(?:的\s*)?(?:名\s*单|同\s*学|人\s*员|学\s*生)?(?:如\s*下|有)?\s*[:\n]"
    r"|的\s*(?:名\s*单|同\s*学|人\s*员|学\s*生)?\s*有)"
)
FINISHED_END = re.compile(rf"[。;!?]|{PENDING}")
# 李四\n张三还没交: the name in front of a pending word is no longer in the finished roster.
# A blank only separates when a whole name follows it; OCR also puts blanks inside names.
CLAUSE = re.compile(r"[\n,、;]|\s(?=\S{2,}\Z)")
# 张三：已交
FINISHED_LINE = re.compile(rf"^[^:\n]*:\s*(?:{spaced(DONE_WORDS)})\s*$", re.M)
# 张三：未交名单：李四 has the sender's name in front of the list. 张三：未交 is an entry.
SENDER = re.compile(rf"^[^:\n]{{1,12}}:(?=\s*(?:{PENDING})[^:\n]*:)", re.M)
# After the name: 张三你还差一份, but not the header in 张三 未交名单：李四.
PENDING_NEXT = re.compile(
    rf"(?:\s*(?:同\s*学|你|还|也|都|仍|尚))*\s*(?:{PENDING})(?![^,、;。\n]*(?:名\s*单|:))"
)
# After the name: 张三同学已经交过.
DONE_NEXT = re.compile(r"(?:同学)?(?:已|交了|交过|提交了|报名了|签到了|填了)")
# Before the name: 班主任：张三 signs the list and is not on it.
ROLE = re.compile(r"(?:班主任|辅导员|老师|联系人|负责人|收集人|发布人|通知人)\s*:\s*\Z")


def identity_pattern(identity):
    identity = unicodedata.normalize("NFKC", identity).strip()
    if not identity:
        return None
    if identity.isdecimal():
        return re.compile(r"(?<!\d)" + re.escape(identity) + r"(?!\d)")
    # OCR may insert blanks between Chinese characters. Do not remove spaces between
    # digits: separate numbers must not accidentally become a student's full ID.
    return re.compile(r"\s*".join(re.escape(c) for c in identity if not c.isspace()), re.I)


def mentions(text, identities):
    for name in identities:
        if pattern := identity_pattern(name):
            for match in pattern.finditer(text):
                yield match.group(), text[: match.start()], text[match.end() :]


def another_person(found, before, after):
    """张三丰, 张三丰同学, 张三老师 and Anna for Ann; 张三还没交 and 张三同学 are still the user."""
    if found[-1].isascii() and after[:1].isascii() and after[:1].isalnum():
        return True
    if found[0].isascii() and before[-1:].isascii() and before[-1:].isalnum():
        return True
    # Blanks inside the name came from OCR, which may have split the rest of it too.
    rest = re.sub(r"\s+", "", after) if re.search(r"\s", found) else after
    following = re.split(r"同学|学长|学姐", re.match(rf"[{HAN}]*", rest).group())[0]
    return rest.startswith("老师") or (len(following) == 1 and following not in AFTER_NAME)


def listed_in(text, identities):
    return any(
        not another_person(found, before, after)
        and not ROLE.search(before)
        and not DONE_NEXT.match(re.sub(r"\s+", "", after))
        for found, before, after in mentions(text, identities)
    )


def says_pending(text, identities):
    """张三还没交 needs no list, and holds even right after a finished roster."""
    return any(
        not another_person(found, before, after) and PENDING_NEXT.match(after)
        for found, before, after in mentions(text, identities)
    )


def entered_in(entries, identities):
    """A numbered entry starts with the name, so the characters around it can be judged too."""
    return any(
        not another_person(found, before, after)
        and not re.search(rf"[{HAN}]\Z|(?<!\w)[{HAN}][^\S\n]+\Z", before)
        and not re.match(rf"[^\S\n]+[{HAN}](?!\w)", after)
        for found, before, after in mentions(entries, identities)
    )


def without_finished(text):
    """Finding a name is enough to warn the user, so only remove what says otherwise."""
    text = FINISHED_LINE.sub("", SENDER.sub("", text))
    kept, end = [], 0
    for roster in FINISHED_ROSTER.finditer(text):
        if roster.start() < end or re.search(
            r"(?:[未没缺漏]|没\s*有)\s*\Z", text[: roster.start()]
        ):
            continue  # 未报名名单 contains 报名名单
        kept.append(text[end : roster.start()])
        stop = FINISHED_END.search(text, roster.end())
        end = stop.start() if stop else len(text)
        if stop and stop.group() not in "。;!?":
            clauses = list(CLAUSE.finditer(text, roster.end(), end))
            end = clauses[-1].end() if clauses else end
    return "".join(kept) + text[end:]


def redact_self(text, identities):
    if not identities:
        return text
    # Match a normalized shadow but replace spans in the original, so untouched OCR
    # punctuation stays verbatim for provenance checks.
    normalized, offsets = "", []
    for index, char in enumerate(text):
        piece = unicodedata.normalize("NFKC", char)
        normalized += piece
        offsets.extend([index] * len(piece))
    spans = []
    for name in sorted(identities, key=len, reverse=True):
        if pattern := identity_pattern(name):
            spans.extend(
                (offsets[m.start()], offsets[m.end() - 1] + 1) for m in pattern.finditer(normalized)
            )
    result, end = [], 0
    for start, stop in sorted(spans, key=lambda span: (span[0], -span[1])):
        if start < end:
            continue
        result.extend((text[end:start], SELF_MARK))
        end = stop
    result.append(text[end:])
    return "".join(result)


def redact_todo(todo, local_identities):
    """Redact a fresh todo view before returning it to an Agent or indexing daily text."""
    if todo is not None:
        identities = [
            name for plugin in todo["source_plugin"].split(",") for name in local_identities(plugin)
        ]
        for field in ("title", "notes", "source_excerpt", "course"):
            if isinstance(todo.get(field), str):
                todo[field] = redact_self(todo[field], identities)
        for source in todo.get("sources", []):
            for field in ("sender", "chat_name"):
                source[field] = redact_self(source[field], identities)
    return todo


def message_flags(text, identities):
    if not any(name.strip() for name in identities):
        return []  # no identity configured: absence cannot be determined
    normalized = unicodedata.normalize("NFKC", text)
    entries = NUMBERED.findall(normalized)
    chain = bool(entries) and "接龙" in re.sub(r"\s+", "", normalized)
    flags = []
    unfinished = without_finished(normalized)
    compact = re.sub(r"\s+", "", unfinished)
    # 接龙名单 is the chain's title: its entries are the people who did join.
    words = PENDING_WORDS if chain else LIST_WORDS
    if says_pending(normalized, identities) or (
        any(word in compact for word in words) and listed_in(unfinished, identities)
    ):
        flags.append("self_listed")
    # Membership is checked only in numbered entries, never in the sender/header.
    if chain:
        flags.append(
            "self_joined"
            if any(entered_in(entry, identities) for entry in entries)
            else "self_missing"
        )
    return flags


def has_roster_hint(text):
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    return any(word in compact for word in (*LIST_WORDS, *CHAIN_WORDS))


def open_reason(text, identities, everyone=False):
    if everyone:
        return "everyone"
    if not has_roster_hint(text):
        return None
    if text.rstrip().endswith(ELLIPSES):
        return "truncated"
    if message_flags(text, identities):
        return None
    # A complete-looking explicit roster can be checked locally; a notice mentioning a
    # list without showing its entries cannot. Empty self_names also means unknown.
    if identities and re.search(r"[:：]\s*\S+", text):
        return None
    return "membership_unknown"
