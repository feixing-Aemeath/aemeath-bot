"""Glue between storage and the pure algorithms; used by the plugin, the CLI and tests."""

import json
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from plugins.health.daily import NIGHT_KINDS, compute_day, fetch_windows
from plugins.health.export_xml import ExportReader
from plugins.health.observations import UPLOAD_NORMALIZER, normalize_upload
from plugins.health.sleep import sleep_day_of
from plugins.health.stats import median
from plugins.health.storage import (
    ObservationStore,
    ensure_derived_schema,
    read_days,
    read_nights,
    read_workouts,
    write_day,
)
from plugins.health.summary import Analyzer, baselines_payload, daily_payload, sleep_payload
from plugins.health.training import trimp

KV_PREFIX = "plugin:health:"  # same keys as PluginContext.get/put for the "health" plugin
TRIMP_LOOKAHEAD_DAYS = 30  # a day's resting HR feeds the TRIMP of the following 30 days
HR_MAX_PLAUSIBLE = 230
DERIVED_DATE_COLUMNS = (
    ("health_daily", "date"),
    ("sleep_sessions", "sleep_date"),
    ("health_workouts", "date"),
)


class HealthEngine:
    def __init__(self, db, data_dir, config):
        self.db, self.config = db, config
        self.zone = ZoneInfo(config.timezone)
        self.observations = ObservationStore(Path(data_dir) / "health" / "observations.sqlite3")
        ensure_derived_schema(db)
        self.lock = threading.Lock()

    def kv(self, key, default=None):
        return self.db.get(KV_PREFIX + key, default)

    def put_kv(self, key, value):
        self.db.put(KV_PREFIX + key, value)

    def today(self):
        return datetime.now(self.zone).date()

    # Ingestion -----------------------------------------------------------------------------

    def ingest_uploads(self, batch=5000):
        """Normalize samples from POST /api/v1/health/samples that were not seen yet."""
        if self.kv("upload_normalizer") != UPLOAD_NORMALIZER:
            # Uploads ignored by an older normalizer (e.g. RMSSD before version 2) are read
            # again; observations already stored are skipped, so this only fills the gaps.
            self.put_kv("upload_cursor", 0)
            self.put_kv("upload_normalizer", UPLOAD_NORMALIZER)
        cursor = self.kv("upload_cursor", 0)
        used, ignored, days = 0, 0, set()
        while True:
            rows = self.db.rows(
                "SELECT rowid AS position,data FROM health_samples WHERE rowid>? "
                "ORDER BY rowid LIMIT ?",
                (cursor, batch),
            )
            if not rows:
                break
            normalized = []
            for row in rows:
                try:
                    item = normalize_upload(json.loads(row["data"]))
                except (KeyError, TypeError, ValueError):
                    item = None
                if item is None:
                    ignored += 1
                else:
                    normalized.append(item)
            _, touched = self.observations.add(normalized, "upload")
            used += len(normalized)
            days |= touched
            cursor = rows[-1]["position"]
            self.put_kv("upload_cursor", cursor)  # only after the batch is stored
        return {"used": used, "ignored": ignored, "days": days}

    def import_export(self, source, progress=None):
        """Stream an export into the observation store; `progress` may wrap the row iterator."""
        reader = ExportReader(source)
        rows = reader.rows()
        inserted, days = self.observations.add(progress(rows) if progress else rows, "export")
        return {
            "export_date": reader.export_date,
            "inserted": inserted,
            "counts": dict(reader.counts),
            "days": days,
        }

    # Derivation ----------------------------------------------------------------------------

    def data_dates(self):
        """First and last day with data; night-time kinds count for the sleep day they end."""
        bounds = self.observations.bounds()
        if not bounds:
            return None
        cutoff = self.config.sleep_day_cutoff_hour

        def day_of(kind, moment):
            if kind in NIGHT_KINDS:
                return sleep_day_of(moment, self.zone, cutoff)
            return datetime.fromtimestamp(moment, self.zone).date()

        first = min(day_of(kind, low) for kind, (low, _) in bounds.items())
        last = max(day_of(kind, max(low, high - 1)) for kind, (low, high) in bounds.items())
        return first, last

    def affected_dates(self, utc_days):
        """Local calendar and sleep days that a set of UTC day numbers can influence."""
        span = self.data_dates()
        if span is None or not utc_days:
            return set()
        dates = set()
        for number in utc_days:
            middle = date(1970, 1, 1) + timedelta(days=number)
            dates.update(middle + timedelta(days=offset) for offset in range(-1, 3))
        return {d for d in dates if span[0] <= d <= span[1]}

    def recompute(self, dates=None):
        """Rebuild stored rows for `dates` (all data days when None), then TRIMP."""
        span = self.data_dates()
        if span is None:
            return 0
        full = dates is None
        if full:
            count = (span[1] - span[0]).days + 1
            dates = [span[0] + timedelta(days=offset) for offset in range(count)]
        dates = sorted(dates)
        if not dates:
            return 0
        timestamp = datetime.now(UTC).isoformat()
        if full:
            # Another time zone or cutoff can move the first/last day; drop rows outside.
            first, last = span[0].isoformat(), span[1].isoformat()
            with self.db.connect() as conn:
                for table, column in DERIVED_DATE_COLUMNS:
                    conn.execute(
                        f"DELETE FROM {table} WHERE {column}<? OR {column}>?", (first, last)
                    )
        for day in dates:
            result = compute_day(
                day, self.observations.fetch(fetch_windows(day, self.config)), self.config
            )
            with self.db.connect() as conn:
                write_day(conn, result, timestamp)
        self.apply_trimp(dates)
        if full:
            self.put_kv("derived_fingerprint", self.config.derived_fingerprint())
        return len(dates)

    @property
    def stale(self):
        """Stored rows were derived with other settings (or never); a full rebuild is due."""
        return self.kv("derived_fingerprint") != self.config.derived_fingerprint()

    def refresh(self, full=False, incremental_only=False, wait=True):
        """Ingest new uploads and rebuild the days they touch; everything when settings changed.

        MCP tools pass incremental_only and wait=False: a full rebuild of years of history is
        left to the scheduled job instead of running inside a tool call's time limit.
        """
        if not self.lock.acquire(blocking=wait):
            return None
        try:
            ingest = self.ingest_uploads()
            if full or (self.stale and not incremental_only):
                days = self.recompute()
            else:
                days = self.recompute(self.affected_dates(ingest["days"]))
            return {
                "uploads_used": ingest["used"],
                "uploads_ignored": ingest["ignored"],
                "days": days,
            }
        finally:
            self.lock.release()

    def observed_hr_max(self):
        peaks = [
            row["extras"].get("hr_max")
            for row in read_days(self.db).values()
            if row["extras"].get("hr_max")
        ]
        peaks = [p for p in peaks if p <= HR_MAX_PLAUSIBLE]
        return max(peaks) if peaks else None

    def hr_max(self):
        if self.config.max_hr:
            return {"value": self.config.max_hr, "source": "config"}
        observed = self.observed_hr_max()
        return {"value": observed, "source": "observed" if observed else "unavailable"}

    def apply_trimp(self, dates):
        """TRIMP per workout and per day. HRmax is global, so a new maximum redoes all days."""
        hr_max = self.hr_max()["value"]
        if hr_max != self.kv("trimp_hr_max") or not dates:
            start = end = None
        else:
            start, end = min(dates), max(dates) + timedelta(days=TRIMP_LOOKAHEAD_DAYS)
        rows = read_days(self.db)
        resting = {
            d: row["metrics"]["resting_hr"]["value"]
            for d, row in rows.items()
            if row["metrics"].get("resting_hr", {}).get("quality") == "ok"
        }
        overall = median(list(resting.values())) if resting else None
        workouts = read_workouts(self.db, start, end)
        per_day = {}
        timestamp = datetime.now(UTC).isoformat()
        with self.db.connect() as conn:
            for workout in workouts:
                day = date.fromisoformat(workout["date"])
                rest = self.config.resting_hr
                if rest is None:
                    recent = [
                        resting[d]
                        for d in (day - timedelta(days=offset) for offset in range(30))
                        if d in resting
                    ]
                    rest = median(recent) if len(recent) >= 3 else overall
                value = None
                if workout["hr_histogram"] and rest and hr_max and hr_max > rest + 20:
                    value = trimp(
                        workout["hr_histogram"], rest, hr_max, self.config.trimp_weighting
                    )
                conn.execute(
                    "UPDATE health_workouts SET trimp=?,hr_rest=?,hr_max=?,updated_at=? WHERE id=?",
                    (value, rest, hr_max, timestamp, workout["id"]),
                )
                per_day.setdefault(day, []).append(value)
            for day, row in rows.items():
                if (start and day < start) or (end and day > end):
                    continue
                entry = row["metrics"].get("trimp", {"value": None, "quality": "missing"})
                values = per_day.get(day, [])
                if entry["quality"] == "missing" and not values:
                    entry["value"] = None
                else:
                    entry["value"] = sum(v for v in values if v is not None)
                    if any(v is None for v in values) and entry["quality"] == "ok":
                        entry["quality"] = "partial"  # a workout without heart rate
                row["metrics"]["trimp"] = entry
                conn.execute(
                    "UPDATE health_daily SET metrics=? WHERE date=?",
                    (json.dumps(row["metrics"], ensure_ascii=False), day.isoformat()),
                )
        self.put_kv("trimp_hr_max", hr_max)

    # Read side -----------------------------------------------------------------------------

    def analyzer(self, day, history_days, today=None):
        start = day - timedelta(days=history_days)
        return Analyzer(
            read_days(self.db, start, day),
            read_nights(self.db, start, day),
            self.config,
            today or self.today(),
        )

    def _envelope(self, payload):
        payload["recompute_pending"] = self.stale
        return payload

    def daily(self, day=None, days=2, today=None):
        day = day or today or self.today()
        # Alert streaks look back up to 60 days, each against a 30-day baseline.
        analyzer = self.analyzer(day, days + 95, today)
        return self._envelope(daily_payload(analyzer, day, days, self.hr_max()))

    def sleep(self, day=None, days=7, today=None):
        day = day or today or self.today()
        return self._envelope(sleep_payload(self.analyzer(day, days + 35, today), day, days))

    def baselines(self, day=None, today=None):
        day = day or today or self.today()
        return self._envelope(baselines_payload(self.analyzer(day, 31, today), day))
