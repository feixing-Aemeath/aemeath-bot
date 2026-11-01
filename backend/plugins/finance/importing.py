"""Inbox-only imports and no-clobber archives. Passwords live only on the call stack."""

import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from core.documents import file_limit, sha256
from core.knowledge_access import safe_path
from core.platforms import is_link
from plugins.finance import parsers
from plugins.finance.config import PRIVATE, SOURCES, SUFFIXES, FinanceError

MESSAGES = {
    "needs_password": "需要 zip 密码；请在记账对话里解锁",
    "needs_unzip": "这种 zip 加密方式暂不支持，请先在本机解压，再上传里面的文件",
    "unrecognized": "认不出账单表头，请核对导出格式",
    "failed": "账单解析失败，请核对格式或本地 PDF 提取器",
    "unsafe": "路径包含符号链接或文件发生变化，保留原件",
    "archived": "已导入并归档",
    "duplicate": "重复的账单，已归档",
    "archive_failed": "已记账但归档失败，保留原件，下次重试",
}


class Importer:
    def __init__(self, context, config, ledger):
        self.context, self.config, self.ledger = context, config, ledger
        self.knowledge = context.memory.knowledge
        self.db = ledger.db
        self.area = "知识库/" + config.get("topic", "财务")
        self.knowledge.validate_topic(config.get("topic", "财务"))
        self.temporary = context.data_dir / "finance" / "tmp"
        self.temporary.mkdir(parents=True, exist_ok=True)

    def ensure(self, create=False):
        path = safe_path(self.context.memory.root, self.area)
        if not path.exists() and create:
            self.knowledge.topic(self.config.get("topic", "财务"), readers=[])
        if not path.is_dir():
            raise FinanceError("财务主题不存在")
        area = next((a for a in self.knowledge.access.discover() if a["path"] == self.area), None)
        if area is None or area["readers"]:
            raise FinanceError(PRIVATE)
        return path

    def record(self, relative, digest, status, at, archive=None):
        self.db.execute(
            "INSERT INTO finance_files VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
            "sha256=excluded.sha256,status=excluded.status,error=excluded.error,"
            "archive_path=excluded.archive_path,updated_at=excluded.updated_at",
            (relative, digest, status, MESSAGES[status], archive, at),
        )
        return {"file": relative, "status": status, "reason": MESSAGES[status]}

    def scan(self, clock, retry=False):
        inbox = self.ensure()
        # A file that could not be imported waits for the user (a password, another export)
        # instead of being read, and a PDF extracted, every few minutes; a changed file, or an
        # explicit finance_import_now, tries again.
        waiting = {
            r["path"]: r["sha256"]
            for r in self.db.rows(
                "SELECT path,sha256 FROM finance_files WHERE status IN "
                "('needs_password','needs_unzip','unrecognized','failed')"
            )
        }
        results = []
        for path in sorted(inbox.iterdir()):
            if path.suffix.lower() not in SUFFIXES or path.name.startswith("."):
                continue
            relative = self.area + "/" + path.name
            if (
                not retry
                and relative in waiting
                and path.is_file()
                and not is_link(path)
                and sha256(path) == waiting[relative]
            ):
                continue
            results.append(self.one(path.name, clock))
        return {"files": results}

    def parse(self, path):
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return parsers.tables(parsers.csv_rows(path.read_bytes()), self.ledger.zone)
        if suffix == ".xlsx":
            return parsers.tables(parsers.xlsx_rows(path), self.ledger.zone)
        if suffix == ".pdf":
            result = self.context.memory.documents.read(path)
            if result["error"] or result["truncated"]:
                raise FinanceError("PDF 提取失败或文字被截断")
            return parsers.pdf(result["content"], self.ledger.zone)
        raise FinanceError("认不出账单表头")

    def unzip(self, path, directory, password):
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 100 or sum(i.file_size for i in members) > parsers.MAX_BYTES:
                raise FinanceError("压缩包过大")
            candidates = []
            for info in members:
                name = PurePosixPath(info.filename.replace("\\", "/"))
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or ":" in info.filename
                    or stat.S_ISLNK(info.external_attr >> 16)
                ):
                    raise FinanceError("压缩包路径无效")
                if info.is_dir() or any(p.startswith(".") or p == "__MACOSX" for p in name.parts):
                    continue
                if name.suffix.lower() in {".csv", ".xlsx", ".pdf"}:
                    candidates.append(info)
            if not candidates:
                raise FinanceError("认不出账单表头")
            statements = []
            for index, info in enumerate(candidates):
                if info.flag_bits & 1 and password is None:
                    return "needs_password", None
                # Read a bounded member; never extractall, never use member paths on disk.
                try:
                    data = archive.read(
                        info, pwd=password.encode("utf-8") if password is not None else None
                    )
                except NotImplementedError:
                    return "needs_unzip", None
                except RuntimeError:
                    return "needs_password", None
                file = directory / f"statement-{index}{PurePosixPath(info.filename).suffix.lower()}"
                with file.open("xb") as stream:
                    stream.write(data)
                statements.append(self.parse(file))
            if len({s.source for s in statements}) != 1:
                raise FinanceError("压缩包包含多种来源，请分别上传")
            first = statements[0]
            return None, parsers.Statement(
                first.source,
                [r for s in statements for r in s.rows],
                min(s.start for s in statements),
                max(s.end for s in statements),
                "statement"
                if all(s.coverage == "statement" for s in statements)
                else "transactions",
                # The latest statement of each card has its balance.
                {
                    k: v
                    for s in sorted(statements, key=lambda s: s.end)
                    for k, v in s.balances.items()
                },
            )

    def one(self, filename, clock, password=None):
        self.ensure()
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or "\\" in filename
            or filename.startswith(".")
            or Path(filename).suffix.lower() not in SUFFIXES
        ):
            raise FinanceError("只能导入财务主题最外层的账单文件")
        relative = self.area + "/" + filename
        digest = None
        try:
            path = safe_path(self.context.memory.root, relative)
            if not path.is_file():
                raise FinanceError("账单文件不存在")
            before = path.stat()
            if before.st_size > file_limit(path):
                raise FinanceError("账单文件过大")
            digest = sha256(path)
            old = self.db.rows("SELECT * FROM finance_imports WHERE sha256=?", (digest,))
            duplicate = bool(old)
            if old:
                imported = old[0]
            else:
                if path.suffix.lower() == ".zip":
                    with tempfile.TemporaryDirectory(dir=self.temporary) as folder:
                        status, statement = self.unzip(path, Path(folder), password)
                    if status:
                        return self.record(relative, digest, status, clock)
                else:
                    statement = self.parse(path)
                self.ensure()
                if sha256(path) != digest or path.stat().st_ino != before.st_ino:
                    return self.record(relative, digest, "unsafe", clock)
                identity, _ = self.ledger.imported(statement, digest, clock)
                imported = self.db.rows("SELECT * FROM finance_imports WHERE id=?", (identity,))[0]
            self.ensure()
            try:
                archived = self.archive(relative, imported, before, digest)
            except (OSError, ValueError):
                return self.record(relative, digest, "archive_failed", clock)
            self.db.execute(
                "UPDATE finance_imports SET status='archived',archive_path=? WHERE id=?",
                (imported["archive_path"] or archived, imported["id"]),
            )
            return self.record(
                relative, digest, "duplicate" if duplicate else "archived", clock, archived
            )
        except FinanceError as exc:
            if str(exc) in {PRIVATE, "财务主题不存在"}:
                raise
            status = "unrecognized" if str(exc) == "认不出账单表头" else "failed"
            return self.record(relative, digest, status, clock)
        except ValueError:
            return self.record(relative, digest, "unsafe", clock)
        except Exception:
            # Exception messages can contain passwords, amounts, filenames and merchants.
            return self.record(relative, digest, "failed", clock)

    def reread(self, clock):
        """Read the archived bills again with the current readers and add the rows they
        missed before (a reader that has improved). Rows already in the ledger, and what the
        user decided about them, stay. An archived zip would need its password again."""
        self.ensure()
        result = {"files": 0, "added": 0, "skipped": 0}
        for imported in self.db.rows(
            "SELECT * FROM finance_imports WHERE archive_path IS NOT NULL ORDER BY created_at"
        ):
            path = safe_path(self.context.memory.root, imported["archive_path"])
            if not path.is_file() or is_link(path) or path.suffix.lower() == ".zip":
                result["skipped"] += 1
                continue
            statement = self.parse(path)
            if statement.source != imported["source"]:
                raise FinanceError("归档账单的来源变了，请保留原件核对")
            result["files"] += 1
            result["added"] += self.ledger.add_rows(statement, imported["id"], clock)
        return result

    def archive(self, relative, imported, before, digest):
        root = self.context.memory.root
        source = safe_path(root, relative)
        if source.stat().st_ino != before.st_ino or sha256(source) != digest:
            raise FinanceError("账单文件发生变化")
        folder = f"{self.area}/账单原件/{SOURCES[imported['source']]}/{imported['end'][:4]}"
        directory = safe_path(root, folder)
        directory.mkdir(parents=True, exist_ok=True)
        index = 1
        while True:
            suffix = "" if index == 1 else f" ({index})"
            target = safe_path(root, f"{folder}/{source.stem}{suffix}{source.suffix}")
            try:
                # Atomic EEXIST, same-volume link publication; never overwrite a user file.
                os.link(source, target, follow_symlinks=False)
                break
            except FileExistsError:
                index += 1
        # Verify source still refers to the bytes we linked before removing the inbox name.
        if (
            is_link(source)
            or source.stat().st_ino != target.stat().st_ino
            or sha256(target) != digest
        ):
            raise FinanceError("账单文件发生变化")
        source.unlink()
        return target.relative_to(root).as_posix()
