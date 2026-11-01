"""Metadata first: only export images taken during a known (or estimated) class."""

import asyncio
import os
import re
import shutil
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger

from core.documents import IMAGES, MAX_FILE
from core.knowledge_access import safe_path
from core.plugins import Plugin as BasePlugin
from plugins.class_photos.photos import HELPER, library
from plugins.school.common import zone_of


def course_name(title):
    return re.sub(r"\s*(?:（推测）|\(推测\))\s*$", "", title).strip()


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        CronTrigger.from_crontab(config.get("cron", "*/30 * * * *"))
        self.db, self.knowledge = context.store.db, context.memory.knowledge
        self.photos = library(config.get("helper", HELPER))
        self.now = lambda: datetime.now(zone_of(context))
        for topic in config.get("course_topics", {}).values():
            self.knowledge.validate_topic(topic)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS class_photos_files("
            "id TEXT PRIMARY KEY,created_at TEXT NOT NULL,day TEXT NOT NULL,course TEXT,topic TEXT,"
            "estimated_from TEXT,status TEXT NOT NULL,output_path TEXT)"
        )
        self.staging = context.data_dir / "class_photos_staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        # Remove only this plugin's abandoned temporary directories after a process crash.
        for folder in self.staging.glob("photo-*"):
            if folder.is_dir() and not folder.is_symlink():
                shutil.rmtree(folder)

    def jobs(self):
        return {"scan": {"cron": self.config.get("cron", "*/30 * * * *"), "callback": self.scan}}

    async def permission(self):
        try:
            value = (await asyncio.to_thread(self.photos.call, "status"))["permission"]
        except Exception:
            value = "unavailable"
        self.context.put("permission", value)
        return value

    async def status(self):
        return {
            "enabled": True,
            "permission": await self.permission(),
            "last_run_at": self.context.get("last_run_at"),
            "recent": self.db.rows(
                "SELECT day,course,topic,count(*) AS count FROM class_photos_files "
                "WHERE status='copied' GROUP BY day,course,topic ORDER BY day DESC,course LIMIT 30"
            ),
            "skipped_not_slides": self.db.rows(
                "SELECT count(*) AS n FROM class_photos_files WHERE status='not_slides'"
            )[0]["n"],
        }

    async def scan(self):
        self.context.put("last_run_at", self.now().isoformat())
        if await self.permission() not in {"authorized", "limited"}:
            return
        timetable = self.context.get_plugin("timetable") if self.context.get_plugin else None
        if timetable is None:
            raise ValueError("class_photos_timetable_unavailable")
        end = self.now()
        value = self.context.get("cursor")
        lookback = end - timedelta(days=self.config.get("lookback_days", 3))
        # iCloud can deliver a photo long after its creationDate. Revisit recent metadata;
        # the full asset ID table still ensures that previously handled photos aren't read.
        start = min(datetime.fromisoformat(value), lookback) if value else lookback
        failed = False
        # Work a day at a time so the public timetable tool's 14-day limit is respected,
        # even after a long outage. Commit the cursor only after that entire window succeeds.
        while start < end:
            stop = min(start + timedelta(days=1), end)
            data = await timetable.timetable_days(
                {"from": (start.date() - timedelta(days=1)).isoformat(), "days": 4}
            )
            items = await self.list_window(start, stop)
            for item in items:
                previous = self.db.rows(
                    "SELECT status FROM class_photos_files WHERE id=?", (item["id"],)
                )
                if previous and previous[0]["status"] != "waiting_timetable":
                    continue
                at = datetime.fromisoformat(item["created_at"]).astimezone(zone_of(self.context))
                match = self.match(at, data["days"])
                if match is None:
                    known = any(
                        day["date"] == at.date().isoformat() and day["known"]
                        for day in data["days"]
                    )
                    self.record(
                        item,
                        at,
                        None,
                        None,
                        None,
                        "outside_class" if known else "waiting_timetable",
                    )
                    continue
                course, estimated = match
                if course in self.config.get("skip_courses", []):
                    self.record(item, at, course, None, estimated, "skipped_course")
                    continue
                topic = self.config.get("course_topics", {}).get(course, course)
                try:
                    self.knowledge.validate_topic(topic)
                    await self.import_photo(item, at, course, topic, estimated)
                except Exception:
                    # Keep a retryable cursor, but one corrupt image or failed iCloud
                    # download must not prevent later photos from entering the library,
                    # nor fail every run forever: give up on it after a few attempts.
                    failures = self.context.get("failures", {})
                    failures[item["id"]] = failures.get(item["id"], 0) + 1
                    if failures[item["id"]] >= self.config.get("max_attempts", 3):
                        failures.pop(item["id"])
                        self.record(item, at, course, topic, estimated, "failed")
                    else:
                        failed = True
                    self.context.put("failures", failures)
            if not failed:
                self.context.put("cursor", stop.isoformat())
            start = stop
        await asyncio.to_thread(self.knowledge.sync)
        if failed:
            raise ValueError("class_photos_import_failed")

    async def list_window(self, start, end):
        items = await asyncio.to_thread(
            self.photos.call, "list", {"from": start.isoformat(), "to": end.isoformat()}
        )
        if len(items) < 2000:
            return items
        # Never silently skip the tail of a capped PhotoKit batch. Bisect the interval.
        if (end - start).total_seconds() <= 0.001:
            raise ValueError("class_photos_batch_full")
        middle = start + (end - start) / 2
        return await self.list_window(start, middle) + await self.list_window(middle, end)

    def match(self, at, days):
        matches = []
        for day in days:
            if not day["known"]:
                continue
            for entry in day["entries"]:
                if entry["kind"] != "课程":
                    continue
                start = datetime.combine(
                    datetime.fromisoformat(day["date"]).date(),
                    time.fromisoformat(entry["start"]),
                    at.tzinfo,
                )
                end = datetime.combine(start.date(), time.fromisoformat(entry["end"]), at.tzinfo)
                if end < start:
                    end += timedelta(days=1)
                if (
                    start - timedelta(minutes=self.config.get("before_minutes", 5))
                    <= at
                    <= end + timedelta(minutes=self.config.get("after_minutes", 10))
                ):
                    # For overlapping padding prefer the actual class, then the nearest start.
                    matches.append(
                        (
                            not start <= at <= end,
                            abs((at - start).total_seconds()),
                            course_name(entry["title"]),
                            day.get("estimated_from"),
                        )
                    )
        return min(matches, key=lambda m: m[:3])[2:] if matches else None

    def record(self, item, at, course, topic, estimated, status, output=None):
        self.db.execute(
            "INSERT INTO class_photos_files VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET course=excluded.course,topic=excluded.topic,"
            "estimated_from=excluded.estimated_from,status=excluded.status,"
            "output_path=excluded.output_path WHERE class_photos_files.status='waiting_timetable'",
            (
                item["id"],
                item["created_at"],
                at.date().isoformat(),
                course,
                topic,
                estimated,
                status,
                output,
            ),
        )

    async def import_photo(self, item, at, course, topic, estimated):
        with tempfile.TemporaryDirectory(prefix="photo-", dir=self.staging) as folder:
            base = Path(folder)
            result = await asyncio.to_thread(
                self.photos.call, "export", {"id": item["id"], "dest": str(base / "original")}
            )
            source = Path(result["path"])
            if (
                not source.resolve().is_relative_to(base)
                or source.is_symlink()
                or not source.is_file()
            ):
                raise ValueError("class_photos_invalid_export")
            if source.suffix.lower() not in IMAGES or source.stat().st_size > MAX_FILE:
                self.record(item, at, course, topic, estimated, "unreadable")
                return
            document = await asyncio.to_thread(self.context.memory.documents.read, source)
            if document["error"] and document["error"] != "没有识别出文字":
                # Extraction failures are retryable; they are not evidence of a non-slide.
                raise ValueError("class_photos_extraction_failed")
            if sum(not c.isspace() for c in document["content"]) < self.config.get(
                "min_text_chars", 15
            ):
                self.record(item, at, course, topic, estimated, "not_slides")
                return
            # PhotoKit localIdentifiers often contain '/L0/...'; only use a safe prefix.
            prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", item["id"][:8])
            relative = (
                f"知识库/{topic}/课堂照片/{at:%Y-%m-%d}/{at:%H%M}-{prefix}{source.suffix.lower()}"
            )
            target = safe_path(self.context.memory.root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.knowledge.access.discover()
            # Stage on the vault's volume as data_dir may be on another disk.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=".photo-", delete=False
                ) as output:
                    temporary = Path(output.name)
                    with source.open("rb") as stream:
                        shutil.copyfileobj(stream, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                # Persist intent first: a crash after linking cannot cause a second export.
                self.record(item, at, course, topic, estimated, "importing", relative)
                safe_path(self.context.memory.root, relative)
                os.link(temporary, target)
            except FileExistsError:
                status = "conflict"
            except OSError:
                self.db.execute(
                    "DELETE FROM class_photos_files WHERE id=? AND status='importing'",
                    (item["id"],),
                )
                raise
            else:
                status = "copied"
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            self.db.execute(
                "UPDATE class_photos_files SET status=? WHERE id=?", (status, item["id"])
            )
