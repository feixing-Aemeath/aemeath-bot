"""Thin Win32 window/capture/input boundary. Importing on Mac/Linux is harmless.

No keyboard API exists in this adapter. Input methods require a region and verify the
foreground window and the window under the pointer before sending a click or wheel event.
"""

import sys
from dataclasses import dataclass

from plugins.screen.regions import Rect, screen_point

PROCESSES = {"qq": {"qq.exe"}, "wechat": {"wechat.exe", "weixin.exe"}, "chrome": {"chrome.exe"}}


@dataclass(frozen=True)
class Window:
    handle: int
    frame: Rect
    title: str


class Desktop:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("windows_required")
        import ctypes

        import win32api
        import win32con
        import win32gui
        import win32process

        self.ctypes, self.api, self.con = ctypes, win32api, win32con
        self.gui, self.processes = win32gui, win32process
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        # Set before any window coordinates are queried. Windows 10 1703+.
        self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.user.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        self.user.OpenInputDesktop.restype = ctypes.c_void_p
        self.user.CloseDesktop.argtypes = [ctypes.c_void_p]

    def locked(self):
        # A service in Session 0, a locked desktop or a disconnected RDP session cannot
        # interact with the user's desktop. Fail closed in all of these cases.
        handle = self.user.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not handle:
            return True
        try:
            name = self.ctypes.create_unicode_buffer(256)
            needed = self.ctypes.c_uint32()
            self.user.GetUserObjectInformationW.argtypes = [
                self.ctypes.c_void_p,
                self.ctypes.c_int,
                self.ctypes.c_void_p,
                self.ctypes.c_uint32,
                self.ctypes.POINTER(self.ctypes.c_uint32),
            ]
            read = self.user.GetUserObjectInformationW(
                handle, 2, name, self.ctypes.sizeof(name), self.ctypes.byref(needed)
            )
            return not read or name.value.lower() != "default"
        finally:
            self.user.CloseDesktop(handle)

    def windows(self, app):
        names = PROCESSES.get(app)
        if names is None:
            raise RuntimeError("screen_unknown_app")
        from pathlib import PureWindowsPath

        running = set()
        for pid in self.processes.EnumProcesses():
            try:
                process = self.api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
                try:
                    name = PureWindowsPath(
                        self.processes.QueryFullProcessImageName(process, 0)
                    ).name
                    if name.lower() in names:
                        running.add(pid)
                finally:
                    process.Close()
            except self.api.error:
                continue
        if not running:
            raise RuntimeError("screen_app_not_running")
        result = []

        def collect(hwnd, _):
            if self.processes.GetWindowThreadProcessId(hwnd)[1] not in running:
                return
            if not self.gui.IsWindowVisible(hwnd) or self.gui.IsIconic(hwnd):
                return
            left, top, right, bottom = self.gui.GetWindowRect(hwnd)
            if right - left >= 500 and bottom - top >= 400:
                result.append(
                    Window(
                        hwnd,
                        Rect(left, top, right - left, bottom - top),
                        self.gui.GetWindowText(hwnd),
                    )
                )

        self.gui.EnumWindows(collect, None)
        return result

    def capture(self, window):
        import win32ui
        from PIL import Image

        if self.locked():
            raise RuntimeError("screen_locked")
        dc = self.gui.GetWindowDC(window.handle)
        source = target = bitmap = old = None
        try:
            source = win32ui.CreateDCFromHandle(dc)
            target = source.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            width, height = int(window.frame.w), int(window.frame.h)
            bitmap.CreateCompatibleBitmap(source, width, height)
            old = target.SelectObject(bitmap)
            self.user.PrintWindow.argtypes = [
                self.ctypes.c_void_p,
                self.ctypes.c_void_p,
                self.ctypes.c_uint,
            ]
            # PW_RENDERFULLCONTENT: capture the window's rendered contents despite occlusion.
            if not self.user.PrintWindow(window.handle, target.GetSafeHdc(), 2):
                raise RuntimeError("screen_capture_failed")
            image = Image.frombuffer(
                "RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1
            ).copy()
            if max(image.convert("L").getextrema()) - min(image.convert("L").getextrema()) < 2:
                raise RuntimeError("screen_capture_failed")
            return image
        finally:
            if target and old:
                target.SelectObject(old)
            if bitmap:
                self.gui.DeleteObject(bitmap.GetHandle())
            if target:
                target.DeleteDC()
            if source:
                source.DeleteDC()
            self.gui.ReleaseDC(window.handle, dc)

    def activate(self, window):
        try:
            self.gui.SetForegroundWindow(window.handle)
        except self.api.error:
            raise RuntimeError("screen_accessibility_denied") from None

    def _pointer(self, window, region, pixel, size):
        if self.locked():
            raise RuntimeError("screen_locked")
        # Not screen_accessibility_denied: the reader takes that as "nothing was clicked yet",
        # and this check also runs before scrolling a chat that was just clicked open.
        if not region.contains(pixel) or self.gui.GetForegroundWindow() != window.handle:
            raise RuntimeError("screen_input_refused")
        # Refuse input if the window moved since capture; do not risk clicking another row.
        left, top, right, bottom = self.gui.GetWindowRect(window.handle)
        if (left, top, right - left, bottom - top) != (
            window.frame.x,
            window.frame.y,
            window.frame.w,
            window.frame.h,
        ):
            raise RuntimeError("screen_capture_failed")
        point = tuple(round(v) for v in screen_point(pixel, window.frame, size))
        hit = self.gui.WindowFromPoint(point)
        if self.gui.GetAncestor(hit, 2) != window.handle:  # GA_ROOT
            raise RuntimeError("screen_input_refused")
        self.api.SetCursorPos(point)

    def click_row(self, window, region, pixel, size):
        self._pointer(window, region, pixel, size)
        self.api.mouse_event(self.con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        self.api.mouse_event(self.con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def scroll_chat(self, window, region, size, ticks):
        self._pointer(window, region, region.center, size)
        self.api.mouse_event(self.con.MOUSEEVENTF_WHEEL, 0, 0, ticks * 120, 0)
