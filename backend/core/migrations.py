"""Data-preserving upgrades, run in the database startup transaction."""

import unicodedata


def course_key(course):
    """The key v9 stores. Kept here: a migration must not change when the plugin does."""
    return "".join(c for c in unicodedata.normalize("NFKC", course).casefold() if not c.isspace())


def upgrade_v9(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "grades" in tables:
        rows = conn.execute("SELECT id,course FROM grades").fetchall()
        # Temporary keys avoid conflicts with another row's old key during the rewrite.
        prefix = "migration-v9:"
        while conn.execute(
            "SELECT 1 FROM grades WHERE course_key LIKE ?", (prefix + "%",)
        ).fetchone():
            prefix += ":"
        conn.executemany(
            "UPDATE grades SET course_key=? WHERE id=?",
            [(prefix + row["id"], row["id"]) for row in rows],
        )
        conn.executemany(
            "UPDATE grades SET course_key=? WHERE id=?",
            [(course_key(row["course"]), row["id"]) for row in rows],
        )
    if "timetable_entries" in tables:
        primary = {
            row["name"] for row in conn.execute("PRAGMA table_info(timetable_entries)") if row["pk"]
        }
        if "end_time" not in primary:
            conn.execute("""
                CREATE TABLE timetable_entries_v9 (
                  day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
                  title TEXT NOT NULL, kind TEXT NOT NULL,
                  PRIMARY KEY (day, start_time, end_time, title))
            """)
            conn.execute("INSERT INTO timetable_entries_v9 SELECT * FROM timetable_entries")
            conn.execute("DROP TABLE timetable_entries")
            conn.execute("ALTER TABLE timetable_entries_v9 RENAME TO timetable_entries")
    conn.execute("PRAGMA user_version=9")


def upgrade_v10(conn):
    """Restore plugin names omitted by older multi-source todos and refresh their Apple notes."""
    from core.store import enqueue

    conn.execute("DROP INDEX IF EXISTS messages_pending")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS messages_pending_page ON messages(processed,priority DESC,sent_at,id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS messages_history ON messages(sent_at DESC,id)")
    missing = conn.execute("""
        SELECT DISTINCT t.id, t.source_plugin, m.plugin
        FROM todos t JOIN json_each(t.source_refs) ref JOIN messages m ON m.id=ref.value
        WHERE instr(',' || t.source_plugin || ',', ',' || m.plugin || ',')=0
    """).fetchall()
    plugins = {}
    for row in missing:
        plugins.setdefault(row["id"], set(row["source_plugin"].split(","))).add(row["plugin"])
    for identifier, names in plugins.items():
        conn.execute(
            "UPDATE todos SET source_plugin=? WHERE id=?", (",".join(sorted(names)), identifier)
        )
        enqueue(conn, identifier, fields=[])
    conn.execute("PRAGMA user_version=10")
