import sys
from pathlib import Path

from core.plugins import Plugin as BasePlugin
from plugins.screen.helper import HelperScreenCapture, HelperTextRecognizer, JsonHelper
from plugins.screen.reader import ChatScreenReader


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
            context.store.settings.timezone,
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
