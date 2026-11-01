import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from core.apple import FakeAppleBridge
from core.config import Settings
from core.documents import FakeExtractor
from core.models import MessageInput, TodoCreate
from core.service import Service
from harness import deepseek
from plugins.token_usage import limits
from plugins.weather import forecast


class RecordingAppleBridge(FakeAppleBridge):
    def __init__(self, directory):
        super().__init__(directory)
        self.calls = []

    async def call(self, resource, action, payload):
        self.calls.append((resource, action, payload.copy()))
        return await super().call(resource, action, payload)


@pytest.fixture(autouse=True)
def windows_symlink_privilege(monkeypatch):
    if os.name != "nt":
        return
    original = Path.symlink_to

    def link(path, target, target_is_directory=False):
        try:
            return original(path, target, target_is_directory=target_is_directory)
        except OSError as error:
            if error.winerror == 1314:
                pytest.skip("Windows symlink creation requires Developer Mode or privilege")
            raise

    monkeypatch.setattr(Path, "symlink_to", link)


@pytest.fixture(autouse=True)
def no_real_limits(monkeypatch):
    """Enabling token_usage reads Claude's and Codex's limits; never the real codex, Keychain
    or Anthropic from a test. Tests of limits.py call its functions with fakes."""

    def offline(*_args, **_kwargs):
        raise limits.LimitError("offline_in_tests")

    monkeypatch.setattr(limits, "read_codex", offline)
    monkeypatch.setattr(limits, "read_claude", offline)
    monkeypatch.setattr(limits, "renew_claude", lambda *_args: False)  # never the real claude


@pytest.fixture(autouse=True)
def no_real_deepseek_or_weather(monkeypatch):
    """Never the real DeepSeek key (Keychain, environment) or the real weather service from a
    test; their tests hand in fakes."""
    monkeypatch.setattr(deepseek, "keychain_key", lambda *_args: None)
    monkeypatch.delenv("ASSISTANT_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ASSISTANT_DEEPSEEK_MODEL", raising=False)

    def offline(*_args, **_kwargs):
        raise httpx.ConnectError("offline_in_tests")

    monkeypatch.setattr(forecast, "http_get", offline)


@pytest.fixture
def service(tmp_path):
    instance = Service(
        Settings(
            token="test-token-000000000000000000000000",
            data_dir=tmp_path / "state",
            scheduler_enabled=False,
            _env_file=None,
        ),
        platform="linux",
    )

    instance.memory.documents.extractor = FakeExtractor()
    return instance


@pytest.fixture
def seeded(service):
    for plugin in ("qq_screen", "feishu"):
        service.store.ingest(
            plugin,
            [
                MessageInput(
                    external_id=f"{plugin}-1",
                    chat_name="学校",
                    sender="老师",
                    sent_at=datetime(2026, 10, 11, 1, tzinfo=UTC),
                    text="请在明天18点前提交报名表",
                    screenshot_path="private/screenshot.png",
                )
            ],
            ["学校"],
        )
    return service


def candidate(service, plugin="qq_screen", **changes):
    row = service.store.unprocessed(plugin)[0]
    values = dict(
        title="提交报名表",
        due_at="2026-10-12T18:00:00+08:00",
        has_time=True,
        source_plugin=plugin,
        source_refs=[row["id"]],
        source_excerpt="请在明天18点前提交报名表",
        confidence=0.95,
    )
    values.update(changes)
    return TodoCreate.model_validate(values)
