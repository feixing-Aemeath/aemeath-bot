import os
import re
import tempfile
import threading
from pathlib import Path, PurePosixPath

from core.db import Database
from core.documents import SUPPORTED, Documents
from core.knowledge_access import KnowledgeAccess, safe_path
from core.platforms import is_link


class Memory:
    def __init__(self, db: Database, root: Path, documents=None):
        self.db = db
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.access = KnowledgeAccess(db, self.root)
        self.documents = documents or Documents(db.path.parent / "knowledge_text")
        self.knowledge = None
        self._index_lock = threading.Lock()
        self._index_version, self._indexed_at = 0, {}

    def path(self, relative: str) -> Path:
        target = safe_path(self.root, relative)
        if target.suffix.lower() not in SUPPORTED:
            raise ValueError("unsupported memory format")
        return target

    def read(self, relative):
        path = self.path(relative)
        canonical = path.relative_to(self.root).as_posix()
        self.access.require_read(canonical)
        if not path.is_file():
            raise FileNotFoundError(relative)
        result = self.documents.read(path)
        if result["error"]:
            raise ValueError(result["error"])
        content = result["content"]
        if result["truncated"]:
            content += "\n（提取文字已截断：上限 2,000,000 字节。）"
        response = {"path": canonical, "content": content}
        if result["extracted"]:
            response |= {"extracted": True, "truncated": result["truncated"]}
        return response

    def write(self, relative, content):
        if len(content.encode()) > 2_000_000:
            raise ValueError("memory file too large")
        path = self.path(relative)
        requested = PurePosixPath(relative.replace("\\", "/"))
        for parts in (requested.parts, path.relative_to(self.root).parts):
            parts = tuple(part.casefold() for part in parts)
            if parts[0] == "知识库" or (
                len(parts) == 3 and parts[0] == "bots" and parts[2] == "soul.md"
            ):
                raise ValueError("人设和知识库只读，请由用户在本机修改")
        if path.suffix.lower() != ".md":
            raise ValueError("memory writes must be markdown files")
        self.access.require_write(path.relative_to(self.root).as_posix())
        path.parent.mkdir(parents=True, exist_ok=True)
        self.save(path, content)
        return {"path": path.relative_to(self.root).as_posix()}

    def save(self, path, content):
        """Trusted writer shared with knowledge notes; callers enforce write policy first."""
        temp = path.with_suffix(".md.tmp")
        if is_link(temp):
            raise ValueError("invalid temporary path")
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                # Preserve both LF and CRLF on Windows, including the note's hash.
                newline="",
                dir=path.parent,
                prefix=".memory-",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp = Path(output.name)
                output.write(content)
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        self.index_one(path)

    def index_one(self, path):
        relative = path.relative_to(self.root).as_posix()
        safe_path(self.root, relative)
        result = self.documents.read(path)
        with self._index_lock:
            with self.db.connect() as conn:
                conn.execute("DELETE FROM memory_fts WHERE path=?", (relative,))
                if not result["error"]:
                    conn.execute(
                        "INSERT INTO memory_fts VALUES(?,?)", (relative, result["content"])
                    )
            self._index_version += 1
            self._indexed_at[relative] = self._index_version

    def files(self):
        # os.walk without following links; reject linked ancestors as well as leaf links.
        # Hidden names are Finder's .DS_Store, Obsidian's .obsidian and our temporary files.
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [
                d for d in dirs if not d.startswith(".") and not is_link(Path(directory) / d)
            ]
            for name in files:
                path = Path(directory) / name
                if not name.startswith(".") and not is_link(path) and path.is_file():
                    yield path

    def reindex(self):
        with self._index_lock:
            version = self._index_version
        self.access.discover()
        records = []
        for path in self.files():
            try:
                if path.suffix.lower() not in SUPPORTED:
                    continue
                result = self.documents.read(path)
                if not result["error"]:
                    records.append((path.relative_to(self.root).as_posix(), result["content"]))
            except (OSError, UnicodeError, ValueError):
                continue
        with self._index_lock, self.db.connect() as conn:
            # Preserve writes indexed while this filesystem snapshot was being collected.
            changed = {p for p, revision in self._indexed_at.items() if revision > version}
            records = [record for record in records if record[0] not in changed]
            records.extend(
                (row["path"], row["content"])
                for row in conn.execute("SELECT path,content FROM memory_fts")
                if row["path"] in changed
            )
            conn.execute("DELETE FROM memory_fts")
            conn.executemany("INSERT INTO memory_fts VALUES(?,?)", records)
        if self.knowledge:
            self.knowledge.sync()
        return len(records)

    def search(self, query, limit=10):
        query = query.strip()
        if not query or len(query) > 500 or not 1 <= limit <= 50:
            raise ValueError("invalid memory search")
        scope, args = self.access.sql_filter()
        if len(query) < 3:
            escaped = re.sub(r"([%_\\])", r"\\\1", query)
            sql = (
                "SELECT path,substr(content,1,400) AS excerpt FROM memory_fts "
                f"WHERE content LIKE ? ESCAPE '\\' AND {scope} ORDER BY rowid LIMIT ? OFFSET ?"
            )
            parameters = (f"%{escaped}%", *args)
        else:
            phrase = '"' + query.replace('"', '""') + '"'
            sql = (
                "SELECT path,snippet(memory_fts,1,'','', '…',40) AS excerpt "
                f"FROM memory_fts WHERE memory_fts MATCH ? AND {scope} "
                "ORDER BY rank,rowid LIMIT ? OFFSET ?"
            )
            parameters = (phrase, *args)
        results, offset = [], 0
        while len(results) < limit:
            rows = self.db.rows(sql, (*parameters, limit, offset))
            for row in rows:
                try:
                    if self.path(row["path"]).is_file():
                        results.append(row)
                        if len(results) == limit:
                            break
                except ValueError:
                    continue
            if len(rows) < limit:
                break
            offset += len(rows)
        return results
