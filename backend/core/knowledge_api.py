"""App-only knowledge contract. BearerAuth requires app scope for every route here."""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import AwareDatetime, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from core.knowledge_inputs import KnowledgeInputs, inputs
from core.models import Model

BINARY_MEDIA = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/heic",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/markdown",
    "text/plain",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "application/octet-stream",
)


class Reader(Model):
    id: str
    kind: Literal["bot", "agent"]
    sticker: str | None


class Area(Model):
    path: str
    name: str
    kind: Literal["topic", "system"]
    readers: list[str]
    locked: list[str]
    notes: int
    sources: int
    files: int
    pending: int
    failed: int
    updated_at: AwareDatetime | None


class Organize(Model):
    last_run_at: AwareDatetime | None
    status: str | None
    next_run_at: AwareDatetime | None


class Overview(Model):
    readers: list[Reader]
    default_readers: list[str]
    areas: list[Area]
    loose_files: int
    organize: Organize


class KnowledgePatch(Model):
    default_readers: list[str] | None = None
    agents: list[str] | None = None


class AccessPatch(Model):
    path: str
    readers: list[str]


class TopicCreate(Model):
    name: str = Field(min_length=1, max_length=40)
    readers: list[str] | None = None


class Entry(Model):
    path: str
    name: str
    title: str
    kind: Literal["note", "source", "file"]
    format: Literal["md", "txt", "pdf", "image", "docx", "other"]
    size: int
    modified_at: AwareDatetime
    status: Literal["pending", "organized", "failed", "skipped", "unreadable"] | None
    error: str | None


class Files(Model):
    path: str
    area: str | None
    readers: list[str]
    entries: list[Entry]


class Source(Model):
    path: str
    label: str


class File(Entry):
    area: str | None
    readers: list[str]
    content: str
    extracted: bool
    truncated: bool
    sources: list[Source]
    notes: list[str]
    organized_by: str | None
    organized_at: AwareDatetime | None


class SearchEntry(Model):
    path: str
    name: str
    title: str
    kind: Literal["note", "source", "file"]
    area: str | None
    excerpt: str


class Search(Model):
    results: list[SearchEntry]


def router(service):
    """Plain `def` routes run in the thread pool: they walk and hash the vault."""
    api = APIRouter(
        prefix="/api/v1/knowledge",
        tags=["knowledge"],
        responses={
            404: {"description": "路径或区域不存在"},
            422: {"description": "参数、路径或符号链接无效"},
        },
    )
    library = service.knowledge

    @api.get("/inputs", response_model=KnowledgeInputs)
    async def input_status():
        return await inputs(service)

    @api.get("", response_model=Overview)
    def overview():
        return library.overview()

    @api.patch("", response_model=Overview)
    def patch(body: KnowledgePatch):
        if any(getattr(body, key) is None for key in body.model_fields_set):
            raise ValueError("null patch")
        library.access.patch(**body.model_dump(exclude_unset=True))
        return library.overview()

    @api.patch("/access", response_model=Area)
    def access(body: AccessPatch):
        return library.patch_access(body.path, body.readers)

    @api.post(
        "/topics",
        response_model=Area,
        status_code=201,
        responses={409: {"description": "主题已存在"}},
    )
    def topic(body: TopicCreate):
        return library.topic(body.name, body.readers)

    @api.get("/files", response_model=Files)
    def files(path: str):
        return library.files(path)

    @api.get("/file", response_model=File)
    def file(path: str):
        return library.file(path)

    @api.get(
        "/raw",
        response_class=FileResponse,
        responses={
            200: {
                "description": "原文件（PDF 最多 500 MiB，其他格式 50 MiB）",
                "content": {
                    mime: {"schema": {"type": "string", "format": "binary"}}
                    for mime in (
                        "application/pdf",
                        "image/png",
                        "image/jpeg",
                        "image/heic",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "text/markdown",
                        "text/plain",
                    )
                },
            }
        },
    )
    def raw(path: str):
        target, mime = library.raw(path)
        return FileResponse(target, media_type=mime, filename=target.name)

    @api.get("/search", response_model=Search)
    def search(
        q: Annotated[str, Query(min_length=1, max_length=500)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        return library.search(q, limit)

    @api.post(
        "/files",
        response_model=Entry,
        status_code=201,
        responses={
            409: {"description": "同名文件已存在，绝不覆盖"},
            413: {"description": "原文件超过 50 MiB"},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    mime: {"schema": {"type": "string", "format": "binary"}}
                    for mime in BINARY_MEDIA
                },
            }
        },
    )
    async def upload(path: str, request: Request, background_tasks: BackgroundTasks):
        result = await run_in_threadpool(library.upload, path, await request.body())
        background_tasks.add_task(library.extract_upload, result["path"])
        return result

    return api
