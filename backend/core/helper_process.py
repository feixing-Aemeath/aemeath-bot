"""Running the Swift helpers (apple-sync, screen-helper), one request per process.

Both start a copy of themselves that disclaims the parent, so macOS grants the privacy
permissions to the helper and not to Python, and that copy does the work. Killing only the
process the backend started would leave the copy running: still clicking, capturing or
writing reminders, and overlapping the retry. So each helper runs in a process group of its
own (the copy stays in it), and a timeout or cancellation kills the whole group.
"""

import asyncio
import os
import signal
import subprocess
import sys

from core.platforms import command_argv

WINDOWS = sys.platform == "win32"


def process_options():
    if WINDOWS:
        # Suspended until attached to its Job Object: no npm/helper grandchild can escape
        # between CreateProcess and AssignProcessToJobObject.
        return {"creationflags": 0x00000004 | 0x00000200 | 0x08000000}
    return {"start_new_session": True}


def attach_job(process):
    if WINDOWS:
        from core.windows_process import ProcessJob

        process._assistant_job = ProcessJob(process.pid)


def close_job(process):
    job = getattr(process, "_assistant_job", None)
    if job:
        job.close()


def stop_sync(process):
    if WINDOWS:
        close_job(process)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


async def start(argv, **kwargs):
    process = await asyncio.create_subprocess_exec(
        *command_argv(argv), **process_options(), **kwargs
    )
    try:
        attach_job(process)
    except BaseException:
        # Fail closed if the process cannot be contained, including a suspended process.
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return process


async def stop(process):
    if WINDOWS:
        close_job(process)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


def run_sync(argv, *, input=None, timeout=30, **kwargs):
    """Synchronous short-lived tools, with the same descendant cleanup as async tools."""
    process = subprocess.Popen(command_argv(argv), **process_options(), **kwargs)
    try:
        attach_job(process)
        stdout, stderr = process.communicate(input, timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except BaseException:
        stop_sync(process)
        process.communicate()
        raise
    finally:
        close_job(process)


async def run(argv, data, timeout, *, capture_error=False, env=None, cwd=None):
    """Return (exit code, stdout), or stderr on failure when capture_error is set.

    Callers must classify captured errors without logging arbitrary helper output.
    """
    process = await start(
        argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if capture_error else asyncio.subprocess.DEVNULL,
        env=env,
        cwd=cwd,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(data), timeout)
    except (TimeoutError, asyncio.CancelledError):
        await stop(process)
        raise
    finally:
        close_job(process)
    return process.returncode, error if capture_error and process.returncode else output
