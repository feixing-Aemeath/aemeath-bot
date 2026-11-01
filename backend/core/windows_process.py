"""Thin Win32 Job Object boundary, imported only on Windows.

Closing the job stops descendants even when their launcher has already exited. This also
contains npm/node grandchildren and the Agent's stdio MCP bridge.
"""


class ProcessJob:
    def __init__(self, pid):
        import psutil
        import win32api
        import win32con
        import win32job
        import win32process

        self.api, self.jobs = win32api, win32job
        self.handle = win32job.CreateJobObject(None, "")
        try:
            info = win32job.QueryInformationJobObject(
                self.handle, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self.handle, win32job.JobObjectExtendedLimitInformation, info
            )
            process = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid
            )
            try:
                win32job.AssignProcessToJobObject(self.handle, process)
            finally:
                process.Close()
            # Every caller creates its process with CREATE_SUSPENDED. Assign first and
            # resume its main thread only afterwards, so descendants inherit the job.
            for thread in psutil.Process(pid).threads():
                handle = win32api.OpenThread(0x0002, False, thread.id)  # THREAD_SUSPEND_RESUME
                try:
                    win32process.ResumeThread(handle)
                finally:
                    handle.Close()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None
