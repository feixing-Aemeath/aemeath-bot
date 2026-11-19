"""Local source labels and actionable uncertainty; no manifest needed for shared text."""

import hashlib
import re

from core.digests import latest_date
from plugins.screen.plugin_base import ScreenPlugin
from plugins.screen.reader import whitelisted

SCREEN_SOURCES = ("qq_screen", "wechat_screen")
SOURCE_NAMES = {"share": "转给助手", "app": "手动新建"}


def screen_instance(service, identifier):
    instance = service.plugins.instances.get(identifier)
    if identifier not in SCREEN_SOURCES or not isinstance(instance, ScreenPlugin):
        raise KeyError("screen_plugin_not_found")
    return instance


def unsure_items(service, day, digests, now=None):
    """「还不确定」 items of the day's digests; with `now`, none whose dates have all gone by."""
    plugins = [p for p in service.plugins.list() if p["id"] in SCREEN_SOURCES]
    items = []
    for digest in digests:
        active = False
        for raw in digest["summary"].splitlines():
            line = raw.strip()
            if re.fullmatch(r"\*\*.+?\*\*", line):
                active = line == "**还不确定**"
            elif line.startswith("#"):
                active = False
            elif active and line.startswith("- "):
                text = line[2:].strip()
                if now is not None and (when := latest_date(text, now)) is not None and when < now:
                    continue
                identifier = "u-" + hashlib.sha256(f"{day}:{text}".encode()).hexdigest()
                name = re.split("[：:]", text, maxsplit=1)[0].strip()
                matches = [
                    (p["id"], chat)
                    for p in plugins
                    if (chat := whitelisted(name, p["config"].get("whitelist", [])))
                ]
                own = [match for match in matches if match[0] == digest["plugin"]]
                matches = own or matches
                item = {
                    "id": identifier,
                    "text": text,
                    "plugin": None,
                    "chat_name": None,
                    "can_open": False,
                }
                if len(matches) == 1:
                    plugin, chat = matches[0]
                    item.update(plugin=plugin, chat_name=chat)
                    instance = service.plugins.instances.get(plugin)
                    if isinstance(instance, ScreenPlugin):
                        item["can_open"] = instance.reader.can_open(chat) is None
                items.append(item)
    return items
