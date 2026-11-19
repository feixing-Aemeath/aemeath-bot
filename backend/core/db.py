import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core import private_files
from core.migrations import upgrade_v9, upgrade_v10


def now() -> str:
    return datetime.now(UTC).isoformat()


def uid() -> str:
    return str(uuid4())


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Database:
    def __init__(self, path: Path):
        self.path = path
        private_files.directory(path.parent)
        private_files.backups(path.parent / "backups")
        private_files.sqlite_files(path)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, plugin TEXT NOT NULL, external_id TEXT NOT NULL,
              chat_name TEXT NOT NULL, sender TEXT NOT NULL, sent_at TEXT NOT NULL,
              text TEXT NOT NULL, screenshot_path TEXT, priority INTEGER NOT NULL DEFAULT 0,
              processed INTEGER NOT NULL DEFAULT 0, preview INTEGER NOT NULL DEFAULT 0,
              revised INTEGER NOT NULL DEFAULT 0, flags TEXT NOT NULL DEFAULT '[]',
              outgoing INTEGER NOT NULL DEFAULT 0, UNIQUE(plugin,external_id));
            CREATE INDEX IF NOT EXISTS messages_chat ON messages(plugin,chat_name,sent_at);
            CREATE TABLE IF NOT EXISTS todos (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL,
              due_at TEXT, has_time INTEGER NOT NULL, source_plugin TEXT NOT NULL,
              source_refs TEXT NOT NULL, source_excerpt TEXT NOT NULL, confidence REAL NOT NULL,
              status TEXT NOT NULL, dedup_key TEXT NOT NULL UNIQUE, reminder_id TEXT,
              calendar_event_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, sync_status TEXT NOT NULL DEFAULT 'pending', sync_error TEXT);
            CREATE TABLE IF NOT EXISTS outbox (
              todo_id TEXT PRIMARY KEY REFERENCES todos(id), revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS todo_changes (
              id TEXT PRIMARY KEY, todo_id TEXT NOT NULL, old_status TEXT NOT NULL,
              status TEXT NOT NULL, from_agent INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS todo_dedup_keys (
              dedup_key TEXT NOT NULL, todo_id TEXT NOT NULL REFERENCES todos(id),
              PRIMARY KEY(dedup_key,todo_id));
            CREATE TABLE IF NOT EXISTS extraction_missed (
              kind TEXT NOT NULL, reference_id TEXT NOT NULL, created_at TEXT NOT NULL,
              source_json TEXT, PRIMARY KEY(kind,reference_id));
            CREATE TABLE IF NOT EXISTS inbox_submissions (
              message_id TEXT PRIMARY KEY REFERENCES messages(id), text_hash TEXT NOT NULL,
              created_at TEXT NOT NULL, metadata_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS inbox_text_time ON inbox_submissions(text_hash,created_at);
            CREATE TABLE IF NOT EXISTS apple_reminder_state (
              todo_id TEXT PRIMARY KEY REFERENCES todos(id), reminder_id TEXT NOT NULL,
              list_id TEXT, missing_reads INTEGER NOT NULL DEFAULT 0, missing_since TEXT);
            CREATE TABLE IF NOT EXISTS digests (
              id TEXT PRIMARY KEY, date TEXT NOT NULL, plugin TEXT NOT NULL,
              summary TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(date,plugin));
            CREATE INDEX IF NOT EXISTS digests_history ON digests(date DESC,plugin);
            CREATE INDEX IF NOT EXISTS digests_plugin_history ON digests(plugin,date DESC);
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, job TEXT NOT NULL, harness TEXT, started_at TEXT NOT NULL,
              finished_at TEXT, status TEXT NOT NULL, error TEXT, attempt INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS runs_job ON runs(job,started_at);
            CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at DESC,id DESC);
            CREATE INDEX IF NOT EXISTS runs_job_status_started
              ON runs(job,status,started_at DESC,id DESC);
            CREATE TABLE IF NOT EXISTS run_usage (
              run_id TEXT PRIMARY KEY REFERENCES runs(id), job TEXT NOT NULL,
              harness TEXT NOT NULL, recorded_at TEXT NOT NULL, input_tokens INTEGER NOT NULL,
              cached_input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
              cost_usd REAL);
            CREATE INDEX IF NOT EXISTS run_usage_time ON run_usage(recorded_at);
            CREATE TABLE IF NOT EXISTS plugins (
              id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
              config_json TEXT NOT NULL DEFAULT '{}', last_run_at TEXT,
              deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS health_samples (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS health_reports (
              id TEXT PRIMARY KEY, date TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
              title TEXT, body TEXT, input_json TEXT NOT NULL DEFAULT '{}',
              rejections INTEGER NOT NULL DEFAULT 0, error TEXT, harness TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(date, kind));
            CREATE INDEX IF NOT EXISTS health_reports_history ON health_reports(date DESC,kind);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(path UNINDEXED,content,
              tokenize='trigram');
            CREATE TABLE IF NOT EXISTS device_tokens (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
              hint TEXT NOT NULL, scopes TEXT NOT NULL, created_at TEXT NOT NULL,
              expires_at TEXT, last_used_at TEXT, revoked_at TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS device_tokens_active_name
              ON device_tokens(name) WHERE revoked_at IS NULL;
            CREATE TABLE IF NOT EXISTS knowledge_areas (
              path TEXT PRIMARY KEY, readers TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge_notes (path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS knowledge_sources (
              path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, status TEXT NOT NULL,
              error TEXT, organized_at TEXT, notes TEXT NOT NULL DEFAULT '[]',
              organize_offset INTEGER NOT NULL DEFAULT 0);
            """)
            # Add message metadata to older databases without rewriting their history.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            for column in ("preview", "revised", "outgoing"):
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE messages ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            if "flags" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN flags TEXT NOT NULL DEFAULT '[]'")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(device_tokens)")}
            if "reader" not in columns:
                conn.execute("ALTER TABLE device_tokens ADD COLUMN reader TEXT")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_sources)")}
            if "organize_offset" not in columns:
                conn.execute(
                    "ALTER TABLE knowledge_sources ADD COLUMN organize_offset INTEGER NOT NULL DEFAULT 0"
                )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(todos)")}
            for column in ("status_source", "reminder_completed_at"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE todos ADD COLUMN {column} TEXT")
            if "user_modified_fields" not in columns:
                conn.execute(
                    "ALTER TABLE todos ADD COLUMN user_modified_fields TEXT NOT NULL DEFAULT '[]'"
                )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(todo_changes)")}
            if "reason" not in columns:
                conn.execute("ALTER TABLE todo_changes ADD COLUMN reason TEXT")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(outbox)")}
            if "reminders" not in columns:
                conn.execute("ALTER TABLE outbox ADD COLUMN reminders INTEGER NOT NULL DEFAULT 1")
            if "fields" not in columns:
                conn.execute(
                    "ALTER TABLE outbox ADD COLUMN fields TEXT NOT NULL "
                    'DEFAULT \'["title","due_at","has_time","status"]\''
                )
            if "agent_base" not in columns:
                conn.execute("ALTER TABLE outbox ADD COLUMN agent_base TEXT NOT NULL DEFAULT '{}'")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(apple_reminder_state)")}
            if "last_modified_at" not in columns:
                conn.execute("ALTER TABLE apple_reminder_state ADD COLUMN last_modified_at TEXT")
            conn.execute(
                "INSERT OR IGNORE INTO todo_dedup_keys SELECT dedup_key,id FROM todos "
                "WHERE dedup_key NOT LIKE 'cancelled:%'"
            )
            conn.execute(
                "UPDATE todos SET dedup_key='cancelled:'||id "
                "WHERE status='cancelled' AND dedup_key NOT LIKE 'cancelled:%'"
            )
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 9:
                upgrade_v9(conn)
            if version < 10:
                upgrade_v10(conn)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def rows(self, sql, args=()):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.connect() as conn:
            return conn.execute(sql, args).rowcount

    def get(self, key, default=None):
        rows = self.rows("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def put(self, key, value):
        self.execute(
            "INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, dump(value)),
        )

    def start_run(self, job, harness=None, attempt=1):
        run_id = uid()
        self.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
            (run_id, job, harness, now(), None, "running", None, attempt),
        )
        return run_id

    def finish_run(self, run_id, status="succeeded", error=None):
        self.execute(
            "UPDATE runs SET finished_at=?,status=?,error=? WHERE id=?",
            (now(), status, error, run_id),
        )

    def record_usage(self, run_id, job, harness, usage):
        """Tokens an Agent run reported (harness.base.Usage); counts only, never output."""
        self.execute(
            "INSERT OR REPLACE INTO run_usage VALUES(?,?,?,?,?,?,?,?)",
            (
                run_id,
                job,
                harness,
                now(),
                usage.input_tokens,
                usage.cached_input_tokens,
                usage.output_tokens,
                usage.cost_usd,
            ),
        )

    def usage_since(self, since):
        """run_usage rows recorded at or after the UTC ISO time `since`, oldest first."""
        return self.rows(
            "SELECT * FROM run_usage WHERE recorded_at>=? ORDER BY recorded_at", (since,)
        )

    def recover_runs(self):
        self.execute(
            "UPDATE runs SET status='failed',error='interrupted',finished_at=? "
            "WHERE status='running'",
            (now(),),
        )
