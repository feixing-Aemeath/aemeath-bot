"""Local CLI adapters. All processes use helper_process and bounded process groups."""

import asyncio
import html
import importlib.util
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Protocol

from core import helper_process
from core.documents import HELPER, WindowsExtractor
from core.platforms import find_command


def asr_default():
    if sys.platform == "win32":
        return str(Path(__file__).resolve().parents[2] / "scripts/windows_transcribe.py")
    return "mlx_whisper"


class VideoError(Exception):
    """Only fixed human-readable reasons may leave the media boundary."""


class NeedsSetup(VideoError):
    pass


class Rejected(VideoError):
    pass


def model_ready(value):
    path = Path(value).expanduser()
    return (
        path.is_dir()
        and (path / "config.json").is_file()
        and any(
            (path / name).is_file()
            for name in ("weights.npz", "weights.safetensors", "model.safetensors", "model.bin")
        )
    )


def setup_issues(config, which=shutil.which):
    issues = []
    for key, command in (
        ("yt_dlp", "yt-dlp"),
        ("ffmpeg", "ffmpeg"),
        ("asr_command", asr_default()),
    ):
        path = Path(config.get(key, command)).expanduser()
        if not (path.suffix.lower() == ".py" and path.is_file()) and not which(str(path)):
            issues.append(f"缺少命令：{command}")
    if not model_ready(config.get("asr_model", "")):
        issues.append("缺少本地语音识别模型（需配置 asr_model）")
    if (
        sys.platform == "win32"
        and not config.get("asr_command")
        and importlib.util.find_spec("faster_whisper") is None
    ):
        issues.append("缺少本地语音识别依赖：uv sync --locked --extra windows-asr")
    doc_text = Path(config.get("doc_text", str(HELPER))).expanduser()
    if (
        config.get("slides", True)
        and not (sys.platform == "win32" and not config.get("doc_text"))
        and not (doc_text.suffix.lower() == ".py" and doc_text.is_file())
        and not which(str(doc_text))
    ):
        issues.append("缺少本地图片文字识别命令 doc-text")
    return issues


def size_of(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def segments(data, offset=0):
    result = []
    for item in data:
        start, text = float(item["start"]), str(item["text"]).strip()
        if not math.isfinite(start) or start < 0:
            raise VideoError("识别结果时间点无效")
        if text:
            result.append({"start": start + offset, "text": text})
    return result


def subtitles(path):
    """VTT/SRT incl. YouTube inline timestamps and rolling-caption duplicates."""
    result = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r", "")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->", line)
            if not match:
                continue
            hours, minutes, seconds, millis = match.groups()
            start = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
            text = html.unescape(re.sub(r"<[^>]*>", "", " ".join(lines[index + 1 :]))).strip()
            if text and (not result or text != result[-1]["text"]):
                result.append({"start": start, "text": text})
            break
    return result


class ASREngine(Protocol):
    async def transcribe(self, media, path, directory) -> list[dict]: ...


class WhisperCLI:
    async def transcribe(self, media, path, directory):
        command = media.command("asr_command", asr_default())
        model = media.config.get("asr_model", "")
        if not model_ready(model):
            raise NeedsSetup("需要先安装本地语音识别模型并配置 asr_model")
        # mlx_whisper reads audio by running `ffmpeg` from PATH, and launchd's PATH has no
        # Homebrew: put the configured ffmpeg first.
        ffmpeg = Path(media.command("ffmpeg", "ffmpeg")).parent
        # A local directory plus offline env: the worker never fetches a model from the Hub.
        await media.run(
            [
                command,
                str(path),
                "--model",
                str(Path(model).expanduser().resolve()),
                "--output-dir",
                str(directory),
                "--output-format",
                "json",
                "--verbose",
                "False",
            ],
            offline=True,
            path=[ffmpeg],
        )
        return segments(
            json.loads((directory / (path.stem + ".json")).read_text(encoding="utf-8"))["segments"]
        )


class Media:
    def __init__(self, config, root):
        self.config, self.root = config, root
        self.asr = WhisperCLI()

    def command(self, key, default):
        value = str(Path(self.config.get(key, default)).expanduser())
        found = find_command(value)
        if not found:
            raise NeedsSetup(f"需要先安装命令：{default}")
        return found

    def disk_check(self):
        if size_of(self.root) >= self.config.get("disk_limit_gb", 10) * 1024**3:
            raise NeedsSetup("本地视频空间达到上限，已暂停；请释放空间或调高 disk_limit_gb")

    async def run(self, args, *, data=None, offline=False, path=()):
        self.disk_check()
        env = os.environ.copy()
        if offline:
            env |= {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        if path:
            env["PATH"] = os.pathsep.join([*map(str, path), env.get("PATH", os.defpath)])
        task = asyncio.create_task(
            helper_process.run(
                args, data, self.config.get("step_timeout", 900), capture_error=True, env=env
            )
        )
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.2)
                self.disk_check()
            code, output = await task
            if code:
                error = output.decode("utf-8", errors="replace").lower()
                if any(
                    word in error
                    for word in (
                        "login",
                        "log in",
                        "sign in",
                        "cookies",
                        "members-only",
                        "premium",
                        "private video",
                        "paywall",
                        "payment",
                        "付费",
                        "登录",
                        "会员",
                    )
                ):
                    raise Rejected("这个视频需要登录或付费才能看")
                raise VideoError("本地视频处理命令失败（可能网络不可用或视频已失效）")
            return output
        except TimeoutError as exc:
            raise VideoError("视频处理超时") from exc
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def ytdlp(self, row, *args):
        return await self.run(
            [
                self.command("yt_dlp", "yt-dlp"),
                "--ignore-config",
                "--no-cookies",
                "--no-cookies-from-browser",
                "--no-cache-dir",
                "--no-playlist",
                "--no-progress",
                "--retries",
                "0",
                "--socket-timeout",
                "30",
                *map(str, args),
                "--",
                row["url"],
            ]
        )

    async def ffmpeg(self, *args):
        return await self.run(
            [
                self.command("ffmpeg", "ffmpeg"),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                *map(str, args),
            ]
        )

    async def ocr(self, path):
        if sys.platform == "win32" and not self.config.get("doc_text"):
            result = await asyncio.to_thread(WindowsExtractor().extract, path)
            return str(result["text"]).strip()
        output = await self.run(
            [
                self.command("doc_text", str(HELPER)),
                "image",
            ],
            data=json.dumps({"path": str(path)}).encode(),
        )
        return str(json.loads(output)["text"]).strip()
