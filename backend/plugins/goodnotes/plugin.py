"""One-way import of settled WebDAV PDFs; the user's vault is never a mirror."""

import asyncio
import os
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger

from core.documents import sha256
from core.knowledge_access import safe_path
from core.platforms import is_link
from core.plugins import Plugin as BasePlugin
from core.webdav import dav_path

UNMAPPED = "在 GoodNotes 里放进和主题同名的文件夹，或者在设置里指定"
CONFLICT = "冲突：记忆目录里的文件被改过"
WRONG_FORMAT = "请在 GoodNotes 里把备份格式选为 PDF"


def signature(stat):
    return f"{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        CronTrigger.from_crontab(config.get("cron", "*/15 * * * *"))
        self.library = context.memory.knowledge
        self.db = context.store.db
        self.root = dav_path(context.data_dir / "dav", "goodnotes")
        self.root.mkdir(parents=True, exist_ok=True)
        for prefix, topic in config.get("mapping", {}).items():
            dav_path(self.root, prefix)
            self.library.validate_topic(topic)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS goodnotes_files("
            "path TEXT PRIMARY KEY,topic TEXT,status TEXT NOT NULL,updated_at TEXT NOT NULL,"
            "message TEXT,output_path TEXT,written_sha TEXT,source_sig TEXT)"
        )
        self.now = lambda: datetime.now(UTC)
        self.stopped = threading.Event()

    def jobs(self):
        return {"scan": {"cron": self.config.get("cron", "*/15 * * * *"), "callback": self.scan}}

    def status(self):
        return {
            "enabled": True,
            "address_path": "/dav/goodnotes/",
            "last_backup_at": self.context.get("last_backup_at"),
            "notebooks": self.db.rows(
                "SELECT path,topic,status,updated_at,message FROM goodnotes_files ORDER BY path"
            ),
        }

    def match_topic(self, relative):
        for prefix, topic in sorted(
            self.config.get("mapping", {}).items(), key=lambda p: -len(p[0])
        ):
            if relative.startswith(prefix):
                return topic
        topics = {
            a["name"].strip().casefold(): a["name"]
            for a in self.library.access.discover()
            if a["kind"] == "topic"
        }
        path = Path(relative)
        for name in (path.parent.name, path.stem):
            if name.strip().casefold() in topics:
                return topics[name.strip().casefold()]
        return None

    def record(self, relative, topic, status, at, message=None):
        self.db.execute(
            "INSERT INTO goodnotes_files(path,topic,status,updated_at,message) VALUES(?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET topic=excluded.topic,status=excluded.status,"
            "updated_at=excluded.updated_at,message=excluded.message",
            (relative, topic, status, at, message),
        )

    async def scan(self):
        self.stopped.clear()
        task = asyncio.create_task(asyncio.to_thread(self._scan))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A cancelled to_thread continues running. Stop before its next publication,
            # and join it before allowing plugin reconfiguration or the next scan.
            self.stopped.set()
            await task
            raise

    async def teardown(self):
        self.stopped.set()

    def _scan(self):
        dav_path(self.context.data_dir / "dav", "goodnotes")
        seen, latest = set(), self.context.get("last_backup_at")
        for folder, directories, filenames in os.walk(self.root, followlinks=False):
            directories[:] = [
                d for d in directories if not d.startswith(".") and not is_link(Path(folder) / d)
            ]
            for filename in sorted(filenames):
                if self.stopped.is_set():
                    return
                source = Path(folder) / filename
                if (
                    is_link(source)
                    or filename.startswith(".")
                    or source.suffix.lower() not in {".pdf", ".goodnotes"}
                ):
                    continue
                relative = source.relative_to(self.root).as_posix()
                seen.add(relative)
                stat = source.stat()
                at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
                if latest is None or at > latest:
                    latest = at
                topic = self.match_topic(relative)
                if source.suffix.lower() == ".goodnotes":
                    self.record(relative, topic, "wrong_format", at, WRONG_FORMAT)
                elif (self.now().timestamp() - stat.st_mtime) < self.config.get(
                    "settle_seconds", 120
                ):
                    self.record(relative, topic, "waiting", at)
                elif topic is None:
                    self.record(relative, None, "unmapped", at, UNMAPPED)
                elif not self.unchanged(relative, topic, signature(stat)):
                    try:
                        self.copy(source, relative, topic, at)
                    except ValueError:
                        self.record(relative, topic, "conflict", at, "目标路径无效或包含符号链接")
        for row in self.db.rows("SELECT path FROM goodnotes_files"):
            if row["path"] not in seen:
                self.db.execute(
                    "UPDATE goodnotes_files SET status='missing',message='已不在备份里' WHERE path=?",
                    (row["path"],),
                )
        self.context.put("last_backup_at", latest)
        self.context.put("last_run_at", self.now().isoformat())
        self.library.sync()

    def unchanged(self, relative, topic, sig):
        """Copied before and not touched since: skip the copy and the hash (notebooks with
        imported slides run to hundreds of MB, and this runs every 15 minutes)."""
        rows = self.db.rows(
            "SELECT topic,status,output_path,source_sig FROM goodnotes_files WHERE path=?",
            (relative,),
        )
        if not rows or rows[0]["status"] != "copied" or rows[0]["topic"] != topic:
            return False
        target = safe_path(self.context.memory.root, rows[0]["output_path"])
        return rows[0]["source_sig"] == sig and target.is_file()

    def copy(self, source, relative, topic, at):
        self.library.validate_topic(topic)
        output_relative = f"知识库/{topic}/GoodNotes/{source.name}"
        target = safe_path(self.context.memory.root, output_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.library.access.discover()  # Freeze the default readers for any new mapped topic.
        previous = self.db.rows("SELECT * FROM goodnotes_files WHERE path=?", (relative,))
        previous = previous[0] if previous else {}
        owned = previous.get("output_path") == output_relative and previous.get("written_sha")
        other = self.db.rows(
            "SELECT path FROM goodnotes_files WHERE output_path=? AND path!=?",
            (output_relative, relative),
        )
        if target.exists() and (not owned or other or sha256(target) != previous["written_sha"]):
            self.record(
                relative,
                topic,
                "conflict",
                at,
                CONFLICT if owned else "同名文件不属于此备份，未覆盖",
            )
            return
        before = source.stat()
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".goodnotes-", delete=False
            ) as output:
                name = output.name
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                self.record(relative, topic, "waiting", at)
                return
            fingerprint = sha256(Path(name))
            if self.stopped.is_set():
                return
            safe_path(self.context.memory.root, output_relative)
            if target.exists():
                if not owned or sha256(target) != previous["written_sha"]:
                    self.record(relative, topic, "conflict", at, CONFLICT)
                    return
                if fingerprint != previous["written_sha"]:
                    os.replace(name, target)
            else:
                try:
                    os.link(name, target)  # Never clobber an unowned file, even on a race.
                except FileExistsError:
                    self.record(relative, topic, "conflict", at, "同名文件已存在，未覆盖")
                    return
            self.record(relative, topic, "copied", at)
            self.db.execute(
                "UPDATE goodnotes_files SET output_path=?,written_sha=?,source_sig=? WHERE path=?",
                (output_relative, fingerprint, signature(after), relative),
            )
        finally:
            if name:
                Path(name).unlink(missing_ok=True)
