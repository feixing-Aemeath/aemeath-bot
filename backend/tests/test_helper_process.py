"""The Swift helpers do their work in a copy of themselves (they disclaim the parent for the
privacy permissions); a timeout or cancellation must stop that copy too, not only the
process the backend started. A shell script with a background job stands in for them."""

import asyncio

import pytest

from core import helper_process
from core.apple import SwiftAppleBridge
from plugins.screen.helper import JsonHelper


def helper(tmp_path):
    """A helper whose child would touch `late` after a second, while the parent waits."""
    late = tmp_path / "late"
    script = tmp_path / "helper.py"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(late)!r}).touch()"
    script.write_text(
        f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child!r}]).wait()\n",
        encoding="utf-8",
    )
    return script, late


async def test_a_timeout_stops_the_helpers_child_too(tmp_path):
    script, late = helper(tmp_path)
    with pytest.raises(TimeoutError):
        await helper_process.run([str(script)], b"{}", 0.2)
    await asyncio.sleep(1.3)
    assert not late.exists()


async def test_a_cancelled_job_stops_the_helpers_child_too(tmp_path):
    script, late = helper(tmp_path)
    task = asyncio.create_task(helper_process.run([str(script)], b"{}", 10))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.3)
    assert not late.exists()


async def test_both_helpers_run_in_their_own_group(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    script, late = helper(tmp_path)
    with pytest.raises(RuntimeError, match="^screen_helper_timeout$"):
        await JsonHelper(script).call({"action": "preview"}, timeout=0.2)
    with pytest.raises(TimeoutError):
        await SwiftAppleBridge(script, timeout=0.2).call("reminders", "upsert", {})
    await asyncio.sleep(1.3)
    assert not late.exists()


async def test_screen_helpers_take_turns(tmp_path, monkeypatch):
    # Two captures at once (the QQ read and the timetable at :00) left one hanging; a second
    # helper started while the first runs would find its busy file and fail.
    monkeypatch.setattr("sys.platform", "darwin")
    script = tmp_path / "helper.py"
    script.write_text(
        "import os,sys,time\nfrom pathlib import Path\nsys.stdin.buffer.read()\n"
        "busy = Path(__file__).with_suffix('.busy')\n"
        "try:\n    fd = os.open(busy, os.O_CREAT | os.O_EXCL | os.O_WRONLY)\n"
        'except FileExistsError:\n    print(\'{"error":"screen_overlap"}\'); sys.exit(1)\n'
        "os.close(fd)\ntime.sleep(0.3)\nbusy.unlink()\nprint('{\"blocks\":[]}')\n",
        encoding="utf-8",
    )
    qq, chrome = JsonHelper(script, app="qq"), JsonHelper(script, app="chrome")
    results = await asyncio.gather(
        qq.call({"action": "preview"}), chrome.call({"action": "capture_page", "title": "课表"})
    )
    assert results == [{"blocks": []}, {"blocks": []}]


async def test_a_helper_that_finishes_gives_its_code_and_output(tmp_path):
    script = tmp_path / "helper.py"
    script.write_text(
        "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(3)\n",
        encoding="utf-8",
    )
    assert await helper_process.run([str(script)], b'{"a":1}', 5) == (3, b'{"a":1}')
