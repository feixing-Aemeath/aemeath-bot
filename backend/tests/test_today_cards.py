"""/today carries every plugin's cards, each marked with its plugin, worked out at once."""

import asyncio
import time

from core.models import PluginPatch
from tests.test_api import client_for


async def test_cards_name_their_plugin(service):
    await service.plugins.discover()
    await service.plugins.patch("weather", PluginPatch(enabled=True))
    cards = await service.plugins.cards("weather")
    assert cards and {card["plugin"] for card in cards} == {"weather"}
    with client_for(service) as client:
        today = client.get("/api/v1/today").json()["cards"]
        assert [c["title"] for c in today if c["plugin"] == "weather"] != []
        assert {c["plugin"] for c in client.get("/api/v1/plugins/weather/cards").json()} == {
            "weather"
        }


async def test_plugins_cards_are_worked_out_at_once(service, monkeypatch):
    await service.plugins.discover()
    names = [plugin["id"] for plugin in service.plugins.list()][:4]
    assert len(names) == 4

    async def slow(identifier):
        await asyncio.sleep(0.3)
        if identifier not in names:
            return []
        return [
            {"type": "text", "title": "同名", "data": {"text": identifier}, "plugin": identifier}
        ]

    monkeypatch.setattr(service.plugins, "cards", slow)
    with client_for(service) as client:
        started = time.monotonic()
        cards = client.get("/api/v1/today").json()["cards"]
        elapsed = time.monotonic() - started
    # In the plugins' order, and two plugins' cards of the same type and title both kept.
    assert [card["plugin"] for card in cards] == names
    assert elapsed < 0.3 * 2
