import asyncio
import base64
import binascii
import hmac
import unicodedata
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

import jsonschema
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import AwareDatetime, ValidationError
from starlette.responses import JSONResponse

from core.digests import brief, combine
from core.extraction_stats import extraction_stats
from core.health_upload import upload as upload_health
from core.knowledge_api import router as knowledge_router
from core.mcp_server import create_mcp
from core.models import (
    BotPreferences,
    BotPreferencesUpdate,
    Card,
    ChatAccepted,
    ChatPage,
    ChatSend,
    DigestPage,
    ExtractionStats,
    HealthAccepted,
    HealthBatch,
    HealthReportKind,
    HealthReportPage,
    HealthReportStatus,
    InboxAccepted,
    InboxItem,
    JobAccepted,
    OpenChat,
    OpenChatAccepted,
    PluginPatch,
    PluginView,
    RunPage,
    RunStatus,
    RuntimeSettings,
    SettingsPatch,
    Status,
    Today,
    Todo,
    TodoNew,
    TodoPage,
    TodoPatch,
    TodoSourceTexts,
    TodoStatus,
)
from core.service import Service
from core.source_views import screen_instance, unsure_items
from core.store import ConflictError, todo_row
from core.tokens import MAIN, current_grant, required_scopes
from core.video_api import router as video_router
from core.webdav import WebDAV

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
Before = Annotated[
    str,
    Query(
        min_length=1,
        max_length=2048,
        description="Exclusive time/date boundary or next_before cursor",
    ),
]


class BearerAuth:
    """Pure ASGI middleware also protects mounted MCP and OpenAPI routes.

    ASSISTANT_TOKEN may call everything; a device token only the routes of its scopes
    (core/tokens.py). Unknown, revoked or expired tokens get 401, a token without the scope
    for the route 403.
    """

    def __init__(self, app, token, tokens):
        self.app, self.token, self.tokens = app, token.encode(), tokens

    def grant(self, header, dav=False):
        if dav and header[:6].lower() == b"basic ":
            try:
                credentials = base64.b64decode(header[6:], validate=True)
                _, password = credentials.split(b":", 1)
                # Basic uses device tokens only, never the environment's master token.
                return self.tokens.check(password.decode("ascii"))
            except (ValueError, UnicodeDecodeError, binascii.Error):
                return None
        if hmac.compare_digest(header, b"Bearer " + self.token):
            return MAIN
        if not header.startswith(b"Bearer "):
            return None
        try:
            return self.tokens.check(header[len(b"Bearer ") :].decode("ascii"))
        except UnicodeDecodeError:
            return None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            dav = scope["path"] == "/dav" or scope["path"].startswith("/dav/")
            grant = self.grant(headers.get(b"authorization", b""), dav=dav)
            if grant is None:
                await JSONResponse(
                    {"detail": "unauthorized"},
                    401,
                    headers={
                        "WWW-Authenticate": 'Basic realm="Aemeath WebDAV", Bearer'
                        if dav
                        else "Bearer"
                    },
                )(scope, receive, send)
                return
            if not grant.scopes & required_scopes(scope["method"], scope["path"]):
                await JSONResponse(
                    {"detail": "insufficient_scope"},
                    403,
                    headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
                )(scope, receive, send)
                return
            if dav and any(unicodedata.category(c).startswith("C") for c in scope["path"]):
                await JSONResponse({"detail": "invalid_path"}, 403)(scope, receive, send)
                return
            if not dav and scope["method"] in {"POST", "PATCH", "PUT"}:
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    maximum = (
                        50 * 1024 * 1024
                        if scope["path"] == "/api/v1/knowledge/files"
                        else 2_000_000
                    )
                    if len(body) > maximum:
                        await JSONResponse({"detail": "request_too_large"}, 413)(
                            scope, receive, send
                        )
                        return
                    if not message.get("more_body", False):
                        break
                original_receive, delivered = receive, False

                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await original_receive()

                receive = replay
        context = current_grant.set(grant) if scope["type"] == "http" else None
        try:
            await self.app(scope, receive, send)
        finally:
            if context is not None:
                current_grant.reset(context)


