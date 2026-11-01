"""Tokens from the session files Claude Code and Codex keep on disk: your own use of the CLIs.

Claude Code writes ~/.claude/projects/<project>/<session>.jsonl (subagents in subfolders),
with one line per content block of a reply, each repeating that reply's usage. Codex writes
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, whose token_count events carry the session's
running total. Neither format is a published contract, so lines that do not match are skipped.

Only the counts are kept, never message text. Claude Code counts every line, as the desktop App's
usage panel does, so the numbers match it: a reply split into thinking, text and tool-use lines
counts its usage once per line, and a resumed session's file repeats the earlier lines, which
count again; together several times the tokens actually billed. A Claude Code count is keyed by
its file's name (the session id), the line's uuid and which repeat of that uuid in the file it
is; a Codex count by session id and running total. So a file copied over from another computer,
or read twice, is counted once. The core's own Agent runs write no session files
(--no-session-persistence, --ephemeral), so nothing here overlaps run_usage.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from harness.base import count

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_usage (
  key TEXT PRIMARY KEY, harness TEXT NOT NULL, recorded_at TEXT NOT NULL,
  input_tokens INTEGER NOT NULL, cached_input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS local_usage_time ON local_usage(recorded_at);
"""
FILES_KEY = "files"
# How Claude Code lines are counted. Rows imported another way are dropped and read again.
COUNTING_KEY = "claude_counting"
COUNTING = "per_file_line"


def utc(text):
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC).isoformat()


def claude_rows(lines, name):
    repeats = {}
    for item in lines:
        message = item.get("message")
        if item.get("type") != "assistant" or not isinstance(message, dict):
            continue
        usage, at = message.get("usage"), utc(item.get("timestamp"))
        if not isinstance(usage, dict) or not message.get("id") or not at:
            continue
        cached = count(usage.get("cache_read_input_tokens"))
        line = item.get("uuid") or f"{message['id']}:{item.get('requestId') or ''}"
        repeats[line] = repeats.get(line, 0) + 1
        yield (
            f"claude:{name}:{line}:{repeats[line]}",
            "claude_code",
            at,
            count(usage.get("input_tokens"))
            + count(usage.get("cache_creation_input_tokens"))
            + cached,
            cached,
            count(usage.get("output_tokens")),
        )


def codex_rows(lines):
    session, last = None, None
    for item in lines:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        if item.get("type") == "session_meta":
            session, last = payload.get("id"), None
            continue
        info = payload.get("info")
        if payload.get("type") != "token_count" or not isinstance(info, dict) or not session:
            continue
        total, at = info.get("total_token_usage"), utc(item.get("timestamp"))
        if not isinstance(total, dict) or not at:
            continue
        now = tuple(
            count(total.get(k)) for k in ("input_tokens", "cached_input_tokens", "output_tokens")
        )
        # A running total that went down started over (a resumed or forked session).
        pairs = list(zip(now, last, strict=True)) if last else []
        delta = tuple(a - b for a, b in pairs) if all(a >= b for a, b in pairs) else now
        delta = delta or now
        last = now
        if any(delta):
            yield (f"codex:{session}:{sum(now)}:{now[0]}", "codex", at, *delta)


def read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as file:
        for line in file:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                yield item


def rows_in(path):
    """Claude Code or Codex, told apart by the first line that says which."""
    for item in read_lines(path):
        if item.get("type") == "session_meta":
            return list(codex_rows(read_lines(path)))
        if item.get("type") in {"assistant", "user"} or "sessionId" in item:
            return list(claude_rows(read_lines(path), path.name))
    return []


def import_sessions(db, directories, state):
    """Read new or changed .jsonl files below `directories`; returns the files' new state.

    `state` maps a file path to [size, mtime_ns] from the last import, so an hourly import
    only reads files that changed.
    """
    with db.connect() as conn:
        conn.executescript(SCHEMA)
    seen = {}
    for directory in directories:
        root = Path(os.path.expanduser(directory))
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            try:
                stat = path.stat()
            except OSError:
                continue
            key, mark = str(path), [stat.st_size, stat.st_mtime_ns]
            seen[key] = mark
            if state.get(key) == mark:
                continue
            try:
                rows = rows_in(path)
            except OSError:
                seen.pop(key)
                continue
            with db.connect() as conn:
                # Older Claude Code versions logged a reply's first block before its final
                # output count; keep the largest count seen for each key.
                conn.executemany(
                    "INSERT INTO local_usage VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "input_tokens=max(input_tokens,excluded.input_tokens),"
                    "cached_input_tokens=max(cached_input_tokens,excluded.cached_input_tokens),"
                    "output_tokens=max(output_tokens,excluded.output_tokens)",
                    rows,
                )
    return seen


def recount_claude(db, counting):
    """Drop Claude Code rows counted another way; True when every file must be read again."""
    if counting == COUNTING:
        return False
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM local_usage WHERE harness='claude_code'")
    return True


def totals(db, since=""):
    """Tokens per CLI recorded at or after the UTC time `since` (all time by default)."""
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        return {
            r["harness"]: r["n"]
            for r in conn.execute(
                "SELECT harness, sum(input_tokens+output_tokens) AS n FROM local_usage "
                "WHERE recorded_at>=? GROUP BY harness",
                (since,),
            )
        }


def usage_since(db, since):
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM local_usage WHERE recorded_at>=? ORDER BY recorded_at", (since,)
            )
        ]
