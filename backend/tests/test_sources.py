import time
from types import SimpleNamespace

import pytest

from core.plugins import PluginContext
from plugins.feishu.plugin import OfficialFeishu, Plugin, TokenStore, extract_text


async def test_feishu_pagination_and_commit_only_after_ingest(service):
    class SDK:
        calls = []

        def history(self, chat, start, end, page_token):
            self.calls.append((chat, page_token))
            return {
                "items": [
                    {
                        "message_id": page_token or "m1",
                        "create_time": "1791673200000",
                        "sender": {"id": "teacher"},
                        "msg_type": "text",
                        "body": {"content": '{"text":"通知"}'},
                    }
                ],
                "has_more": not page_token,
                "page_token": "p2" if not page_token else None,
            }

    plugin = Plugin()
    plugin.context = PluginContext(service.store, service.settings.data_dir, "feishu")
    plugin.config = {"whitelist": ["allowed"]}
    plugin.client = SDK()
    messages = await plugin.fetch()
    assert plugin.context.get("cursor:allowed") is None
    assert len(messages) == 2
    service.store.ingest(
        "feishu",
        messages=[
            __import__("core.models", fromlist=["MessageInput"]).MessageInput.model_validate(m)
            for m in messages
        ],
        whitelist=["allowed"],
    )
    await plugin.commit_fetch()
    assert plugin.context.get("cursor:allowed")
    assert plugin.client.calls == [("allowed", None), ("allowed", "p2")]


async def test_feishu_page_limit_never_advances_cursor(service):
    plugin = Plugin()
    plugin.context = PluginContext(service.store, service.settings.data_dir, "feishu")
    plugin.config = {"whitelist": ["allowed"], "max_pages": 1}
    plugin.client = SimpleNamespace(
        history=lambda *args: {"items": [], "has_more": True, "page_token": "more"}
    )
    with pytest.raises(RuntimeError, match="page_limit"):
        await plugin.fetch()
    assert plugin.context.get("cursor:allowed") is None


def test_official_sdk_request_shape_and_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "test-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    tokens = TokenStore(tmp_path / "tokens.json")
    tokens.write(
        {"access_token": "expired", "refresh_token": "refresh", "expires_at": time.time() - 1}
    )
    api = OfficialFeishu(tokens)
    requests = []

    def request(req):
        requests.append(req)
        return SimpleNamespace(
            raw=SimpleNamespace(
                status_code=200,
                content=b'{"code":0,"access_token":"new","refresh_token":"rotated","expires_in":7200}',
            )
        )

    monkeypatch.setattr(api.client, "request", request)
    assert api.token() == "new"
    assert requests[0].uri == "/open-apis/authen/v2/oauth/token"
    assert requests[0].body["grant_type"] == "refresh_token"
    assert tokens.read()["refresh_token"] == "rotated"
    captured = []

    def list_messages(req, option):
        captured.append((req, option))
        return SimpleNamespace(success=lambda: True, data={"items": [], "has_more": False})

    monkeypatch.setattr(api.client.im.v1.message, "list", list_messages)
    assert api.history("oc_test", 1, 2) == {"items": [], "has_more": False}
    assert captured[0][1].user_access_token == "new"


def test_post_ocr_text_extraction():
    assert (
        extract_text(
            '{"zh_cn":{"title":"公告","content":[[{"tag":"text","text":"正文"}]]}}', "post"
        )
        == "公告\n正文"
    )
    assert extract_text('{"image_key":"private"}', "image") == ""
