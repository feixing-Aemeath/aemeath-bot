"""App contract for video ingestion; the plugin owns validation and processing."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, Field

from core.models import Model
from core.store import ConflictError


def video_plugin(service):
    plugin = service.plugins.instances.get("video_notes")
    if plugin is None:
        raise ConflictError("video_notes_disabled")
    return plugin


class VideoCreate(Model):
    url: str = Field(min_length=1, max_length=2048)
    topic: str | None = Field(default=None, min_length=1, max_length=40)


class Video(Model):
    id: str
    url: str
    platform: Literal["bilibili", "youtube"]
    video_id: str
    topic: str
    status: Literal["queued", "processing", "done", "failed", "needs_setup"]
    step: str
    error: str | None
    title: str | None
    duration: float | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    output_path: str | None


class Videos(Model):
    items: list[Video]
    total: int


def router(service):
    routes = APIRouter()

    @routes.post("/api/v1/videos", response_model=Video)
    async def add_video(body: VideoCreate):
        return await video_plugin(service).add(body.model_dump())

    @routes.get("/api/v1/videos", response_model=Videos)
    async def videos(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return video_plugin(service).queue.list(limit, offset)

    return routes
