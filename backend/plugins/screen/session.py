"""Content-free desktop availability. Unknown probe results never mean 'locked'."""

import ctypes
import os
import plistlib
import sys
from dataclasses import asdict, dataclass

IOREG = "/usr/sbin/ioreg"
CONSOLE_COMMAND = [IOREG, "-n", "Root", "-d1", "-a"]
DESKTOP_ERRORS = {
    "screen_locked",
    "screen_login_window",
    "screen_session_inactive",
    "screen_display_asleep",
}
DESKTOP_HINTS = {
    "screen_locked": "当前桌面会话已锁定，请在 Mac 上解锁；亮屏不等于解锁。",
    "screen_login_window": "当前停在登录窗口，请登录运行后端的用户并进入桌面。",
    "screen_session_inactive": "运行后端的用户不在前台控制台，请切回该用户的桌面；远程桌面断开后也需核对。",
    "screen_display_asleep": "所有在线显示器都在睡眠，请唤醒显示器并检查显示器睡眠设置；无显示器时检查 HDMI 欺骗器。",
}


def boolean(value):
    # Do not interpret an unreadable string such as 'false' as True.
    return bool(value) if type(value) in (bool, int) and value in (0, 1) else None


def console_state(output, uid=None):
    """Only the current user's console state, never a stale lock from another user.

    Keys are defined in Apple's xnu/iokit/IOKit/IOKitKeysPrivate.h. Raw dictionaries
    (which contain user names and IDs) must not be logged or returned in cards.
    """
    uid = os.getuid() if uid is None else uid
    try:
        root = plistlib.loads(output)
    except (ValueError, TypeError, plistlib.InvalidFileException):
        return DesktopState()
    if isinstance(root, list):
        root = root[0] if root else {}
    if not isinstance(root, dict):
        return DesktopState()
    users = root.get("IOConsoleUsers")
    if users == []:
        return DesktopState(login_done=False, on_console=False)
    users = [u for u in users if isinstance(u, dict)] if isinstance(users, list) else []
    ours = [u for u in users if u.get("kCGSSessionUserIDKey") == uid]
    own = next(
        (u for u in ours if boolean(u.get("kCGSSessionOnConsoleKey")) is True),
        ours[0] if ours else None,
    )
    active = next((u for u in users if boolean(u.get("kCGSSessionOnConsoleKey")) is True), None)
    # An explicit unfinished foreground login is distinct from switching to another user.
    if active and boolean(active.get("kCGSessionLoginDoneKey")) is False:
        return DesktopState(login_done=False, on_console=False)
    if own is not None:
        on_console = boolean(own.get("kCGSSessionOnConsoleKey"))
        locked = boolean(own.get("CGSSessionScreenIsLocked"))
        if on_console is True and boolean(root.get("IOConsoleLocked")) is True:
            locked = True
        return DesktopState(locked, boolean(own.get("kCGSessionLoginDoneKey")), on_console)
    if any(type(u.get("kCGSSessionUserIDKey")) is int for u in users):
        return DesktopState(on_console=False)
    # Older/incomplete registries may omit UIDs. Only an explicitly active session, or
    # a single session with no console flag, can supply its lock flag.
    candidate = active or (users[0] if len(users) == 1 else {})
    locked = boolean(root.get("IOConsoleLocked"))
    if locked is None and boolean(candidate.get("kCGSSessionOnConsoleKey")) is not False:
        locked = boolean(candidate.get("CGSSessionScreenIsLocked"))
    return DesktopState(locked=locked)


@dataclass(frozen=True)
class DesktopState:
    locked: bool | None = None
    login_done: bool | None = None
    on_console: bool | None = None
    display_asleep: bool | None = None

    @property
    def code(self):
        if self.login_done is False:
            return "screen_login_window"
        if self.on_console is False:
            return "screen_session_inactive"
        if self.locked is True:
            return "screen_locked"
        if self.display_asleep is True:
            return "screen_display_asleep"
        return None

    def details(self):
        return asdict(self)


def displays_asleep():
    """Public CoreGraphics API; no capture, input events, permission request or helper.

    None means unavailable (including no online display). One awake display is sufficient.
    This also works on Apple silicon, where IODisplayWrangler may not exist.
    """
    if sys.platform != "darwin":
        return None
    try:
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGGetOnlineDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        cg.CGGetOnlineDisplayList.restype = ctypes.c_int32
        cg.CGDisplayIsAsleep.argtypes = [ctypes.c_uint32]
        cg.CGDisplayIsAsleep.restype = ctypes.c_uint32
        displays = (ctypes.c_uint32 * 32)()
        count = ctypes.c_uint32()
        if cg.CGGetOnlineDisplayList(32, displays, ctypes.byref(count)) or not count.value:
            return None
        return all(cg.CGDisplayIsAsleep(d) for d in displays[: count.value])
    except (OSError, AttributeError):
        return None
