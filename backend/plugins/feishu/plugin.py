import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from core.plugins import Plugin as BasePlugin


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self):
        if not self.path.exists():
            raise RuntimeError("feishu_oauth_required")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temp, self.path)
        os.chmod(self.path, 0o600)


class OfficialFeishu:
    """All traffic uses the official lark-oapi SDK and first-party Feishu endpoints."""

    def __init__(self, tokens: TokenStore):
        import lark_oapi as lark

        self.lark, self.tokens = lark, tokens
        secrets_config = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
        self.app_id = os.environ.get("FEISHU_APP_ID") or secrets_config.get("FEISHU_APP_ID")
        self.app_secret = os.environ.get("FEISHU_APP_SECRET") or secrets_config.get(
            "FEISHU_APP_SECRET"
        )
        if not self.app_id or not self.app_secret:
            raise RuntimeError("feishu_credentials_required")
        self.client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .log_level(lark.LogLevel.CRITICAL)
            .build()
        )

    def oauth(self, body):
        lark = self.lark
        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.POST)
            .uri("/open-apis/authen/v2/oauth/token")
            .token_types(set())
            .body(
                {
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    **body,
                }
            )
            .build()
        )
        response = self.client.request(request)
        if response.raw.status_code != 200:
            raise RuntimeError("feishu_oauth_failed")
        result = json.loads(response.raw.content)
        if result.get("code", 0) != 0 or "access_token" not in result:
            raise RuntimeError("feishu_oauth_failed")
        if "refresh_token" not in result:
            result["refresh_token"] = self.tokens.read()["refresh_token"]
        result["expires_at"] = time.time() + result.get("expires_in", 7200)
        self.tokens.write(result)
        return result

    def token(self):
        data = self.tokens.read()
        if data["expires_at"] - time.time() < 120:
            data = self.oauth(
                {"grant_type": "refresh_token", "refresh_token": data["refresh_token"]}
            )
        return data["access_token"]

    def history(self, chat, start, end, page_token=None):
        from lark_oapi.api.im.v1 import ListMessageRequest

        builder = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat)
            .start_time(str(start))
            .end_time(str(end))
            .sort_type("ByCreateTimeAsc")
            .page_size(50)
        )
        if page_token:
            builder.page_token(page_token)
        response = self.client.im.v1.message.list(
            builder.build(),
            self.lark.RequestOption.builder().user_access_token(self.token()).build(),
        )
        if not response.success():
            # Never include SDK error text, tokens, or message contents in logs/errors.
            if response.code in {99991663, 99991668}:
                data = self.tokens.read()
                self.oauth({"grant_type": "refresh_token", "refresh_token": data["refresh_token"]})
            raise RuntimeError("feishu_history_failed")
        return json.loads(self.lark.JSON.marshal(response.data))


def extract_text(content, message_type):
    value = json.loads(content)
    if message_type == "text":
        return value.get("text", "")
    if message_type == "post":
        post = value.get("zh_cn", value.get("en_us", value))
        parts = [post.get("title", "")]
        for line in post.get("content", []):
            parts.append(
                "".join(
                    node.get("text", "@全体成员" if node.get("user_id") == "all" else "")
                    for node in line
                    if node.get("tag") in {"text", "a", "at"}
                )
            )
        return "\n".join(parts).strip()
    # No image download or cloud OCR. Unsupported attachments stay local to Feishu.
    return ""


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.client = OfficialFeishu(TokenStore(context.data_dir / "secrets" / "feishu.json"))
        self.pending = {}

    async def fetch(self):
        messages, self.pending = [], {}
        end = int(time.time())
        for chat in self.config.get("whitelist", []):
            start = self.context.get(
                f"cursor:{chat}", end - self.config.get("initial_lookback_hours", 24) * 3600
            )
            page_token = None
            for _ in range(self.config.get("max_pages", 20)):
                page = await asyncio.to_thread(
                    self.client.history, chat, max(0, start - 1), end, page_token
                )
                for item in page.get("items") or []:
                    if item.get("deleted"):
                        continue
                    text = extract_text(
                        item.get("body", {}).get("content", "{}"), item.get("msg_type")
                    )
                    if text:
                        messages.append(
                            {
                                "external_id": item["message_id"],
                                "chat_name": chat,
                                "sender": (item.get("sender") or {}).get("id", "unknown"),
                                "sent_at": datetime.fromtimestamp(
                                    int(item["create_time"]) / 1000, UTC
                                ),
                                "text": text,
                                "priority": any(
                                    m.get("id") == "all" for m in item.get("mentions") or []
                                ),
                            }
                        )
                if not page.get("has_more"):
                    self.pending[chat] = end
                    break
                page_token = page.get("page_token")
                if not page_token:
                    raise RuntimeError("feishu_pagination_missing")
            else:
                # Never advance past an incomplete window. Operator can narrow the lookback.
                raise RuntimeError("feishu_page_limit")
        return messages

    async def commit_fetch(self):
        for chat, value in self.pending.items():
            self.context.put(f"cursor:{chat}", value)
        self.pending = {}

    async def cards(self):
        return [
            {
                "type": "stat",
                "title": "飞书白名单",
                "data": {"count": len(self.config.get("whitelist", []))},
            }
        ]
