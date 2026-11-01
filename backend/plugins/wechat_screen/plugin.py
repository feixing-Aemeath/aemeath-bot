from plugins.screen.layout import WECHAT_LAYOUT
from plugins.screen.plugin_base import ScreenPlugin


class Plugin(ScreenPlugin):
    layout = WECHAT_LAYOUT
    app = "wechat"
    app_name = "微信"
    title = "微信屏幕读取"
