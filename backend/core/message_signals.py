"""Local roster rules. Never return the configured identities or the matching identity."""

import re
import unicodedata

LIST_WORDS = (
    "未交",
    "未完成",
    "未提交",
    "没交",
    "未填",
    "未报名",
    "未签到",
    "以下同学",
    "名单",
)
CHAIN_WORDS = ("接龙", "报名", "收集表")
URGENT_FLAGS = {"self_listed", "self_missing"}
NUMBERED = re.compile(r"(?<!\d)\d{1,3}\s*[.、)）．]\s*([^\n]+?)(?=\s+\d{1,3}\s*[.、)）．]|\n|$)")
ELLIPSES = ("…", "...", "⋯", "..")


def identity_pattern(identity):
    identity = unicodedata.normalize("NFKC", identity).strip()
    if not identity:
        return None
    if identity.isdecimal():
        return re.compile(r"(?<!\d)" + re.escape(identity) + r"(?!\d)")
    # OCR may insert blanks between Chinese characters. Do not remove spaces between
    # digits: separate numbers must not accidentally become a student's full ID.
    return re.compile(r"\s*".join(re.escape(c) for c in identity if not c.isspace()), re.I)


def has_self(text, identities):
    text = unicodedata.normalize("NFKC", text)
    return any(pattern.search(text) for name in identities if (pattern := identity_pattern(name)))


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
        result.extend((text[end:start], "[本人]"))
        end = stop
    result.append(text[end:])
    return "".join(result)


def message_flags(text, identities):
    if not any(name.strip() for name in identities):
        return []  # no identity configured: absence cannot be determined
    normalized = unicodedata.normalize("NFKC", text)
    compact = re.sub(r"\s+", "", normalized)
    flags = []
    if any(word in compact for word in LIST_WORDS) and has_self(normalized, identities):
        flags.append("self_listed")
    # Membership is checked only in numbered entries, never in the sender/header.
    entries = NUMBERED.findall(normalized)
    if "接龙" in compact and entries:
        flags.append("self_joined" if has_self("\n".join(entries), identities) else "self_missing")
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
