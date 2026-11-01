"""Characters share a soul and notes across plugins with the same mascot sticker."""

CHARACTERS = {
    "aemeath": "助理",
    "red_ribbon": "成绩",
    "luosela": "校园",
    "moning": "作业",
    "shouanren": "用量",
    "lily_veil": "健康",
    "orange_daisy": "知识库",
    "carlotta": "记账",
}
# Bots of the App itself rather than of a plugin, by bot_id: their sticker. 知识库 keeps the
# knowledge base; GoodNotes, class photos and videos join her through the same sticker.
BUILT_IN = {"assistant": "aemeath", "knowledge": "orange_daisy"}
SOUL_LIMIT = 8000
MEMORY_LIMIT = 6000


def character(bot_id, manifest=None):
    if bot_id in BUILT_IN:
        return CHARACTERS[BUILT_IN[bot_id]]
    if not manifest or "source" in manifest["provides"]:
        return None
    return CHARACTERS.get((manifest.get("mascot") or {}).get("sticker"))


def excerpt(memory, relative, limit, missing):
    try:
        with memory.path(relative).open(encoding="utf-8") as source:
            content = source.read(limit + 1)
    except FileNotFoundError:
        return missing
    except (OSError, UnicodeError, ValueError):
        return "（暂时无法读取，请告诉用户检查这个文件。）"
    if len(content) > limit:
        return content[:limit] + f"\n（内容已截断，只展示开头 {limit} 字符。）"
    return content or missing


def context(memory, name):
    if name is None:
        return {
            "character": "（还没有对应的角色）",
            "soul_path": "（还没有人设路径）",
            "memory_path": "（还没有记忆路径，不要自行创建角色目录）",
            "soul": "（还没有人设。）",
            "memories": "（还没有角色记忆。）",
        }
    soul_path, memory_path = f"Bots/{name}/soul.md", f"Bots/{name}/记忆.md"
    return {
        "character": name,
        "soul_path": soul_path,
        "memory_path": memory_path,
        "soul": excerpt(memory, soul_path, SOUL_LIMIT, "（还没有人设。）"),
        "memories": excerpt(memory, memory_path, MEMORY_LIMIT, "（还没有角色记忆。）"),
    }
