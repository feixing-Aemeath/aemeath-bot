from plugins.screen.layout import QQ_LAYOUT
from plugins.screen.plugin_base import ScreenPlugin


class Plugin(ScreenPlugin):
    layout = QQ_LAYOUT
    app = "qq"
    app_name = "QQ"
    title = "QQ 屏幕读取"