def create_app(settings=None, service=None):
    if service is None:
        from core.config import Settings

        service = Service(settings or Settings())
    mcp = create_mcp(service)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await service.close()

    app = FastAPI(
        title="24 小时助手", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.service, app.state.mcp = service, mcp
    app.add_middleware(
        BearerAuth, token=service.settings.token.get_secret_value(), tokens=service.tokens
    )

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "not_found"}, 404)

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse({"detail": "state_conflict"}, 409)

    async def invalid(request, exc):
        return JSONResponse({"detail": "invalid_request"}, 422)

    for error in (ValueError, ValidationError, RequestValidationError, jsonschema.ValidationError):
        app.add_exception_handler(error, invalid)

    @app.exception_handler(Exception)
    async def failure(request, exc):
        return JSONResponse({"detail": "internal_error"}, 500)

    @app.get("/api/v1/status", response_model=Status)
    async def status():
        runs = service.store.runs(limit=1)["items"]
        return {
            "online": True,
            "platform": service.plugins.platform,
            "apple_mode": service.settings.apple_mode,
            "apple_sync_error": getattr(service.apple.bridge, "error", None),
            "agents": {
                name: {
                    "available": harness.available(),
                    "models": getattr(harness, "models", list)(),
                }
                for name, harness in service.jobs.harnesses.items()
            },
            "last_run": runs[0] if runs else None,
            "pending_sync": service.db.rows("SELECT count(*) n FROM outbox")[0]["n"],
        }

    @app.get("/api/v1/today", response_model=Today)
    async def today():
        zone = ZoneInfo(service.store.settings.timezone)
        now = datetime.now(zone)
        day = now.date()
        candidates = service.db.rows(
            "SELECT * FROM todos WHERE status='pending' ORDER BY due_at,id"
        )
        todos = service.store.with_sources(
            [
                todo_row(row)
                for row in candidates
                if row["due_at"]
                and datetime.fromisoformat(row["due_at"]).astimezone(zone).date() <= day
            ]
        )
        # Every plugin's cards at once (each has its own 10 s limit), each marked with its
        # plugin: the App's dashboard shows them all from this one request.
        plugins = service.plugins.list()
        groups = await asyncio.gather(*(service.plugins.cards(p["id"]) for p in plugins))
        cards = [card for group in groups for card in group]
        sources = {p["id"] for p in plugins if "source" in p["provides"]} | {"share"}
        # The assistant's summary: what the message sources said today, without what has gone
        # by and with only the most pressing todos (digests.brief). Other Bots' digests (usage,
        # the monthly report) stay on their own pages.
        summaries = service.store.digests(day.isoformat(), limit=200)["items"]
        said = combine(s["summary"] for s in summaries if s["plugin"] in sources)
        return {
            "date": day,
            "summary": brief(said, now) if said else "",
            "todos": todos,
            "cards": cards,
            "unsure": unsure_items(service, day, summaries, now),
        }

    @app.post(
        "/api/v1/todos",
        response_model=Todo,
        status_code=201,
        responses={
            200: {
                "model": Todo,
                "description": "Pending todo with the same current deduplication key",
            }
        },
    )
    async def new_todo(data: TodoNew, response: Response):
        result = await service.new_todo(data)
        response.status_code = 201 if result["disposition"] == "added" else 200
        return result["todo"]

    @app.post("/api/v1/inbox", response_model=InboxAccepted, status_code=202)
    async def inbox(data: InboxItem):
        identifier = service.store.inbox(data)
        return {
            "message_id": identifier,
            "job_started": service.jobs.submit_if_idle("extract_todos"),
        }

    @app.get("/api/v1/todos/{id}/sources", response_model=TodoSourceTexts)
    async def todo_sources(id: str):
        return service.store.todo_source_texts(id)

    @app.get("/api/v1/stats/extraction", response_model=ExtractionStats)
    async def extraction(weeks: Annotated[int, Query(ge=1, le=26)] = 4):
        return extraction_stats(service.store, weeks)

    @app.get("/api/v1/todos", response_model=TodoPage)
    async def todos(status: TodoStatus | None = None, limit: Limit = 50, offset: Offset = 0):
        return service.store.todos(status, limit, offset)

    @app.patch(
        "/api/v1/todos/{id}",
        response_model=Todo,
        responses={
            409: {
                "description": "Terminal deletion, cancelled todo completed without reopening, or duplicate deduplication key"
            }
        },
    )
    async def update_todo(id: str, patch: TodoPatch):
        return await service.update_todo(id, patch)

    @app.get("/api/v1/digests", response_model=DigestPage)
    async def digests(
        date: date | None = None,
        plugin: str | None = None,
        limit: Limit = 50,
        offset: Offset = 0,
        before: Before | None = None,
    ):
        return service.store.digests(
            date.isoformat() if date else None, plugin, limit, offset, before
        )

    @app.get("/api/v1/runs", response_model=RunPage)
    async def runs(
        limit: Limit = 50,
        offset: Offset = 0,
        job: str | None = None,
        status: RunStatus | None = None,
        since: Annotated[
            AwareDatetime | None, Query(description="Inclusive started_at boundary")
        ] = None,
        until: Annotated[
            AwareDatetime | None, Query(description="Exclusive started_at boundary")
        ] = None,
    ):
        """Successful extract_todos runs count harness attempts only. Omit status to include scheduler runs."""
        return service.store.runs(limit, offset, job, status, since, until)

    @app.post("/api/v1/jobs/{job_id}/run", response_model=JobAccepted, status_code=202)
    async def run_job(job_id: str):
        service.jobs.submit(job_id)
        return {"job_id": job_id, "status": "accepted"}

    @app.get("/api/v1/plugins", response_model=list[PluginView])
    async def plugins():
        return service.plugins.list()

    @app.patch("/api/v1/plugins/{id}", response_model=PluginView)
    async def patch_plugin(id: str, patch: PluginPatch):
        return await service.plugins.patch(id, patch)

    @app.delete("/api/v1/plugins/{id}", status_code=204)
    async def delete_plugin(id: str):
        await service.plugins.delete(id)
        return Response(status_code=204)

    @app.get("/api/v1/plugins/{id}/cards", response_model=list[Card])
    async def plugin_cards(id: str):
        return await service.plugins.cards(id)

    @app.post("/api/v1/plugins/{id}/open-chat", response_model=OpenChatAccepted, status_code=202)
    async def open_chat(id: str, data: OpenChat):
        instance = screen_instance(service, id)
        if data.chat_name not in instance.config.get("whitelist", []):
            raise HTTPException(403, "screen_open_not_whitelisted")
        result = instance.request_open(data.chat_name)
        if result["status"] != "scheduled":
            reason = result["reason"]
            raise HTTPException(409 if reason == "screen_open_daily_limit" else 403, reason)
        return {"accepted": True, "job_started": service.jobs.submit_if_idle(f"fetch:{id}")}

    health_upload_lock = asyncio.Lock()

    @app.post("/api/v1/health/samples", response_model=HealthAccepted)
    async def health(batch: HealthBatch):
        async with service.plugins.locks.get("health", health_upload_lock):
            return await asyncio.to_thread(upload_health, service, batch)

    @app.get("/api/v1/health/reports", response_model=HealthReportPage)
    async def health_reports(
        date: date | None = None,
        kind: HealthReportKind | None = None,
        status: HealthReportStatus | None = None,
        limit: Limit = 50,
        offset: Offset = 0,
        before: Before | None = None,
    ):
        day = date.isoformat() if date else None
        return service.store.health_reports(day, kind, status, limit, offset, before)

    @app.get("/api/v1/bots/{bot_id}/messages", response_model=ChatPage)
    async def chat_messages(
        bot_id: str, limit: Limit = 50, offset: Offset = 0, before: Before | None = None
    ):
        service.chat.bot(bot_id)
        return service.chat.page(bot_id, limit, offset, before)

    @app.post("/api/v1/bots/{bot_id}/messages", response_model=ChatAccepted, status_code=202)
    async def chat_send(bot_id: str, body: ChatSend):
        if body.model and not body.harness:
            raise ValueError("model_needs_harness")
        return await service.chat.send(
            bot_id, body.text.strip(), body.bot_name, body.harness, body.model
        )

    @app.get("/api/v1/app/bot-preferences", response_model=BotPreferences)
    def get_bot_preferences():
        return service.store.bot_preferences()

    @app.put(
        "/api/v1/app/bot-preferences",
        response_model=BotPreferences,
        responses={409: {"model": BotPreferences, "description": "Current preferences"}},
    )
    def put_bot_preferences(update: BotPreferencesUpdate, response: Response):
        applied, current = service.store.update_bot_preferences(update)
        if not applied:
            response.status_code = 409
        return current

    @app.get("/api/v1/settings", response_model=RuntimeSettings)
    async def get_settings():
        return service.store.settings

    @app.patch("/api/v1/settings", response_model=RuntimeSettings)
    async def patch_settings(patch: SettingsPatch):
        data = service.store.settings.model_dump() | patch.model_dump(exclude_unset=True)
        try:
            updated = RuntimeSettings.model_validate(data)
        except (ValueError, KeyError) as exc:
            raise ValueError("invalid_settings") from exc
        service.db.put("settings", updated.model_dump())
        service.apple.queue_alarm_changes()
        service.reconfigure()
        await service.apple.flush()
        return updated

    @app.get("/internal/tools", include_in_schema=False)
    async def tools():
        return [tool.model_dump(mode="json", exclude_none=True) for tool in await mcp.list_tools()]

    @app.post("/internal/tools/{name}", include_in_schema=False)
    async def invoke(name: str, arguments: dict):
        try:
            result = await mcp.call_tool(name, arguments)
            content, structured = result if isinstance(result, tuple) else (result, None)
            return jsonable_encoder(
                {"content": content, "structuredContent": structured, "isError": False}
            )
        except Exception as exc:
            # The Agent gets the reason (a fixed code such as source_excerpt_mismatch, or what
            # was wrong with its own arguments) so it can fix the call; nothing is logged.
            reason = str(exc)[:500] or type(exc).__name__
            return {
                "content": [{"type": "text", "text": f"tool_failed: {reason}"}],
                "isError": True,
            }

    app.include_router(knowledge_router(service))
    app.include_router(video_router(service))
    WebDAV(service).install(app)
    app.mount("/", mcp_app)
    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "ASSISTANT_TOKEN, or a device token with the app scope "
                "(health_upload is enough for POST /api/v1/health/samples); see docs/api.md.",
            }
        }
        schema["security"] = [{"BearerAuth": []}]
        return schema

    app.openapi = openapi
    return app
