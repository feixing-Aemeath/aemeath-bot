"""Upgrades and private file modes using temporary SQLite files only."""

import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.db import Database
from core.migrations import upgrade_v9
from core.plugins import PluginContext
from core.store import Store
from plugins.grades.plugin import SCHEMA as GRADES_SCHEMA
from plugins.grades.plugin import Plugin as GradesPlugin

OLD_TIMETABLE = """
CREATE TABLE timetable_entries (
  day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, title TEXT NOT NULL,
  kind TEXT NOT NULL, PRIMARY KEY (day, start_time, title));
CREATE TABLE timetable_weeks (monday TEXT PRIMARY KEY, read_at TEXT NOT NULL);
CREATE TABLE timetable_calendar_events (
  client_id TEXT PRIMARY KEY, event_id TEXT, payload_json TEXT NOT NULL, synced_json TEXT);
"""


@pytest.fixture
def version8(tmp_path):
    path = tmp_path / "state" / "assistant.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.executescript(GRADES_SCHEMA + OLD_TIMETABLE)
        conn.executemany(
            "INSERT INTO grades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    identifier,
                    term,
                    course,
                    old_key,
                    score,
                    str(score),
                    1,
                    3,
                    "必修",
                    "agent",
                    '["source"]',
                    "2026-10-01",
                    "2026-10-01",
                )
                for identifier, term, course, old_key, score in (
                    ("sharp", "2026-2027-1", "C#", "c", 80),
                    ("math", "2026-2027-1", "高等数学（A）", "高等数学a", 90),
                    ("plus", "2025-2026-2", "C++", "c", 90),
                )
            ],
        )
        conn.execute(
            "INSERT INTO timetable_entries VALUES('2026-10-20','08:00','09:35','英语','课程')"
        )
        conn.execute("INSERT INTO timetable_weeks VALUES('2026-10-19','2026-10-20')")
        conn.execute("INSERT INTO timetable_calendar_events VALUES('client','event','{}','{}')")
        conn.execute("PRAGMA user_version=8")
    return path


async def test_v9_migrates_grade_keys_without_new_releases(version8):
    with sqlite3.connect(version8) as conn:
        conn.row_factory = sqlite3.Row
        before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM grades")}
    for _ in range(2):
        db = Database(version8)
        rows = db.rows("SELECT * FROM grades")
        assert len(rows) == len(before)
        assert {r["course"]: r["course_key"] for r in rows} == {
            "C#": "c#",
            "C++": "c++",
            "高等数学（A）": "高等数学(a)",
        }
        for row in rows:
            assert {k: v for k, v in row.items() if k != "course_key"} == {
                k: v for k, v in before[row["id"]].items() if k != "course_key"
            }
        assert db.rows("PRAGMA user_version")[0]["user_version"] == 10
    plugin = GradesPlugin()
    await plugin.setup(PluginContext(Store(db), version8.parent, "grades"), {})
    result = plugin._upsert("C#", 80, 3, "2026-2027-1", "必修", "agent", ["source"])
    assert result["disposition"] == "updated" and result["grade"]["id"] == "sharp"
    assert result["grade"]["updated_at"] == before["sharp"]["updated_at"]
    assert len(plugin.rows()) == len(before)


def test_v9_migrates_timetable_without_losing_rows_or_receipts(version8):
    for _ in range(2):
        db = Database(version8)
        assert db.rows("SELECT * FROM timetable_entries") == [
            {
                "day": "2026-10-20",
                "start_time": "08:00",
                "end_time": "09:35",
                "title": "英语",
                "kind": "课程",
            }
        ]
        assert db.rows("SELECT * FROM timetable_weeks") == [
            {"monday": "2026-10-19", "read_at": "2026-10-20"}
        ]
        assert db.rows("SELECT * FROM timetable_calendar_events") == [
            {"client_id": "client", "event_id": "event", "payload_json": "{}", "synced_json": "{}"}
        ]
        primary = {r["name"] for r in db.rows("PRAGMA table_info(timetable_entries)") if r["pk"]}
        assert primary == {"day", "start_time", "end_time", "title"}
    db.execute("INSERT INTO timetable_entries VALUES('2026-10-20','08:00','11:30','英语','课程')")
    assert len(Database(version8).rows("SELECT * FROM timetable_entries")) == 2


