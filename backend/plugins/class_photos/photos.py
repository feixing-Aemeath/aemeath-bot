"""PhotoKit boundary. Tests never access the user's Photos library."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

HELPER = Path.home() / "Library/Application Support/AemeathBot/bin/photo-export"


class PhotoLibrary(Protocol):
    def call(self, command: str, payload: dict | None = None): ...


class MacPhotoLibrary:
    def __init__(self, helper=HELPER):
        self.helper = Path(helper).expanduser()

    def call(self, command, payload=None):
        try:
            result = subprocess.run(
                [str(self.helper), command],
                input=json.dumps(payload or {}).encode(),
                capture_output=True,
                timeout=130 if command == "export" else 30,
                check=False,
            )
            if result.returncode:
                raise ValueError("photo_export_failed")
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ValueError("photo_export_unavailable") from exc


class FakePhotoLibrary:
    def __init__(self, items=(), files=None, permission="authorized"):
        self.items, self.files, self.permission = list(items), files or {}, permission
        self.exports = []

    def call(self, command, payload=None):
        if command == "status":
            return {"permission": self.permission}
        if command == "list":
            from datetime import datetime

            first, last = (datetime.fromisoformat(payload[k]) for k in ("from", "to"))
            return sorted(
                [p for p in self.items if first <= datetime.fromisoformat(p["created_at"]) < last],
                key=lambda p: (p["created_at"], p["id"]),
            )[:2000]
        if command == "export":
            self.exports.append(payload["id"])
            source = self.files[payload["id"]]
            target = Path(payload["dest"]).with_suffix(source.suffix.lower())
            shutil.copyfile(source, target)
            return {"path": str(target), "uti": "public.image", "bytes": target.stat().st_size}
        raise ValueError("unsupported_photo_command")


def library(helper=HELPER):
    return (
        MacPhotoLibrary(helper)
        if sys.platform == "darwin"
        else FakePhotoLibrary(permission="unavailable")
    )
