"""Persisted folder readers; every MCP memory operation enforces the current Grant."""

import json
import os
import unicodedata
from pathlib import PurePosixPath

from core.bots import CHARACTERS
from core.db import dump
from core.platforms import is_link, windows_component
from core.tokens import current_grant

BOTS = list(CHARACTERS.values())
DEFAULTS = {"agents": ["Claude Code", "Codex"], "default_readers": ["助理", "知识库"]}


def safe_path(root, relative):
    parts = PurePosixPath(relative.replace("\\", "/"))
    if parts.is_absolute() or ".." in parts.parts or ":" in relative or "\x00" in relative:
        raise ValueError("invalid vault path")
    target = root
    for part in parts.parts:
        if os.name == "nt" and not windows_component(part):
            raise ValueError("invalid vault path")
        target = target / part
        if is_link(target):
            raise ValueError("符号链接只读且不能通过知识库接口访问")
    if not target.resolve().is_relative_to(root):
        raise ValueError("invalid vault path")
    return target


class KnowledgeAccess:
    def __init__(self, db, root):
        self.db, self.root = db, root

    @property
    def settings(self):
        return DEFAULTS | self.db.get("knowledge_settings", {})

    @property
    def readers(self):
        return [
            {"id": name, "kind": "bot", "sticker": sticker} for sticker, name in CHARACTERS.items()
        ] + [{"id": name, "kind": "agent", "sticker": None} for name in self.settings["agents"]]

    def validate(self, readers, agents=None):
        known = BOTS + (self.settings["agents"] if agents is None else agents)
        if any(r not in known for r in readers):
            raise ValueError("未知读者")
        return [r for r in known if r in readers]

    def discover(self):
        areas = []
        for folder in sorted(self.root.iterdir(), key=lambda p: p.name):
            if folder.name.startswith(".") or is_link(folder) or not folder.is_dir():
                continue
            if folder.name == "知识库":
                areas.extend(
                    {"path": f"知识库/{topic.name}", "name": topic.name, "kind": "topic"}
                    for topic in sorted(folder.iterdir(), key=lambda p: p.name)
                    if topic.is_dir() and not is_link(topic) and not topic.name.startswith(".")
                )
            else:
                areas.append({"path": folder.name, "name": folder.name, "kind": "system"})
        areas.append({"path": ".", "name": "根目录文件", "kind": "system"})
        for area in areas:
            default = self.settings["default_readers"] if area["kind"] == "topic" else BOTS
            self.db.execute(
                "INSERT OR IGNORE INTO knowledge_areas VALUES(?,?)", (area["path"], dump(default))
            )
        stored = {
            r["path"]: json.loads(r["readers"])
            for r in self.db.rows("SELECT * FROM knowledge_areas")
        }
        for area in areas:
            area["locked"] = BOTS.copy() if area["kind"] == "system" else []
            area["readers"] = self.validate(stored[area["path"]] + area["locked"])
        return sorted(areas, key=lambda a: (a["kind"] != "topic", a["name"]))

    def patch(self, default_readers=None, agents=None):
        # Freeze newly discovered folders BEFORE changing defaults.
        self.discover()
        settings = self.settings
        if agents is not None:
            if len(agents) != len(set(agents)):
                raise ValueError("Agent 名称重复")
            for name in agents:
                if (
                    not 1 <= len(name) <= 40
                    or name.strip() != name
                    or name in BOTS
                    or any(unicodedata.category(c).startswith("C") for c in name)
                ):
                    raise ValueError("Agent 名称无效")
            settings["agents"] = agents
        if default_readers is not None:
            settings["default_readers"] = self.validate(default_readers, settings["agents"])
        else:
            settings["default_readers"] = [
                r for r in settings["default_readers"] if r in BOTS + settings["agents"]
            ]
        with self.db.connect() as conn:
            for row in conn.execute("SELECT * FROM knowledge_areas").fetchall():
                readers = [r for r in json.loads(row["readers"]) if r in BOTS + settings["agents"]]
                conn.execute(
                    "UPDATE knowledge_areas SET readers=? WHERE path=?",
                    (dump(readers), row["path"]),
                )
            conn.execute(
                "INSERT OR REPLACE INTO kv VALUES(?,?)", ("knowledge_settings", dump(settings))
            )

    def set_readers(self, path, readers):
        areas = {a["path"]: a for a in self.discover()}
        if path not in areas:
            raise KeyError(path)
        readers = self.validate(readers + areas[path]["locked"])
        self.db.execute("UPDATE knowledge_areas SET readers=? WHERE path=?", (dump(readers), path))

    @staticmethod
    def area_of(relative):
        parts = PurePosixPath(relative).parts
        if len(parts) <= 1:
            return "."
        if parts[0] == "知识库":
            return "/".join(parts[:2]) if len(parts) >= 3 else None
        return parts[0]

    def allowed(self, grant=None):
        grant = grant or current_grant.get()
        if grant.kind is None and grant.reader is None:
            return None  # MAIN and legacy unscoped device grants
        areas = self.discover()
        if grant.kind == "organizer":
            return [a["path"] for a in areas if a["path"] == grant.area and a["readers"]]
        if grant.kind == "system":
            return [a["path"] for a in areas if a["kind"] == "system"]
        if grant.kind == "agent" and grant.reader not in self.settings["agents"]:
            return []
        return [a["path"] for a in areas if grant.reader in a["readers"]]

    def require_read(self, relative, grant=None):
        allowed = self.allowed(grant)
        if allowed is not None and self.area_of(relative) not in allowed:
            raise ValueError("没有读取这个文件夹的权限")

    def require_write(self, relative, grant=None):
        grant = grant or current_grant.get()
        parts = PurePosixPath(relative).parts
        if grant.kind == "bot":
            if (len(parts) >= 3 and parts[0] == "Bots" and parts[1] == grant.reader) or parts[
                0
            ] == "每日":
                return
            raise ValueError("只能写自己的角色记忆和每日笔记")
        if grant.kind in {"system", "organizer", "finance_report"}:
            raise ValueError("此运行不能使用 memory_write")
        if grant.reader is not None:
            self.require_read(relative, grant)
            if parts[0].casefold() in {"bots", "知识库"}:
                raise ValueError("不能写 Bots 或知识库")
            if self.area_of(relative) not in [
                a["path"] for a in self.discover() if a["kind"] == "system"
            ]:
                raise ValueError("只能写可读的系统区")

    def sql_filter(self, grant=None):
        allowed = self.allowed(grant)
        if allowed is None:
            return "1", []
        clauses, args = [], []
        for area in allowed:
            if area == ".":
                clauses.append("instr(path,'/')=0")
            else:
                clauses.append("substr(path,1,?)=?")
                args.extend([len(area) + 1, area + "/"])
        return "(" + " OR ".join(clauses) + ")" if clauses else "0", args