def test_v9_upgrade_without_school_plugins_is_repeatable(tmp_path):
    path = tmp_path / "assistant.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE custom_data(value TEXT)")
        conn.execute("INSERT INTO custom_data VALUES('keep')")
        conn.execute("PRAGMA user_version=8")
    for _ in range(2):
        db = Database(path)
        assert db.rows("PRAGMA user_version")[0]["user_version"] == 10
        assert db.rows("SELECT * FROM custom_data") == [{"value": "keep"}]


def test_v9_failed_upgrade_rolls_back_keys_schema_and_version(version8, monkeypatch):
    def interrupted(conn):
        upgrade_v9(conn)
        raise RuntimeError("synthetic_upgrade_failure")

    with monkeypatch.context() as patch:
        patch.setattr("core.db.upgrade_v9", interrupted)
        with pytest.raises(RuntimeError, match="synthetic_upgrade_failure"):
            Database(version8)
    with sqlite3.connect(version8) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert dict(conn.execute("SELECT id,course_key FROM grades")) == {
            "sharp": "c",
            "math": "高等数学a",
            "plus": "c",
        }
        assert "end_time" not in {
            row[1] for row in conn.execute("PRAGMA table_info(timetable_entries)") if row[5]
        }
        assert conn.execute("SELECT COUNT(*) FROM timetable_entries").fetchone()[0] == 1
    assert Database(version8).rows("PRAGMA user_version")[0]["user_version"] == 10


def test_database_skips_chmod_on_windows(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("chmod called on Windows")

    monkeypatch.setattr("core.private_files.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(Path, "chmod", forbidden)
    db = Database(tmp_path / "state" / "assistant.sqlite3")
    db.put("synthetic", "private")
    assert db.get("synthetic") == "private"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_database_and_live_sidecars_are_private_with_permissive_umask(tmp_path):
    old = os.umask(0)
    try:
        db = Database(tmp_path / "state" / "assistant.sqlite3")
        assert stat.S_IMODE(db.path.parent.stat().st_mode) == 0o700
        with db.connect() as conn:
            conn.execute("INSERT INTO kv VALUES('test','1')")
            for path in (
                db.path,
                db.path.with_name(db.path.name + "-wal"),
                db.path.with_name(db.path.name + "-shm"),
            ):
                assert path.exists()
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        os.umask(old)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_startup_tightens_existing_database_sidecars_and_backups(tmp_path):
    path = tmp_path / "state" / "assistant.sqlite3"
    path.parent.mkdir(mode=0o755)
    holder = sqlite3.connect(path)
    try:
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("CREATE TABLE old_data(value TEXT)")
        holder.execute("INSERT INTO old_data VALUES('private')")
        holder.commit()
        files = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]
        backup = path.parent / "backups" / "old"
        backup.mkdir(parents=True)
        saved = backup / "assistant.sqlite3"
        saved.write_bytes(b"synthetic backup")
        for item in (path.parent, backup.parent, backup):
            item.chmod(0o755)
        for item in (*files, saved):
            item.chmod(0o644)
        db = Database(path)
        assert db.rows("SELECT * FROM old_data") == [{"value": "private"}]
        for item in (path.parent, backup.parent, backup):
            assert stat.S_IMODE(item.stat().st_mode) == 0o700
        for item in (*files, saved):
            assert stat.S_IMODE(item.stat().st_mode) == 0o600
    finally:
        holder.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
async def test_new_backup_directory_and_database_are_private(service):
    old = os.umask(0)
    try:
        await service.jobs.backup()
    finally:
        os.umask(old)
    root = service.settings.data_dir / "backups"
    [backup] = root.iterdir()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert stat.S_IMODE((backup / "assistant.sqlite3").stat().st_mode) == 0o600
