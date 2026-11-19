import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import predeploy_check

# The check is run on the Mac before a deploy. Its fake helper is a script with a shebang and its
# database name has a ? in it, neither of which Windows allows.
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Mac deploy check")

TITLE = "合成私密标题_勿输出"
TODO_ID = "synthetic-private-todo-id"
REMINDER_ID = "synthetic-private-reminder-id"
LIST_ID = "synthetic-private-list-id"
SECRET = "合成人名_聊天全文_备注_不得输出"
DUE = "2026-11-06T18:00:00+08:00"


@pytest.fixture
def version5(tmp_path):
    path = tmp_path / "副本 #?&.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA user_version=5;
            CREATE TABLE todos (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL,
              due_at TEXT, has_time INTEGER NOT NULL, source_plugin TEXT NOT NULL,
              source_refs TEXT NOT NULL, source_excerpt TEXT NOT NULL, confidence REAL NOT NULL,
              status TEXT NOT NULL, dedup_key TEXT NOT NULL UNIQUE, reminder_id TEXT,
              calendar_event_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, sync_status TEXT NOT NULL DEFAULT 'pending', sync_error TEXT);
            CREATE TABLE outbox (
              todo_id TEXT PRIMARY KEY REFERENCES todos(id), revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.execute(
            "INSERT INTO kv VALUES('settings',?)", (json.dumps({"timezone": "Asia/Shanghai"}),)
        )
        conn.execute("INSERT INTO kv VALUES('private',?)", (SECRET,))
    conn.close()
    return path


def add_todo(path, suffix="", **changes):
    values = {
        "id": TODO_ID + suffix,
        "title": TITLE + suffix,
        "notes": SECRET,
        "due_at": DUE,
        "has_time": 1,
        "source_plugin": "feishu",
        "source_refs": json.dumps(["synthetic-message-id"]),
        "source_excerpt": SECRET,
        "confidence": 0.95,
        "status": "pending",
        "dedup_key": "synthetic-key" + suffix,
        "reminder_id": REMINDER_ID + suffix,
        "calendar_event_id": None,
        "created_by": "agent",
        "created_at": DUE,
        "updated_at": DUE,
    }
    values.update(changes)
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"INSERT INTO todos ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )
    conn.close()
    return values


def snapshot(row, **changes):
    item = {
        "id": row["reminder_id"],
        "current_id": None,
        "exists": True,
        "list_id": LIST_ID,
        "completed": row["status"] == "completed",
        "completed_at": None,
        "title": row["title"],
        "due_at": row["due_at"],
        "has_time": bool(row["has_time"]),
        "last_modified_at": DUE,
    }
    item.update(changes)
    return item


def missing(row):
    return {
        key: row["reminder_id"] if key == "id" else False if key == "exists" else None
        for key in snapshot(row)
    }


def fake_helper(tmp_path, batch=None, *, error=None, output=None):
    path = tmp_path / "fake apple-sync"
    path.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['reminders', 'read']\n"
        "payload = json.load(sys.stdin)\n"
        "assert set(payload) == {'reminder_ids', 'client_ids', 'timezone'}\n"
        "assert len(payload['reminder_ids']) == len(payload['client_ids'])\n"
        "assert payload['timezone'] == 'Asia/Shanghai'\n"
        "assert not any(i.endswith('-excluded') for i in payload['reminder_ids'])\n"
        f"sys.stderr.write({(error or SECRET)!r})\n"
        f"sys.stdout.write({output if output is not None else json.dumps(batch)!r})\n"
        f"sys.exit({1 if error else 0})\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def run_check(path, capsys, helper=None):
    before = hashlib.sha256(path.read_bytes()).digest()
    files = {p.name for p in path.parent.iterdir()}
    args = ["--db", str(path)]
    if helper is not None:
        args += ["--apple-sync", str(helper)]
    result = predeploy_check.main(args)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert captured.err == ""
    assert all(re.fullmatch(r"[a-z_.]+=\d+", line) for line in captured.out.splitlines()[:-1])
    for private in (TITLE, TODO_ID, REMINDER_ID, LIST_ID, SECRET, str(path), str(helper)):
        assert private not in output
    assert hashlib.sha256(path.read_bytes()).digest() == before
    assert {p.name for p in path.parent.iterdir()} == files
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    assert "Traceback" not in output
    return result, captured.out


def test_all_reminder_categories_and_legacy_counts(version5, tmp_path, capsys):
    same = add_todo(version5, "-same", calendar_event_id="synthetic-private-event-id")
    status = add_todo(version5, "-status")
    reopened = add_todo(version5, "-reopened", status="completed")
    title = add_todo(version5, "-title")
    date = add_todo(version5, "-date")
    lost = add_todo(version5, "-missing")
    moved = add_todo(version5, "-moved")
    rebound = add_todo(version5, "-rebound")
    add_todo(version5, "-local", reminder_id=None)
    add_todo(version5, "-excluded", status="deleted")
    add_todo(version5, "-cancelled-excluded", status="cancelled")
    add_todo(version5, "-watchdog-excluded", source_plugin="watchdog")
    with sqlite3.connect(version5) as conn:
        conn.execute("INSERT INTO outbox(todo_id) VALUES(?)", (same["id"],))
    conn.close()
    helper = fake_helper(
        tmp_path,
        {
            "list_ids": [LIST_ID],
            "items": [
                snapshot(same),
                snapshot(status, completed=True, completed_at=DUE),
                snapshot(reopened, completed=False),
                snapshot(title, title=TITLE + " changed"),
                snapshot(date, due_at="2026-11-07T18:00:00+08:00"),
                missing(lost),
                snapshot(moved, list_id="synthetic-other-list-id"),
                snapshot(rebound, current_id="synthetic-private-new-id"),
            ],
        },
    )
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    for line in (
        "db.user_version=5",
        "db.integrity_errors=0",
        "db.foreign_key_errors=0",
        "db.todos.pending=9",
        "db.todos.completed=1",
        "db.todos.deleted=1",
        "db.todos.cancelled=1",
        "db.reminder_ids=11",
        "db.calendar_event_ids=1",
        "db.outbox=1",
        "db.apple_reminder_state_exists=0",
        "db.reminder_ids_without_state=11",
        "apple.requested=8",
        "apple.read=8",
        "apple.found=7",
        "apple.lists=1",
        "apple.consistent=2",
        "apple.status_only=2",
        "apple.title_or_date_changed=2",
        "apple.missing=1",
        "apple.other_list=1",
        "apple.current_id_changed=1",
    ):
        assert line in output.splitlines()
    assert output.splitlines()[-1].startswith("需要先看一下：")
    for code in (
        "outbox_pending",
        "reminders_user_modified_fields",
        "reminders_missing_manual_decision",
        "reminders_other_list_sync_held",
    ):
        assert code in output.splitlines()[-1]


def test_matching_and_status_only_can_deploy_with_reidentified_ids(version5, tmp_path, capsys):
    row = add_todo(version5)
    helper = fake_helper(
        tmp_path,
        {
            "list_ids": [LIST_ID],
            "items": [
                snapshot(row, completed=True, current_id="synthetic-private-new-id"),
            ],
        },
    )
    result, output = run_check(version5, capsys, helper)
    assert result == 0
    assert "apple.status_only=1" in output
    assert "apple.current_id_changed=1" in output
    assert output.splitlines()[-1] == "可以部署"


def test_no_helper_skips_apple_and_does_not_claim_ready(version5, capsys, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("helper must not start")

    monkeypatch.setattr(predeploy_check.helper_process, "run", forbidden)
    add_todo(version5, status=SECRET)
    result, output = run_check(version5, capsys)
    assert result == 1
    assert "db.todos.unknown=1" in output
    assert "apple.skipped=1" in output
    assert "apple_check_skipped" in output.splitlines()[-1]
    assert "db_status_invalid" in output.splitlines()[-1]


def test_existing_state_and_stale_binding_counts(version5, tmp_path, capsys):
    first = add_todo(version5)
    second = add_todo(version5, "-second")
    with sqlite3.connect(version5) as conn:
        conn.execute(
            "CREATE TABLE apple_reminder_state (todo_id TEXT PRIMARY KEY, reminder_id TEXT)"
        )
        conn.executemany(
            "INSERT INTO apple_reminder_state VALUES(?,?)",
            [
                (first["id"], first["reminder_id"]),
                (second["id"], "synthetic-stale-id"),
            ],
        )
    conn.close()
    helper = fake_helper(
        tmp_path, {"list_ids": [LIST_ID], "items": [snapshot(first), snapshot(second)]}
    )
    result, output = run_check(version5, capsys, helper)
    assert result == 0
    assert "db.apple_reminder_state_exists=1" in output
    assert "db.reminder_ids_without_state=1" in output
    assert "apple.consistent=2" in output


@pytest.mark.parametrize(
    ("due", "has_time", "remote_due", "remote_has_time", "changed"),
    [
        ("2026-11-06T10:00:00.123+00:00", 1, DUE, True, 0),
        ("2026-11-06T00:00:00+08:00", 0, DUE, False, 0),
        (DUE, 1, None, False, 1),
        (DUE, 1, DUE, False, 1),
        (None, 0, None, False, 0),
    ],
)
def test_uses_eventkit_date_comparison(
    version5, tmp_path, capsys, due, has_time, remote_due, remote_has_time, changed
):
    row = add_todo(version5, due_at=due, has_time=has_time)
    helper = fake_helper(
        tmp_path,
        {
            "list_ids": [LIST_ID],
            "items": [
                snapshot(row, due_at=remote_due, has_time=remote_has_time),
            ],
        },
    )
    result, output = run_check(version5, capsys, helper)
    assert result == changed
    assert f"apple.title_or_date_changed={changed}" in output


@pytest.mark.parametrize(
    "code",
    [
        "apple_reminders_denied",
        "apple_reminders_list_unavailable",
        "apple_reminders_read_failed",
        SECRET,
    ],
)
def test_helper_errors_are_redacted(version5, tmp_path, capsys, code):
    add_todo(version5)
    helper = fake_helper(tmp_path, error=code)
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    expected = "apple_reminders_read_failed" if code == SECRET else code
    assert output.splitlines()[-1] == "需要先看一下：" + expected


@pytest.mark.parametrize(
    "kind", ["json", "missing_item", "duplicate", "wrong_id", "invalid_bool", "no_lists"]
)
def test_invalid_helper_response_is_a_read_failure(version5, tmp_path, capsys, kind):
    row = add_todo(version5)
    items = [snapshot(row)]
    if kind == "missing_item":
        items = []
    elif kind == "duplicate":
        items *= 2
    elif kind == "wrong_id":
        items[0]["id"] = "synthetic-unrequested-id"
    elif kind == "invalid_bool":
        items[0]["exists"] = "yes"
    helper = fake_helper(
        tmp_path,
        {
            "list_ids": [] if kind == "no_lists" else [LIST_ID],
            "items": items,
        },
        output=SECRET if kind == "json" else None,
    )
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    expected = (
        "apple_reminders_list_unavailable" if kind == "no_lists" else "apple_reminders_read_failed"
    )
    assert output.splitlines()[-1] == "需要先看一下：" + expected


def test_missing_and_unexecutable_helpers(version5, tmp_path, capsys):
    helper = tmp_path / "missing-private-helper"
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    assert output.splitlines()[-1] == "需要先看一下：apple_sync_not_found"
    helper.write_text(SECRET, encoding="utf-8")
    helper.chmod(0o600)
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    assert output.splitlines()[-1] == "需要先看一下：apple_sync_unavailable"


def test_timeout_is_reported(version5, tmp_path, capsys, monkeypatch):
    helper = tmp_path / "slow-helper"
    helper.write_text(
        f"#!{Path(sys.executable).resolve()}\nimport time\ntime.sleep(20)\n", encoding="utf-8"
    )
    helper.chmod(0o700)
    monkeypatch.setattr(predeploy_check, "READ_TIMEOUT", 0.05)
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    assert "apple_reminders_timeout" in output.splitlines()[-1]


def test_empty_database_still_checks_helper_authorization(version5, tmp_path, capsys):
    helper = fake_helper(tmp_path, error="apple_reminders_denied")
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    assert "apple.requested=0" in output
    assert "apple_reminders_denied" in output


def test_foreign_key_errors_only_print_counts(version5, capsys):
    with sqlite3.connect(version5) as conn:
        conn.execute("INSERT INTO outbox(todo_id) VALUES(?)", (TODO_ID,))
    conn.close()
    result, output = run_check(version5, capsys)
    assert result == 1
    assert "db.foreign_key_errors=1" in output
    assert "db_foreign_key_failed" in output.splitlines()[-1]


def test_integrity_errors_do_not_print_private_index_names(version5, capsys):
    with sqlite3.connect(version5) as conn:
        conn.execute("CREATE TABLE synthetic(value TEXT)")
        conn.execute(f'CREATE INDEX "{SECRET}" ON synthetic(value)')
        conn.execute("INSERT INTO synthetic VALUES('example')")
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE name=?",
            (
                f"CREATE INDEX \"{SECRET}\" ON synthetic(value || 'changed')",
                SECRET,
            ),
        )
    conn.close()
    result, output = run_check(version5, capsys)
    assert result == 1
    assert "db.integrity_errors=1" in output
    assert "db_integrity_failed" in output.splitlines()[-1]


@pytest.mark.parametrize("settings", [SECRET, "[]", '{"timezone":null}', '{"timezone":"invalid"}'])
def test_invalid_settings_are_redacted(version5, capsys, settings):
    with sqlite3.connect(version5) as conn:
        conn.execute("UPDATE kv SET value=? WHERE key='settings'", (settings,))
    conn.close()
    result, output = run_check(version5, capsys)
    assert result == 1
    assert output.splitlines()[-1] == "需要先看一下：db_settings_invalid"


def test_missing_settings_use_default_timezone(version5, tmp_path, capsys):
    with sqlite3.connect(version5) as conn:
        conn.execute("DELETE FROM kv WHERE key='settings'")
    conn.close()
    helper = fake_helper(tmp_path, {"list_ids": [LIST_ID], "items": []})
    result, output = run_check(version5, capsys, helper)
    assert result == 0
    assert output.splitlines()[-1] == "可以部署"


def test_invalid_todo_date_is_reported_without_content(version5, tmp_path, capsys):
    row = add_todo(version5, due_at=SECRET)
    helper = fake_helper(tmp_path, {"list_ids": [LIST_ID], "items": [snapshot(row, due_at=DUE)]})
    result, output = run_check(version5, capsys, helper)
    assert result == 1
    assert output.splitlines()[-1] == "需要先看一下：db_todo_date_invalid"


@pytest.mark.parametrize("kind", ["missing", "corrupt", "schema"])
def test_bad_database_is_reported_without_paths(tmp_path, capsys, kind):
    path = tmp_path / (SECRET + ".sqlite3")
    if kind == "corrupt":
        path.write_bytes(SECRET.encode())
    elif kind == "schema":
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE private(value TEXT)")
    files = {p.name for p in tmp_path.iterdir()}
    result = predeploy_check.main(["--db", str(path)])
    output = capsys.readouterr()
    assert result == 1
    assert SECRET not in output.out + output.err
    assert "Traceback" not in output.out + output.err
    assert output.out.splitlines()[-1].startswith("需要先看一下：db_")
    assert {p.name for p in tmp_path.iterdir()} == files


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_wal_copy_is_rejected_without_reading_incomplete_data(version5, capsys, suffix):
    wal = Path(str(version5) + suffix)
    wal.write_bytes(SECRET.encode())
    before = version5.read_bytes(), wal.read_bytes()
    assert predeploy_check.main(["--db", str(version5)]) == 1
    output = capsys.readouterr()
    assert output.out.splitlines()[-1] == "需要先看一下：db_sidecars_present"
    assert SECRET not in output.out + output.err
    assert before == (version5.read_bytes(), wal.read_bytes())


def test_database_is_opened_readonly_without_migration(version5, tmp_path, capsys, monkeypatch):
    connect = sqlite3.connect
    calls = []

    def readonly(database, *args, **kwargs):
        assert "mode=ro" in database and "immutable=1" in database
        assert kwargs["uri"] is True
        conn = connect(database, *args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE forbidden(value TEXT)")
        calls.append(database)
        return conn

    def forbidden(*_args, **_kwargs):
        raise AssertionError("must not initialize Database or Service")

    monkeypatch.setattr(predeploy_check.sqlite3, "connect", readonly)
    monkeypatch.setattr("core.db.Database.__init__", forbidden)
    monkeypatch.setattr("core.service.Service.__init__", forbidden)
    helper = fake_helper(tmp_path, {"list_ids": [LIST_ID], "items": []})
    result, output = run_check(version5, capsys, helper)
    assert result == 0 and calls
    assert "db.user_version=5" in output


def test_cli_exit_code_and_argument_errors_do_not_echo_private_paths(version5, tmp_path):
    helper = fake_helper(tmp_path, {"list_ids": [LIST_ID], "items": []})
    for args, expected in [
        (["--db", str(version5), "--apple-sync", str(helper)], 0),
        (["--db", str(version5), "--private=" + SECRET], 2),
        ([], 2),
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.predeploy_check", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == expected
        assert SECRET not in result.stdout + result.stderr
        assert str(version5) not in result.stdout + result.stderr
        if expected == 2:
            assert result.stdout.splitlines()[-1] == "需要先看一下：arguments_invalid"
