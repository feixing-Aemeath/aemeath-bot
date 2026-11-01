"""Local document extraction. Cached text is derived data outside the user's vault."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Protocol

MAX_FILE = 50 * 1024 * 1024
MAX_PDF = 500 * 1024 * 1024
MAX_TEXT = 2_000_000
TEXT = {".md", ".txt"}
IMAGES = {".png", ".jpg", ".jpeg", ".heic"}
SUPPORTED = TEXT | IMAGES | {".pdf", ".docx"}
HELPER = Path.home() / "Library/Application Support/AemeathBot/bin/doc-text"


def file_limit(path):
    return MAX_PDF if path.suffix.lower() == ".pdf" else MAX_FILE


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Extractor(Protocol):
    def extract(self, path: Path) -> dict: ...


class FakeExtractor:
    def __init__(self, results=None):
        self.results, self.calls = results or {}, []

    def extract(self, path):
        self.calls.append(path.name)
        value = self.results.get(path.name)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise ValueError("本平台未配置资料提取器")
        return value


class MacExtractor:
    def __init__(self, helper=HELPER):
        self.helper = helper

    def extract(self, path):
        if path.suffix.lower() == ".docx":
            result = subprocess.run(
                ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if result.returncode:
                raise ValueError("文档提取失败")
            return {"text": result.stdout.decode("utf-8")}
        mode = "pdf" if path.suffix.lower() == ".pdf" else "image"
        result = subprocess.run(
            [str(self.helper), mode],
            input=json.dumps({"path": str(path)}).encode(),
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise ValueError("文档提取失败（可能加密或无法识别）")
        return json.loads(result.stdout)


class WindowsExtractor:
    def extract(self, path):
        from core.helper_process import run_sync

        mode = {".docx": "docx", ".pdf": "pdf"}.get(path.suffix.lower(), "image")
        result = run_sync(
            [sys.executable, "-m", "core.windows_documents", mode],
            input=json.dumps({"path": str(path.resolve())}).encode(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=120,
            cwd=Path(__file__).resolve().parents[1],
        )
        if result.returncode:
            raise ValueError("文档提取失败（Windows OCR 或文件不可用）")
        return json.loads(result.stdout)


class Documents:
    def __init__(self, directory, extractor=None):
        self.directory = directory
        self.extractor = extractor or (
            MacExtractor()
            if sys.platform == "darwin"
            else WindowsExtractor()
            if sys.platform == "win32"
            else FakeExtractor()
        )
        self._extract_lock = threading.Lock()
        self._errors = {}

    def read(self, path, fingerprint=None, extract=True):
        suffix = path.suffix.lower()
        result = {"content": "", "extracted": suffix not in TEXT, "truncated": False, "error": None}
        if suffix not in SUPPORTED:
            return result | {"error": "不支持这种格式"}
        maximum = file_limit(path)
        if path.stat().st_size > maximum:
            return result | {"error": f"文件超过 {maximum // (1024 * 1024)} MB"}
        if suffix in TEXT:
            try:
                with path.open("rb") as stream:
                    raw = stream.read(MAX_TEXT + 4)
                text = (
                    raw[:MAX_TEXT].decode("utf-8", errors="strict")
                    if len(raw) <= MAX_TEXT
                    else raw[:MAX_TEXT].decode("utf-8", errors="ignore")
                )
                return result | {"content": text, "truncated": len(raw) > MAX_TEXT}
            except UnicodeError:
                return result | {"error": "文件不是 UTF-8 文本"}
        fingerprint = fingerprint or sha256(path)
        cache = self.directory / f"{fingerprint}.json"
        if cache.is_file():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if cached.get("error") is None:
                    return cached
            except (ValueError, OSError):
                pass
        if not extract:
            return self._errors.get(fingerprint, result)
        # Indexing, upload and organization can overlap. Recheck the cache under a
        # lock, so the same bytes are only extracted once; App cached reads do not wait.
        with self._extract_lock:
            if cache.is_file():
                try:
                    cached = json.loads(cache.read_text(encoding="utf-8"))
                    if cached.get("error") is None:
                        return cached
                except (ValueError, OSError):
                    pass
            return self._extract(path, cache, result)

    def _extract(self, path, cache, result):
        try:
            data = self.extractor.extract(path)
            if path.suffix.lower() == ".pdf":
                text = "\n\n".join(f"[第 {p['page']} 页]\n{p['text']}" for p in data["pages"])
                meaningful = "".join(p["text"] for p in data["pages"])
            else:
                text = meaningful = data["text"]
            if not meaningful.strip():
                result["error"] = "没有识别出文字"
            raw = text.encode("utf-8")
            result |= {
                "content": raw[:MAX_TEXT].decode("utf-8", errors="ignore"),
                "truncated": len(raw) > MAX_TEXT or bool(data.get("truncated")),
            }
        except Exception:
            # No subprocess stderr, file text or arbitrary exception reaches logs or runs.
            result["error"] = "提取失败：文件可能加密、损坏、无法识别或提取器不可用"
        if result["error"]:
            # App reads can show the last failure, but every extraction retries it.
            self._errors[cache.stem] = result
            return result
        self._errors.pop(cache.stem, None)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Atomic cache replacement keeps concurrent cached reads from seeing partial JSON.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.directory, delete=False
        ) as temporary:
            json.dump(result, temporary, ensure_ascii=False)
        os.replace(temporary.name, cache)
        return result
