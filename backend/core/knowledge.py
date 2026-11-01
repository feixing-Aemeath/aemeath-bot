"""User knowledge library, source receipts and topic-scoped nightly organization."""

import asyncio
import json
import mimetypes
import os
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath

import yaml

from core.db import dump, now
from core.documents import IMAGES, MAX_FILE, SUPPORTED, file_limit, sha256
from core.knowledge_access import safe_path
from core.platforms import is_link
from core.prompts import render
from core.store import ConflictError
from core.tokens import MAIN, Grant, current_grant

# Agent runs per topic in one organize job; each takes up to 8 sources.
BATCHES = 6
ANNOTATIONS = re.compile(r"^## 我的批注[ \t]*\r?$", re.MULTILINE)


def frontmatter(text):
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    if match:
        try:
            meta = yaml.safe_load(match[1])
            return (meta if isinstance(meta, dict) else {}), text[match.end() :]
        except yaml.YAMLError:
            pass
    return {}, text


def visible(path):
    """A plain file the user put there: not hidden (.DS_Store) and not a link."""
    return not path.name.startswith(".") and path.is_file() and not is_link(path)


def file_format(path):
    suffix = path.suffix.lower()
    return "image" if suffix in IMAGES else suffix[1:] if suffix in SUPPORTED else "other"


