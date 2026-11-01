"""App-facing state of enabled knowledge inputs; never file contents or credentials."""

import asyncio
from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from core.models import Model


class DisabledInput(Model):
    enabled: Literal[False]


class Notebook(Model):
    path: str
    topic: str | None
    status: Literal["copied", "unmapped", "conflict", "missing", "wrong_format", "waiting"]
    updated_at: AwareDatetime
    message: str | None


class GoodNotesInput(Model):
    enabled: Literal[True]
    address_path: Literal["/dav/goodnotes/"]
    last_backup_at: AwareDatetime | None
    notebooks: list[Notebook]


class PhotoDay(Model):
    day: date
    course: str
    topic: str
    count: int


class ClassPhotosInput(Model):
    enabled: Literal[True]
    permission: Literal[
        "authorized", "limited", "denied", "restricted", "not_determined", "unavailable"
    ]
    last_run_at: AwareDatetime | None
    recent: list[PhotoDay]
    skipped_not_slides: int


class KnowledgeInputs(Model):
    goodnotes: GoodNotesInput | DisabledInput
    class_photos: ClassPhotosInput | DisabledInput


async def inputs(service):
    result = {}
    for identifier in ("goodnotes", "class_photos"):
        plugin = service.plugins.instances.get(identifier)
        if plugin is None:
            result[identifier] = {"enabled": False}
        elif identifier == "class_photos":
            result[identifier] = await plugin.status()
        else:
            result[identifier] = await asyncio.to_thread(plugin.status)
    return result
