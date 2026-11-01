"""Read-only Windows checks, using doctor.SystemProbe so all decisions are testable."""

import json
import re
import sys

SETTINGS = r"""
$ErrorActionPreference = 'Stop'
$desktop = Get-ItemProperty 'HKCU:\Control Panel\Desktop'
$policy = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -ErrorAction SilentlyContinue
$login = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name AutoAdminLogon -ErrorAction SilentlyContinue
$dynamic = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name EnableGoodbye -ErrorAction SilentlyContinue
@{
  screen_saver_active = $desktop.ScreenSaveActive
  screen_saver_secure = $desktop.ScreenSaverIsSecure
  inactivity_seconds = $policy.InactivityTimeoutSecs
  dynamic_lock = $dynamic.EnableGoodbye
  auto_login = $login.AutoAdminLogon
  processes = @(Get-Process -Name QQ,WeChat,Weixin -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ProcessName)
} | ConvertTo-Json -Compress
"""
TASK = r"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'local.assistant.windows'
@{state = [string]$task.State; interactive = [string]$task.Principal.LogonType;
  restart_count = $task.Settings.RestartCount; working_directory = $task.Actions[0].WorkingDirectory;
  restart_interval = $task.Settings.RestartInterval; execution_time_limit = $task.Settings.ExecutionTimeLimit;
  user = $task.Principal.UserId} | ConvertTo-Json -Compress
"""


def powershell(script):
    utf8 = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false);\n"
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", utf8 + script]


def json_result(result):
    try:
        value = json.loads(result.out) if result and result.code == 0 else None
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def power_timeouts(text):
    # Powercfg labels are localized, but AC/DC values always end in hexadecimal indices.
    values = re.findall(r"0x([0-9a-fA-F]{8})", text)
    return tuple(int(v, 16) for v in values[-2:]) if len(values) >= 2 else None


def check_windows(report, backend_dir):
    if report.probe.platform != "win32":
        return
    probe = report.probe
    report.group = "Windows 系统设置"
    for name, title, group, option, change in (
        ("windows_sleep", "系统不睡眠", "SUB_SLEEP", "STANDBYIDLE", "standby"),
        ("windows_display", "显示器不关闭", "SUB_VIDEO", "VIDEOIDLE", "monitor"),
    ):
        result = probe.run(["powercfg.exe", "/query", "SCHEME_CURRENT", group, option])
        values = power_timeouts(result.out) if result and result.code == 0 else None
        report.add(
            name,
            title,
            "manual" if values is None else "ok" if values == (0, 0) else "fail",
            f"交流电/电池超时秒数：{values}" if values else "无法读取当前电源计划",
            f"powercfg /change {change}-timeout-ac 0；powercfg /change {change}-timeout-dc 0",
        )
    state = json_result(probe.run(powershell(SETTINGS)))
    locking = (
        str(state.get("screen_saver_active")) == "1"
        and str(state.get("screen_saver_secure")) == "1"
        or int(state.get("inactivity_seconds") or 0) > 0
        or int(state.get("dynamic_lock") or 0) > 0
    )
    report.add(
        "windows_lock",
        "不自动锁屏",
        "fail" if locking else "manual",
        "检测到屏保、动态锁或不活动锁屏策略"
        if locking
        else "未确认所有登录/域策略；请实测空闲后是否锁屏",
        "设置 → 账户 → 登录选项：关闭动态锁；屏保取消恢复时显示登录屏幕；检查组织策略",
    )
    report.add(
        "windows_auto_login",
        "自动登录建议",
        "manual",
        "已配置自动登录，请实测重启"
        if str(state.get("auto_login")) == "1"
        else "尚未确认自动登录；任务在用户登录后才能读屏",
        "可使用 Microsoft Sysinternals Autologon；自行决定是否启用，不把登录密码写进脚本",
    )
    processes = {str(name).lower() for name in state.get("processes", [])}
    for key, names, title, optional in (
        ("qq_login", {"qq"}, "QQ 保持登录", False),
        ("wechat_login", {"wechat", "weixin"}, "微信保持登录", True),
    ):
        running = bool(names & processes)
        report.add(
            key,
            title,
            "manual" if running else "fail",
            "正在运行；登录状态请看窗口" if running else "未检测到进程",
            "启动客户端并登录，让窗口停在消息列表",
            optional=optional,
        )
    ocr = json_result(probe.run([sys.executable, "-X", "utf8", "-m", "scripts.windows_probe"]))
    chinese = any(
        tag.lower() in {"zh-hans", "zh-hans-cn", "zh-cn"} for tag in ocr.get("languages", [])
    )
    report.add(
        "windows_ocr",
        "Windows OCR 简体中文语言包",
        "ok" if chinese else "fail",
        "已安装 zh-Hans" if chinese else "OCR 不可用或缺少简体中文识别语言包",
        "设置 → 时间和语言 → 语言和区域 → 中文(简体) → 语言选项，安装光学字符识别；uv sync --locked",
    )
    report.add(
        "windows_desktop",
        "交互式桌面未锁定",
        "ok" if ocr.get("locked") is False else "fail",
        "可访问当前桌面" if ocr.get("locked") is False else "screen_locked：解锁并保持用户会话连接",
        "不要在 SYSTEM/Session 0 运行；RDP 断开后的截图能力须实测",
    )
    task = json_result(probe.run(powershell(TASK)))
    healthy = (
        task.get("state") == "Running"
        and task.get("interactive") == "Interactive"
        and int(task.get("restart_count") or 0) > 0
        and str(task.get("working_directory", "")).casefold() == str(backend_dir).casefold()
    )
    report.add(
        "windows_task",
        "任务计划程序常驻",
        "ok" if healthy else "fail",
        "交互式任务运行中，已配置崩溃重启"
        if healthy
        else "任务未运行，或交互登录/重启/工作目录不匹配",
        ".\\scripts\\install_windows.ps1 install -Start；用 status 查看状态",
    )
