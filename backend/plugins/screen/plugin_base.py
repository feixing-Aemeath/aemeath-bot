import sys
from pathlib import Path

from core.plugins import Plugin as BasePlugin
from plugins.screen.helper import HelperScreenCapture, HelperTextRecognizer, JsonHelper
from plugins.screen.reader import ChatScreenReader, name_key
from plugins.screen.session import DESKTOP_HINTS

# The fixed codes in words, for the coverage card the user reads: a row used to say
# 「点开失败 · screen_chat_mismatch」. A code without words here shows as it is.
READING_WORDS = {
    "screen_history_gap": "没接上上次读到的位置",
    "screen_chat_mismatch": "打开的不是这个会话",
    "screen_chat_not_in_list": "会话列表里没找到",
    "screen_chat_ambiguous": "列表里有重名的会话",
    "screen_window_obscured": "窗口被别的窗口挡住了",
    "screen_window_not_found": "没找到聊天窗口",
    "screen_capture_failed": "截图没成功",
    "screen_empty_capture": "截到的是空白",
    "screen_no_messages": "点开后没读到消息",
    "screen_read_timeout": "这一轮读取超时",
    "screen_locked": "屏幕锁着",
    "screen_login_window": "停在登录窗口",
    "screen_session_inactive": "这个用户不在前台",
    "screen_display_asleep": "显示器睡着了",
    "screen_app_not_running": "聊天软件没在运行",
    "screen_recording_denied": "没有屏幕录制权限",
    "screen_accessibility_denied": "没有辅助功能权限",
    "screen_open_daily_limit": "今天的点开次数用完了",
    "screen_open_retry_limit": "今天失败太多次，明天再试",
    "screen_open_backoff": "刚失败过，过一会儿再试",
    "screen_open_not_allowed": "这个会话不允许点开",
}


def reading_words(code):
    return READING_WORDS.get(code, code)


