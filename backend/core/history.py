"""Time cursors for the App's chat, digest and report history."""

import base64
import binascii
import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from core.db import dump


def boundary(value, timed):
    if not timed:
        return date.fromisoformat(value).isoformat()
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("timezone_required")
    return moment.astimezone(UTC).isoformat()


def encode(scope, position):
    return "h1." + base64.urlsafe_b64encode(dump([scope, position]).encode()).decode().rstrip("=")


def decode(value, scope, timed):
    if not value.startswith("h1."):
        return boundary(value, timed), None
    try:
        payload = base64.b64decode(
            value[3:] + "=" * (-len(value[3:]) % 4), altchars=b"-_", validate=True
        )
        saved_scope, position = json.loads(payload)
        moment, key = position
        if saved_scope != scope or not isinstance(moment, str):
            raise ValueError("invalid_cursor")
        if timed:
            if type(key) is not int or not 0 < key <= 2**63 - 1:
                raise ValueError("invalid_cursor")
        elif not isinstance(key, str) or not key:
            raise ValueError("invalid_cursor")
        boundary(moment, timed)
        return moment, key
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError("invalid_cursor") from error


def page(db, table, columns, filters, order, scope, limit=50, offset=0, before=None, zone=None):
    """Read at most limit+1 rows; count the filtered set in the same SQLite snapshot.

    Table/column names come only from core callers. `order` is a descending date/time,
    followed by the existing tie order: rowid descending for chat, kind/plugin ascending.
    """
    if not 1 <= limit <= 200 or offset < 0 or (before is not None and offset):
        raise ValueError("invalid_page")
    clauses, args = [], []
    for column, value in filters.items():
        if value is not None:
            clauses.append(f"{column}=?")
            args.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    count_where, count_args = where, list(args)
    moment_column, key_column = order
    timed = zone is not None
    if before is not None:
        if not before or len(before) > 2048:
            raise ValueError("invalid_cursor")
        moment, key = decode(before, scope, timed)
        if key is None:
            clauses.append(f"{moment_column}<?")
            args.append(moment)
        elif timed:
            clauses.append(f"({moment_column},{key_column})<(?,?)")
            args.extend((moment, key))
        else:
            # The leading range lets SQLite seek straight to this date in its index.
            clauses.append(f"{moment_column}<=? AND ({moment_column}<? OR {key_column}>?)")
            args.extend((moment, moment, key))
        where = " WHERE " + " AND ".join(clauses)
    direction = "DESC" if timed else "ASC"
    with db.connect() as conn:
        conn.execute("BEGIN")
        total = conn.execute("SELECT count(*) FROM " + table + count_where, count_args).fetchone()[
            0
        ]
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT {columns},{key_column} AS _key FROM {table}{where} "
                f"ORDER BY {moment_column} DESC,{key_column} {direction} LIMIT ? OFFSET ?",
                [*args, limit + 1, offset],
            )
        ]
    items, more = rows[:limit], rows[limit:]
    next_before, earlier_date = None, None
    if more:
        last = items[-1]
        next_before = encode(scope, [last[moment_column], last["_key"]])
        earlier_date = more[0][moment_column]
        if timed:
            earlier_date = (
                datetime.fromisoformat(earlier_date).astimezone(ZoneInfo(zone)).date().isoformat()
            )
    for item in items:
        del item["_key"]
    return {
        "items": items,
        "total": total,
        "has_more": bool(more),
        "next_before": next_before,
        "earlier_date": earlier_date,
    }
