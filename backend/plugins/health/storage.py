"""Health storage.

Normalized observations live in a separate SQLite file (<data_dir>/health/observations.sqlite3).
They can be rebuilt from the uploads in health_samples plus the Apple export, and years of
heart-rate readings would otherwise bloat the main database and each daily backup. Derived
rows (health_daily, sleep_sessions, health_workouts) live in the main, backed-up database.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime

from plugins.health.observations import Observation
from plugins.health.sleep import decode_mask, encode_mask

OBSERVATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, device_class TEXT NOT NULL, origin TEXT NOT NULL,
  UNIQUE(name, device_class, origin));
CREATE TABLE IF NOT EXISTS observations (
  kind TEXT NOT NULL, t_start INTEGER NOT NULL, t_end INTEGER NOT NULL, value REAL NOT NULL,
  label TEXT NOT NULL, source_id INTEGER NOT NULL REFERENCES sources(id),
  PRIMARY KEY (kind, t_start, source_id, t_end, value, label)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kind_spans (kind TEXT PRIMARY KEY, longest INTEGER NOT NULL);
"""

DERIVED_SCHEMA = """
CREATE TABLE IF NOT EXISTS health_daily (
  date TEXT PRIMARY KEY, status TEXT NOT NULL, issues TEXT NOT NULL,
  wear_hours INTEGER NOT NULL, metrics TEXT NOT NULL, extras TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sleep_sessions (
  sleep_date TEXT PRIMARY KEY, onset_at TEXT NOT NULL, wake_at TEXT NOT NULL,
  onset_minutes REAL NOT NULL, wake_minutes REAL NOT NULL, asleep_minutes REAL NOT NULL,
  deep_minutes REAL NOT NULL, core_minutes REAL NOT NULL, rem_minutes REAL NOT NULL,
  unspecified_minutes REAL NOT NULL, awake_minutes REAL NOT NULL, segments INTEGER NOT NULL,
  naps INTEGER NOT NULL, nap_minutes REAL NOT NULL, device_class TEXT NOT NULL,
  has_stages INTEGER NOT NULL, quality TEXT NOT NULL, sleep_mask TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS health_workouts (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL,
  activity TEXT NOT NULL, duration_minutes REAL NOT NULL, device_class TEXT NOT NULL,
  hr_method TEXT NOT NULL, hr_coverage REAL, avg_hr REAL, hr_histogram TEXT NOT NULL,
  trimp REAL, hr_rest REAL, hr_max REAL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS health_workouts_date ON health_workouts(date);
"""


def iso(moment):
    return datetime.fromtimestamp(moment, UTC).isoformat()


class ObservationStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(OBSERVATION_SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add(self, rows, origin, chunk=20_000):
        """Insert (kind, start, end, value, label, source, device_class) rows idempotently.

        Commits per chunk so a huge import never holds one long write transaction. Returns
        (inserted, UTC day numbers touched by any row, duplicates included).
        """
        inserted, days, sources, batch, longest = 0, set(), {}, [], {}
        with self.connect() as conn:
            for source_id, name, device in conn.execute(
                "SELECT id,name,device_class FROM sources WHERE origin=?", (origin,)
            ):
                sources[name, device] = source_id

            def flush():
                nonlocal inserted
                before = conn.total_changes
                conn.executemany("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)", batch)
                inserted += conn.total_changes - before
                conn.executemany(
                    "INSERT INTO kind_spans VALUES(?,?) ON CONFLICT(kind) "
                    "DO UPDATE SET longest=max(longest,excluded.longest)",
                    longest.items(),
                )
                conn.commit()
                batch.clear()
                longest.clear()

            for kind, start, end, value, label, name, device in rows:
                key = (name, device)
                if key not in sources:
                    sources[key] = conn.execute(
                        "INSERT INTO sources(name,device_class,origin) VALUES(?,?,?)",
                        (name, device, origin),
                    ).lastrowid
                batch.append((kind, start, end, value, label, sources[key]))
                if end - start > longest.get(kind, -1):
                    longest[kind] = end - start
                days.update(range(start // 86400, end // 86400 + 1))
                if len(batch) >= chunk:
                    flush()
            if batch:
                flush()
        return inserted, days

    def fetch(self, windows):
        """Observations overlapping each window {kind: (start, end)} (points: start inside)."""
        result = []
        with self.connect() as conn:
            # The App uploads a sample's source name only, and the user can name the watch
            # anything (「红线所系之处」), so its uploads come in as "other". A name the export
            # (or an upload that named its device) showed to be a watch or a phone keeps that
            # class; a watch wins over a phone.
            known = dict(
                conn.execute(
                    "SELECT name,device_class FROM sources WHERE device_class!='other' "
                    "ORDER BY device_class='watch'"
                )
            )
            spans = dict(conn.execute("SELECT kind,longest FROM kind_spans"))
            for kind, (start, end) in windows.items():
                if kind not in spans:
                    continue
                result.extend(
                    Observation(*row[:6], known[row[5]], row[7])
                    if row[6] == "other" and row[5] in known
                    else Observation(*row)
                    for row in conn.execute(
                        "SELECT o.kind,o.t_start,o.t_end,o.value,o.label,s.name,s.device_class,"
                        "s.origin FROM observations o JOIN sources s ON s.id=o.source_id "
                        "WHERE o.kind=? AND o.t_start>=? AND o.t_start<? "
                        "AND (o.t_end>? OR o.t_start>=?)",
                        (kind, start - spans[kind], end, start, start),
                    )
                )
        return result

    def bounds(self):
        """{kind: (first start, last end)} over all observations."""
        result = {}
        with self.connect() as conn:
            for kind, longest in conn.execute("SELECT kind,longest FROM kind_spans").fetchall():
                low, high = conn.execute(
                    "SELECT min(t_start),max(t_start) FROM observations WHERE kind=?", (kind,)
                ).fetchone()
                if low is not None:
                    result[kind] = (low, high + longest)
        return result


def ensure_derived_schema(db):
    with db.connect() as conn:
        conn.executescript(DERIVED_SCHEMA)


def write_day(conn, result, timestamp):
    day = result.day.isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO health_daily VALUES(?,?,?,?,?,?,?)",
        (
            day,
            result.status,
            json.dumps(result.issues),
            result.wear_hours,
            json.dumps(result.metrics, ensure_ascii=False),
            json.dumps(result.extras, ensure_ascii=False),
            timestamp,
        ),
    )
    night = result.night
    if night is None:
        conn.execute("DELETE FROM sleep_sessions WHERE sleep_date=?", (day,))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO sleep_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                day,
                iso(night.onset),
                iso(night.wake),
                night.onset_minutes,
                night.wake_minutes,
                night.asleep_minutes,
                night.deep_minutes,
                night.core_minutes,
                night.rem_minutes,
                night.unspecified_minutes,
                night.awake_minutes,
                night.segments,
                night.naps,
                night.nap_minutes,
                night.device_class,
                int(night.has_stages),
                night.quality,
                encode_mask(night.mask),
                timestamp,
            ),
        )
    conn.execute("DELETE FROM health_workouts WHERE date=?", (day,))
    for workout in result.workouts:
        conn.execute(
            "INSERT OR REPLACE INTO health_workouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                workout.id,
                day,
                iso(workout.start),
                iso(workout.end),
                workout.activity,
                workout.duration_minutes,
                workout.device_class,
                workout.hr_method,
                workout.hr_coverage,
                workout.avg_hr,
                json.dumps({str(k): v for k, v in sorted(workout.histogram.items())}),
                None,
                None,
                None,
                timestamp,
            ),
        )


def read_days(db, start=None, end=None):
    """{date: row} for health_daily between start and end (inclusive)."""
    clause, args = _range("date", start, end)
    rows = {}
    for row in db.rows("SELECT * FROM health_daily" + clause + " ORDER BY date", args):
        rows[date.fromisoformat(row["date"])] = {
            "status": row["status"],
            "issues": json.loads(row["issues"]),
            "wear_hours": row["wear_hours"],
            "metrics": json.loads(row["metrics"]),
            "extras": json.loads(row["extras"]),
        }
    return rows


def read_nights(db, start=None, end=None):
    clause, args = _range("sleep_date", start, end)
    nights = {}
    for row in db.rows("SELECT * FROM sleep_sessions" + clause + " ORDER BY sleep_date", args):
        row["has_stages"] = bool(row["has_stages"])
        row["mask"] = decode_mask(row.pop("sleep_mask"))
        row["total_minutes"] = row["asleep_minutes"] + row["nap_minutes"]  # as Night has it
        nights[date.fromisoformat(row["sleep_date"])] = row
    return nights


def read_workouts(db, start=None, end=None):
    clause, args = _range("date", start, end)
    rows = db.rows("SELECT * FROM health_workouts" + clause + " ORDER BY start_at", args)
    for row in rows:
        row["hr_histogram"] = {int(k): v for k, v in json.loads(row["hr_histogram"]).items()}
    return rows


def _range(column, start, end):
    clauses, args = [], []
    if start is not None:
        clauses.append(f"{column}>=?")
        args.append(start.isoformat())
    if end is not None:
        clauses.append(f"{column}<=?")
        args.append(end.isoformat())
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), args