class ScreenPlugin(BasePlugin):
    """Shared by qq_screen and wechat_screen; subclasses set the layout and title."""

    layout = None
    title = ""
    app = ""  # what the helper calls the app: qq, wechat
    app_name = ""

    async def setup(self, context, config):
        validate_config(config)
        config = {
            "self_names": [],
            "open_on_demand": [],
            "on_demand_pages": 2,
            "max_opens_per_chat_per_day": 4,
            **config,
        }
        await super().setup(context, config)
        helper = config.get("helper")
        if not (sys.platform == "win32" and not helper) and (
            not helper or not Path(helper).is_absolute()
        ):
            raise ValueError("screen_helper_requires_calibration")
        # Screenshots stay on this machine, in the plugin's folder (the helper keeps a week).
        shots = context.data_dir / "screens" / context.plugin_id
        json_helper = JsonHelper(Path(helper) if helper else None, app=self.app, out_dir=shots)
        self.reader = ChatScreenReader(
            HelperScreenCapture(json_helper),
            HelperTextRecognizer(json_helper),
            self.layout.with_overrides(config.get("layout", {})),
            context,
            config,
        )

    async def fetch(self):
        return await self.reader.fetch()

    async def commit_fetch(self):
        await self.reader.commit_fetch()

    def request_open(self, chat_name):
        return self.reader.request_open(chat_name)

    async def watch(self):
        """For the watchdog (core/watchdog.py): chats it skips because the list cuts their
        names, chats with messages from a day the reader missed that it may not open, and the
        app left on another page. A failing read shows in its runs."""
        found = []
        computer = "这台电脑" if sys.platform == "win32" else "Mac mini"
        if self.context.get("not_conversation_list"):
            found.append(
                {
                    "key": "not_conversation_list",
                    "title": f"{self.app_name}没停在消息列表",
                    "ok_at": None,
                    "hours": 3,
                    "hint": f"在 {computer} 上的{self.app_name}左侧点回「消息」，预览模式不会替你点。",
                }
            )
        hidden = self.context.get("cut_off_chats") or []
        if self.config.get("preview_only", True) and hidden:
            found.append(
                {
                    "key": "cut_off",
                    "title": f"{self.app_name}有群名被截断，没有读",
                    "ok_at": None,
                    "hours": 3,
                    "hint": f"把{self.app_name}左边的会话栏拉宽，让完整群名显示出来："
                    + "、".join(hidden),
                }
            )
        ambiguous = self.context.get("ambiguous_chats") or []
        if self.config.get("preview_only", True) and ambiguous:
            found.append(
                {
                    "key": "ambiguous",
                    "title": f"{self.app_name}有同名会话，没有读",
                    "ok_at": None,
                    "hours": 0,
                    "hint": "请给会话设置不同的名称，再更新白名单：" + "、".join(ambiguous),
                }
            )
        missed = self.context.get("missed_chats") or []
        if self.config.get("preview_only", True) and missed:
            found.append(
                {
                    "key": "missed",
                    "title": f"{self.app_name}有群的消息没读到",
                    "ok_at": None,
                    "hours": 0,
                    "hint": "读取中断期间这些群来了消息，会话列表上只剩日期："
                    + "、".join(missed)
                    + f"。在{self.app_name}里看一下；想让助手以后自己补读，就在设置里允许按需点开这些群。",
                }
            )
        reading = self.context.get("screen_reading", {})
        incomplete = [
            chat
            for chat, outcome in reading.get("chats", {}).items()
            if outcome.get("state") in {"partial", "failed"} or outcome.get("gap")
        ]
        if incomplete:
            found.append(
                {
                    "key": "incomplete",
                    "title": f"{self.app_name}全文读取有缺口",
                    "ok_at": None,
                    "hours": 3,
                    "hint": "已读内容会保留，请查看插件读取覆盖卡片：" + "、".join(incomplete),
                }
            )
        return found

    async def cards(self):
        preview = self.config.get("preview_only", True)
        cards = []
        if self.context.get("not_conversation_list"):
            cards.append(
                {
                    "type": "text",
                    "title": "读不到会话列表",
                    "data": {
                        "text": f"{self.app_name}现在停在别的页面（比如联系人或群列表）。请在{self.app_name}左侧点回「消息」，"
                        "预览模式不会替你点。"
                    },
                }
            )
        hidden = self.context.get("cut_off_chats") or []
        if preview and hidden:
            cards.append(
                {
                    "type": "list",
                    "title": "群名被截断，没有读",
                    "data": {
                        "items": [
                            {
                                "title": name,
                                "subtitle": f"把{self.app_name}左边的会话栏拉宽，让完整群名显示出来",
                            }
                            for name in hidden
                        ]
                    },
                }
            )
        ambiguous = self.context.get("ambiguous_chats") or []
        if preview and ambiguous:
            cards.append(
                {
                    "type": "text",
                    "title": "同名会话，没有读",
                    "data": {
                        "text": "请给会话设置不同的名称，再更新白名单：" + "、".join(ambiguous)
                    },
                }
            )
        reading = self.context.get("screen_reading", {})
        if reading:
            outcomes = reading.get("chats", {})
            full = sum(o.get("state") == "full" for o in outcomes.values())
            partial = sum(o.get("state") == "partial" for o in outcomes.values())
            failed = sum(o.get("state") == "failed" for o in outcomes.values())
            previews = sum(
                o.get("preview", False) and o.get("state") not in {"full", "partial"}
                for o in outcomes.values()
            )
            missing = sum(o.get("state") == "missing" for o in outcomes.values())
            current_day = self.reader._now().date().isoformat()
            fresh = reading.get("finished_at", "").startswith(current_day)
            complete = (
                bool(outcomes)
                and full == len(outcomes)
                and reading.get("committed")
                and not reading.get("error")
                and not any(o.get("cleanup_error") or o.get("gap") for o in outcomes.values())
            )
            title = "本轮授权范围已读全" if complete and fresh else "全文未读全"
            stamp = reading.get("finished_at", "未知")
            text = (
                f"最近一轮 {stamp}：全文 {full} 个，部分全文 {partial} 个，"
                f"点开失败 {failed} 个，仅有预览 {previews} 个，未读到 {missing} 个。"
            )
            if not fresh:
                text += " 今天还没有读取结果。"
            if not reading.get("committed"):
                text += " 本轮尚未确认入库。"
            if reading.get("error"):
                text += " " + DESKTOP_HINTS.get(reading["error"], reading_words(reading["error"]))
            cards.append({"type": "text", "title": title, "data": {"text": text}})
            labels = {
                "full": "全文范围已覆盖",
                "partial": "部分全文，读取有缺口",
                "failed": "点开失败",
                "preview": "只看到预览",
                "missing": "未读到",
            }
            cards.append(
                {
                    "type": "list",
                    "title": "会话读取覆盖",
                    "data": {
                        "items": [
                            {
                                "title": chat,
                                "subtitle": labels.get(o.get("state"), "未读到")
                                + (" · " + reading_words(o["error"]) if o.get("error") else "")
                                + (
                                    " · 尚有缺口：" + reading_words(o["gap"])
                                    if o.get("gap")
                                    else ""
                                )
                                + (
                                    " · 恢复列表失败：" + reading_words(o["cleanup_error"])
                                    if o.get("cleanup_error")
                                    else ""
                                ),
                            }
                            for chat, o in outcomes.items()
                        ]
                    },
                }
            )
        cards.append(
            {
                "type": "text",
                "title": self.title,
                "data": {
                    "text": (
                        "仅预览模式：只读会话列表；已授权的群可按需点开，群和手机红点会消失"
                        if self.config.get("open_on_demand")
                        else "仅预览模式：只读会话列表里今天的最新一条，不点开聊天"
                    )
                    if preview
                    else "读取白名单聊天"
                },
            }
        )
        return cards


def validate_config(config):
    whitelist = set(config.get("whitelist", []))
    if not set(config.get("open_on_demand", [])) <= whitelist:
        raise ValueError("screen_open_not_whitelisted")
    if config.get("park_chat") and config["park_chat"] not in whitelist:
        raise ValueError("screen_park_not_whitelisted")
    if (
        config.get("preview_only", True)
        and config.get("park_chat")
        and config["park_chat"] not in config.get("open_on_demand", [])
    ):
        raise ValueError("screen_park_not_allowed")
    keys = [name_key(chat) for chat in config.get("whitelist", [])]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("screen_whitelist_ambiguous")