class Knowledge:
    def __init__(self, service):
        self.service, self.memory, self.db = service, service.memory, service.db
        self.access = self.memory.access
        # Run-local write receipts, never supplied by the model.
        self.receipts = {}
        # path -> ((size, mtime_ns), sha256): the App's every request syncs, and hashing every
        # PDF each time would hold the server up.
        self.hashes = {}

    def path(self, relative, exists=True):
        path = safe_path(self.memory.root, relative)
        if exists and not path.exists():
            raise KeyError(relative)
        return path

    def relative(self, path):
        return path.relative_to(self.memory.root).as_posix()

    def is_note(self, relative):
        parts = PurePosixPath(relative).parts
        return (
            len(parts) >= 4
            and parts[0] == "知识库"
            and parts[2] == "整理"
            and parts[-1].lower().endswith(".md")
        )

    def fingerprint(self, path, relative):
        stat = path.stat()
        key = (stat.st_size, stat.st_mtime_ns)
        cached = self.hashes.get(relative)
        if cached and cached[0] == key:
            return cached[1]
        value = sha256(path)
        self.hashes[relative] = (key, value)
        return value

    def source(self, relative):
        rows = self.db.rows("SELECT * FROM knowledge_sources WHERE path=?", (relative,))
        return rows[0] if rows else None

    def sync(self):
        """Discover changed sources without invoking a model; hash is the change identity."""
        areas = {a["path"]: a for a in self.access.discover()}
        seen = set()
        for path in self.memory.files():
            relative = self.relative(path)
            if not relative.startswith("知识库/") or self.is_note(relative):
                continue
            seen.add(relative)
            try:
                # Oversized files are never read or hashed.
                large = path.stat().st_size > file_limit(path)
                fingerprint = "oversize" if large else self.fingerprint(path, relative)
                old = self.source(relative)
                text = self.memory.documents.read(path, fingerprint, extract=False)
                area = areas.get(self.access.area_of(relative))
                status = "unreadable" if text["error"] else "pending"
                error = text["error"]
                if area and not area["readers"]:
                    status, error = "skipped", None
                if old and old["sha256"] == fingerprint:
                    if status == "pending":
                        status = "organized" if old["organized_at"] is not None else "pending"
                        error = old["error"] if old["status"] == "pending" else None
                    # ACL changes do not erase provenance or long-document progress.
                    self.db.execute(
                        "UPDATE knowledge_sources SET status=?,error=? WHERE path=?",
                        (status, error, relative),
                    )
                    continue
                self.db.execute(
                    "INSERT INTO knowledge_sources(path,sha256,status,error) VALUES(?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,status=excluded.status,"
                    "error=excluded.error,organized_at=NULL,notes='[]',organize_offset=0",
                    (relative, fingerprint, status, error),
                )
            except OSError:
                self.db.execute(
                    "INSERT INTO knowledge_sources(path,sha256,status,error) VALUES(?,?,'unreadable',?) "
                    "ON CONFLICT(path) DO UPDATE SET status='unreadable',error=excluded.error",
                    (relative, "unreadable", "无法读取文件"),
                )
        for row in self.db.rows("SELECT path FROM knowledge_sources"):
            if row["path"] not in seen:
                self.db.execute("DELETE FROM knowledge_sources WHERE path=?", (row["path"],))

    def metadata(self, path):
        if path.suffix.lower() != ".md" or path.stat().st_size > 2_000_000:
            return {}, ""
        try:
            return frontmatter(path.read_bytes().decode("utf-8"))
        except UnicodeError:
            return {}, ""

    def entry(self, path):
        relative = self.relative(path)
        area = self.access.area_of(relative)
        topic = area and area.startswith("知识库/")
        kind = (
            "note"
            if self.is_note(relative)
            else "source"
            if topic or relative.startswith("知识库/")
            else "file"
        )
        meta, body = self.metadata(path)
        heading = re.search(r"^# (.+)$", body, re.MULTILINE)
        title = meta.get("title") or (heading[1] if heading else path.stem)
        source = self.source(relative) if kind == "source" else None
        return {
            "path": relative,
            "name": path.name,
            "title": str(title),
            "kind": kind,
            "format": file_format(path),
            "size": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            "status": source["status"] if source else ("pending" if kind == "source" else None),
            "error": source["error"] if source else None,
        }

    def area(self, area):
        entries = []
        for path in self.memory.files():
            if self.access.area_of(self.relative(path)) == area["path"]:
                entries.append(self.entry(path))
        topic = area["kind"] == "topic"
        return area | {
            "notes": sum(e["kind"] == "note" for e in entries) if topic else 0,
            "sources": sum(e["kind"] == "source" for e in entries) if topic else 0,
            "files": len(entries),
            "pending": sum(e["status"] == "pending" for e in entries) if topic else 0,
            "failed": sum(e["status"] in {"failed", "unreadable"} for e in entries) if topic else 0,
            "updated_at": max((e["modified_at"] for e in entries), default=None),
        }

    def overview(self):
        self.sync()
        runs = self.db.rows(
            "SELECT * FROM runs WHERE job='knowledge_organize' AND harness IS NULL "
            "ORDER BY started_at DESC LIMIT 1"
        )
        job = self.service.jobs.scheduler.get_job("knowledge_organize")
        next_run = getattr(job, "next_run_time", None) if runs else None
        loose = self.memory.root / "知识库"
        return {
            "readers": self.access.readers,
            "default_readers": self.access.settings["default_readers"],
            "areas": [self.area(a) for a in self.access.discover()],
            "loose_files": sum(visible(p) for p in loose.iterdir())
            if loose.is_dir() and not is_link(loose)
            else 0,
            "organize": {
                "last_run_at": runs[0]["started_at"] if runs else None,
                "status": runs[0]["status"] if runs else None,
                "next_run_at": next_run.isoformat() if next_run else None,
            },
        }

    def patch_access(self, path, readers):
        self.path(path)
        self.access.set_readers(path, readers)
        self.sync()
        return self.area(next(a for a in self.access.discover() if a["path"] == path))

    def topic(self, name, readers=None):
        self.validate_topic(name)
        readers = self.access.validate(
            self.access.settings["default_readers"] if readers is None else readers
        )
        path = self.path(f"知识库/{name}", exists=False)
        if path.exists():
            raise ConflictError("topic_exists")
        path.parent.mkdir(exist_ok=True)
        try:
            path.mkdir()
        except FileExistsError as exc:
            raise ConflictError("topic_exists") from exc
        self.access.discover()
        self.access.set_readers(self.relative(path), readers)
        return self.area(
            next(a for a in self.access.discover() if a["path"] == self.relative(path))
        )

    @staticmethod
    def validate_topic(name):
        if (
            not 1 <= len(name) <= 40
            or name.startswith(".")
            or name == "整理"
            or any(c in name for c in "/\\:\x00")
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("主题名称无效")

    def files(self, relative):
        self.sync()
        path = self.path(relative)
        if not path.is_dir():
            raise ValueError("path must be a folder")
        canonical = self.relative(path)
        area_path = (
            canonical
            if canonical in {a["path"] for a in self.access.discover()}
            else self.access.area_of(canonical + "/_")
        )
        if canonical == "知识库":
            candidates = [p for p in path.iterdir() if visible(p)]
            area_path = None
        elif canonical == ".":
            candidates = [p for p in path.iterdir() if visible(p)]
        else:
            candidates = [p for p in self.memory.files() if p.is_relative_to(path)]
        areas = {a["path"]: a for a in self.access.discover()}
        entries = sorted(
            (self.entry(p) for p in candidates),
            key=lambda e: (e["modified_at"], e["path"]),
            reverse=True,
        )[:1000]
        return {
            "path": canonical,
            "area": area_path,
            "readers": areas.get(area_path, {}).get("readers", []),
            "entries": entries,
        }

    def file(self, relative):
        self.sync()
        path = self.path(relative)
        if not path.is_file():
            raise ValueError("path must be a file")
        entry = self.entry(path)
        area_path = self.access.area_of(entry["path"])
        areas = {a["path"]: a for a in self.access.discover()}
        # App reads cached extracted text only; indexing/upload does extraction.
        text = self.memory.documents.read(path, extract=False)
        meta, content = (
            frontmatter(text["content"]) if entry["kind"] == "note" else ({}, text["content"])
        )
        source = self.source(entry["path"])
        raw_sources = meta.get("sources")
        sources = [
            {"path": item["path"], "label": item.get("label", "")}
            for item in (raw_sources if isinstance(raw_sources, list) else [])
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("label", ""), str)
        ]
        organized_at = meta.get("organized_at")
        try:
            organized_at = datetime.fromisoformat(str(organized_at))
            if organized_at.tzinfo is None:
                organized_at = None
        except ValueError:
            organized_at = None
        return entry | {
            "area": area_path,
            "readers": areas.get(area_path, {}).get("readers", []),
            "content": content[:200_000],
            "extracted": text["extracted"],
            "truncated": text["truncated"] or len(content) > 200_000,
            "sources": sources,
            "notes": json.loads(source["notes"]) if source else [],
            "organized_by": meta.get("organized_by")
            if isinstance(meta.get("organized_by"), str)
            else None,
            "organized_at": organized_at,
        }

    def raw(self, relative):
        path = self.path(relative)
        if not self.relative(path).startswith("知识库/") or not path.is_file():
            raise ValueError("raw only supports knowledge files")
        if path.stat().st_size > file_limit(path):
            raise ValueError("文件超过格式允许的大小")
        # Fixed here, not left to mimetypes: on Windows it reads the registry, and a machine
        # without Office calls a .docx application/octet-stream.
        types = {
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".heic": "image/heic",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        return path, types.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[
            0
        ] or "application/octet-stream"

    def search(self, query, limit):
        token = current_grant.set(MAIN)  # App is the user, including app+reader device tokens.
        try:
            results = []
            for row in self.memory.search(query, limit):
                entry = self.entry(self.path(row["path"]))
                results.append(
                    {k: entry[k] for k in ("path", "name", "title", "kind")}
                    | {"area": self.access.area_of(row["path"]), "excerpt": row["excerpt"]}
                )
            return {"results": results}
        finally:
            current_grant.reset(token)

    def upload(self, relative, content):
        path = self.path(relative, exists=False)
        canonical = self.relative(path)
        parts = PurePosixPath(canonical).parts
        if (
            len(parts) < 3
            or parts[0] != "知识库"
            or parts[2].casefold() == "整理"
            or path.suffix.lower() not in SUPPORTED | {".csv", ".xlsx", ".zip"}
            or len(content) > MAX_FILE
        ):
            raise ValueError("只能上传支持的资料到主题中，不能上传到整理目录")
        topic = self.path("/".join(parts[:2]))
        if not topic.is_dir():
            raise KeyError("topic_not_found")
        area = next(a for a in self.access.discover() if a["path"] == "/".join(parts[:2]))
        if path.suffix.lower() in {".csv", ".xlsx", ".zip"} and area["readers"]:
            raise ValueError("CSV、XLSX、ZIP 只能上传到没有读者的私人主题，避免账单交给模型整理")
        if path.exists():
            raise ConflictError("file_exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL makes the no-overwrite promise atomic across concurrent uploads.
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ConflictError("file_exists") from exc
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        self.sync()
        return self.entry(path) | (
            {"status": "pending", "error": None} if path.suffix.lower() in SUPPORTED else {}
        )

    def extract_upload(self, relative):
        path = self.path(relative)
        self.memory.index_one(path)
        self.sync()

    def note_write(self, relative, title, content, sources):
        grant = current_grant.get()
        if grant.kind != "organizer" or not grant.area:
            raise ValueError("仅主题整理运行能写整理笔记")
        self.access.require_read(grant.area + "/_")
        path = self.path(relative, exists=False)
        canonical = self.relative(path)
        if (
            not canonical.startswith(grant.area + "/整理/")
            or path.suffix.lower() != ".md"
            or not 1 <= len(title.strip()) <= 200
            or len(content.encode()) > 2_000_000
        ):
            raise ValueError("只能写本主题整理目录里的 Markdown 笔记")
        if not sources:
            raise ValueError("笔记必须有来源")
        normalized_sources = []
        for source in sources:
            if set(source) != {"path", "label"} or not all(
                isinstance(v, str) for v in source.values()
            ):
                raise ValueError("invalid source")
            original = self.path(source["path"], exists=False)
            canonical_source = self.relative(original)
            if (
                not canonical_source.startswith(grant.area + "/")
                or self.is_note(canonical_source)
                or not original.is_file()
            ):
                raise ValueError("来源必须是本主题中存在的资料")
            normalized_sources.append({"path": canonical_source, "label": source["label"]})
        sources = normalized_sources
        previous = None
        if path.exists():
            old, body = self.metadata(path)
            # Never replace a user's manually created file or other content by accident.
            if not old.get("organized_by") or not self.db.rows(
                "SELECT path FROM knowledge_notes WHERE path=?", (canonical,)
            ):
                raise ValueError("已有用户文件不能覆盖，请使用新路径")
            match = ANNOTATIONS.search(body)
            if match:
                previous = body[match.start() + len("## 我的批注") :]
        content = ANNOTATIONS.split(content, maxsplit=1)[0].rstrip()
        meta = {
            "title": title.strip(),
            "sources": sources,
            "organized_by": grant.harness,
            "organized_at": now(),
        }
        links = "\n".join(
            f"- [[{s['path']}]]" + (f" · {s['label']}" if s["label"] else "") for s in sources
        )
        text = (
            "---\n"
            + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            + "---\n\n"
            + content
            + "\n\n## 来源\n\n"
            + links
            + "\n\n## 我的批注"
            + (previous if previous is not None else "\n\n")
        )
        if len(text.encode("utf-8")) > 2_000_000:
            raise ValueError("整理笔记超过 2,000,000 字节，请缩短正文或拆分笔记")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.memory.save(path, text)
        else:
            try:
                with path.open("x", encoding="utf-8", newline="") as output:
                    output.write(text)
            except FileExistsError as exc:
                raise ValueError("已有用户文件不能覆盖，请使用新路径") from exc
            self.memory.index_one(path)
        self.db.execute("INSERT OR IGNORE INTO knowledge_notes VALUES(?)", (canonical,))
        self.receipts.setdefault(grant.name, {}).setdefault(grant.harness, {})[canonical] = {
            s["path"] for s in sources
        }
        return {"path": canonical}

    async def organize(self):
        # Indexing is local; topics without readers are never inserted into a prompt.
        await asyncio.to_thread(self.memory.reindex)
        failures = False
        for area in self.access.discover():
            if area["kind"] != "topic" or not area["readers"]:
                continue
            # A topic with many new sources (a term's slides) takes several batches; up to
            # BATCHES a run, the rest the next night.
            for _ in range(BATCHES):
                try:
                    if not await self.batch(area):
                        break
                except Exception:
                    failures = True
                    break
        if failures:
            raise RuntimeError("knowledge_organize_failed")

    async def batch(self, area):
        """One Agent run over up to 8 pending sources (about 60,000 characters) of a topic.
        True when it moved at least one source on, False when nothing was left or nothing
        could be acknowledged (a source changed during the run)."""
        selected, chunks, count = [], [], 0
        for row in self.db.rows(
            "SELECT * FROM knowledge_sources WHERE status='pending' ORDER BY path"
        ):
            if self.access.area_of(row["path"]) != area["path"]:
                continue
            path = self.path(row["path"])
            document = await asyncio.to_thread(self.memory.documents.read, path)
            if document["error"]:
                self.sync()
                continue
            text = document["content"]
            if len(selected) >= 8 or count >= 60_000:
                continue
            start = row["organize_offset"]
            chunk = text[start : start + 60_000 - count]
            selected.append(row | {"end_offset": start + len(chunk), "text_length": len(text)})
            chunks.append(
                {
                    "path": row["path"],
                    "text": chunk,
                    "part": f"字符 {start + 1}–{start + len(chunk)} / {len(text)}",
                }
            )
            count += len(chunk)
        if not selected:
            return False
        run_name = "knowledge:" + os.urandom(12).hex()
        self.receipts[run_name] = {}
        grant = Grant(None, run_name, frozenset({"mcp"}), kind="organizer", area=area["path"])
        prompt = render(
            "knowledge_organize",
            topic=area["path"],
            sources=json.dumps(chunks, ensure_ascii=False),
        )
        advanced = False
        try:
            preferred = self.service.store.settings.job_harness.get("knowledge_organize", "codex")
            successful_harness = await self.service.jobs.run_agent(
                "knowledge_organize",
                prompt,
                grant=grant,
                harness=preferred,
                succeeded=lambda key=run_name, batch=selected: all(
                    any(
                        row["path"] in refs
                        for refs in self.receipts[key].get(current_grant.get().harness, {}).values()
                    )
                    for row in batch
                ),
                incomplete_error="knowledge_note_not_written",
            )
            written = self.receipts[run_name][successful_harness]
            if area["path"] not in self.access.allowed(grant):
                self.sync()
                return False
            for row in selected:
                # A file changed during the run remains pending, never acknowledge stale text.
                path = self.path(row["path"])
                if sha256(path) == row["sha256"]:
                    notes = sorted(
                        set(json.loads(row["notes"]))
                        | {p for p, refs in written.items() if row["path"] in refs}
                    )
                    complete = row["end_offset"] >= row["text_length"]
                    self.db.execute(
                        "UPDATE knowledge_sources SET status=?,error=NULL,organized_at=?,notes=?,"
                        "organize_offset=? WHERE path=? AND sha256=?",
                        (
                            "organized" if complete else "pending",
                            now() if complete else None,
                            dump(notes),
                            row["end_offset"],
                            row["path"],
                            row["sha256"],
                        ),
                    )
                    advanced = True
        except Exception:
            for row in selected:
                self.db.execute(
                    "UPDATE knowledge_sources SET error='knowledge_organize_failed' "
                    "WHERE path=? AND sha256=? AND status='pending'",
                    (row["path"], row["sha256"]),
                )
            raise
        finally:
            self.receipts.pop(run_name, None)
        return advanced
