import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest
import yaml
from mcp.server.fastmcp.exceptions import ToolError

from core.mcp_server import create_mcp
from core.models import HealthBatch, PluginPatch
from plugins.health.config import DERIVATION, RULE_DEFAULTS, HealthConfig
from plugins.health.metrics import METRICS, VIRTUAL_METRICS
from plugins.health.storage import read_days
from tests.test_api import client_for

HEALTH_TOOLS = {"health_get_daily", "health_get_sleep", "health_get_baselines"}
MANIFEST = Path(__file__).resolve().parents[1] / "plugins" / "health" / "manifest.yaml"


def night_samples():
    """One night, a resting HR and a few readings, as the iPhone App would upload them."""
    samples = []
    stages = [
        ("2026-10-11T15:20:00Z", "2026-10-11T17:00:00Z", "sleep_core"),
        ("2026-10-11T17:00:00Z", "2026-10-11T18:10:00Z", "sleep_deep"),
        ("2026-10-11T18:10:00Z", "2026-10-11T18:15:00Z", "sleep_awake"),
        ("2026-10-11T18:15:00Z", "2026-10-11T19:30:00Z", "sleep_rem"),
        ("2026-10-11T19:30:00Z", "2026-10-11T23:05:00Z", "sleep_core"),
    ]
    for index, (start, end, kind) in enumerate(stages):
        samples.append(
            dict(
                id=f"night-{index}", type=kind, value=1, unit="", start_at=start, end_at=end,
                source="示例用户的Apple Watch",
            )
        )  # fmt: skip
    samples.append(
        dict(
            id="rhr", type="resting_heart_rate", value=55, unit="count/min",
            start_at="2026-10-11T16:05:00Z", end_at="2026-10-12T15:50:00Z",
            source="示例用户的Apple Watch",
        )
    )  # fmt: skip
    return samples


async def tool(mcp, name, arguments):
    result = await mcp.call_tool(name, {"arguments": arguments})
    return json.loads(result[0].text)


async def test_health_plugin_lifecycle_tools_and_upload_job(service):
    mcp = create_mcp(service)
    await service.start()
    try:
        assert service.plugins.view("health")["state"] == "disabled"
        assert not HEALTH_TOOLS & {t.name for t in await mcp.list_tools()}
        await service.plugins.patch("health", PluginPatch(enabled=True))
        tools = {t.name: t for t in await mcp.list_tools()}
        assert HEALTH_TOOLS <= set(tools) and len(tools) == 17
        assert "not a diagnosis" in tools["health_get_daily"].description
        assert "plugin:health:refresh" in service.jobs.callbacks

        service.store.health(HealthBatch.model_validate({"samples": night_samples()}))
        await service.jobs.submit("plugin:health:refresh")
        row = read_days(service.db)[date(2026, 10, 12)]
        assert row["metrics"]["sleep_minutes"]["value"] == 460  # awake minutes excluded
        assert row["metrics"]["resting_hr"] == {"value": 55, "quality": "ok", "method": "apple"}

        sleep = await tool(mcp, "health_get_sleep", {"date": "2026-10-12", "days": 1})
        night = sleep["nights"][0]
        assert (night["onset"], night["wake"], night["asleep_minutes"]) == ("23:20", "07:05", 460)
        daily = await tool(mcp, "health_get_daily", {"date": "2026-10-12"})
        assert [d["date"] for d in daily["days"]] == ["2026-10-11", "2026-10-12"]
        baselines = await tool(mcp, "health_get_baselines", {})
        assert set(baselines["metrics"]) == set(METRICS)
        for bad in ({"days": 0}, {"days": "7"}, {"date": "yesterday"}, {"limit": 5}):
            with pytest.raises(ToolError):
                await mcp.call_tool("health_get_sleep", {"arguments": bad})
        assert any(r["error"] == "plugin_tool_failed" for r in service.store.runs()["items"])

        await service.plugins.patch("health", PluginPatch(enabled=False))
        assert not HEALTH_TOOLS & {t.name for t in await mcp.list_tools()}
        assert "plugin:health:refresh" not in service.jobs.callbacks
    finally:
        await service.close()


def test_health_config_is_validated_over_http(service):
    with client_for(service) as client:
        assert client.patch("/api/v1/plugins/health", json={"config": {"x": 1}}).status_code == 422
        bad_rule = {"alerts": [{"id": "Bad Id", "metric": "resting_hr", "direction": "up"}]}
        assert client.patch("/api/v1/plugins/health", json={"config": bad_rule}).status_code == 422
        broken = {"enabled": True, "config": {"timezone": "Mars/Olympus_Mons"}}
        assert client.patch("/api/v1/plugins/health", json=broken).json()["state"] == "error"
        good = {"enabled": True, "config": {"training": {"max_hr": 192}, "wear": {"min_hours": 10}}}
        view = client.patch("/api/v1/plugins/health", json=good).json()
        assert view["state"] == "enabled" and view["config"]["training"]["max_hr"] == 192
        tools = {t["name"] for t in client.get("/internal/tools").json()}
        assert HEALTH_TOOLS <= tools
        result = client.post(
            "/internal/tools/health_get_daily", json={"arguments": {"date": "2026-10-12"}}
        ).json()
        assert result["isError"] is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["training_load"]["hr_max"] == {"value": 192, "source": "config"}


def test_manifest_schema_matches_code_defaults():
    schema = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["config_schema"]
    jsonschema.Draft202012Validator.check_schema(schema)
    properties = schema["properties"]
    rule_metrics = properties["alerts"]["items"]["properties"]["metric"]["enum"]
    scored = {key for key, metric in METRICS.items() if metric.scored}
    assert set(rule_metrics) == scored | set(VIRTUAL_METRICS)
    floors = properties["baseline"]["properties"]["mad_floors"]["properties"]
    assert set(floors) == set(METRICS)
    defaults = HealthConfig()
    assert properties["timezone"]["default"] == defaults.timezone == "Asia/Shanghai"
    assert properties["wear"]["properties"]["min_hours"]["default"] == defaults.min_wear_hours
    sleep = properties["sleep"]["properties"]
    assert sleep["day_cutoff_hour"]["default"] == defaults.sleep_day_cutoff_hour
    assert sleep["segment_gap_minutes"]["default"] == defaults.sleep_segment_gap_minutes
    assert sleep["night_gap_minutes"]["default"] == defaults.sleep_night_gap_minutes
    assert sleep["min_minutes"]["default"] == defaults.min_sleep_minutes
    baseline = properties["baseline"]["properties"]
    assert baseline["min_days_7d"]["default"] == defaults.baseline_min_days[7]
    assert baseline["min_days_30d"]["default"] == defaults.baseline_min_days[30]
    assert baseline["notable_z"]["default"] == defaults.notable_z
    assert baseline["strong_z"]["default"] == defaults.strong_z
    sri = properties["sri"]["properties"]
    assert sri["min_pairs_7d"]["default"] == defaults.sri_min_pairs[7]
    assert sri["min_pairs_30d"]["default"] == defaults.sri_min_pairs[30]
    rule = properties["alerts"]["items"]["properties"]
    for key in ("baseline", "z", "min_delta", "days", "enabled"):
        assert rule[key]["default"] == RULE_DEFAULTS[key]
    readiness = properties["readiness"]["properties"]
    weights = readiness["weights"]["properties"]
    assert {k: v["default"] for k, v in weights.items()} == defaults.readiness.weights
    for key in ("min_components", "z_full", "z_zero", "sleep_target_hours", "sleep_zero_hours",
                "acwr_full", "acwr_zero"):  # fmt: skip
        assert readiness[key]["default"] == getattr(defaults.readiness, key)


def test_config_merging_validation_and_fingerprint():
    config = HealthConfig.from_dict(
        {"baseline": {"mad_floors": {"resting_hr": 2}}, "sri": {"min_pairs_7d": 5}}
    )
    assert config.mad_floors["resting_hr"] == 2 and config.mad_floors["steps"] == 500
    assert config.sri_min_pairs == {7: 5, 30: 14}
    assert [rule["id"] for rule in config.alert_rules][:2] == ["resting_hr_high", "hrv_low"]
    for bad in (
        {"timezone": "Mars/Olympus_Mons"},
        {"source_priority": ["watch", "watch", "phone"]},
        {"baseline": {"notable_z": 3, "strong_z": 2}},
        {"training": {"max_hr": 100, "resting_hr": 90}},
        {"alerts": [{"id": "a", "metric": "steps", "direction": "below"}] * 2},
    ):
        with pytest.raises((KeyError, ValueError)):
            HealthConfig.from_dict(bad)
    base = HealthConfig().derived_fingerprint()
    assert HealthConfig.from_dict({"baseline": {"notable_z": 1}}).derived_fingerprint() == base
    assert HealthConfig.from_dict({"timezone": "UTC"}).derived_fingerprint() != base
    assert HealthConfig.from_dict({"training": {"max_hr": 190}}).derived_fingerprint() != base
    # A new derivation version rebuilds the stored history like a settings change.
    with patch("plugins.health.config.DERIVATION", DERIVATION + 1):
        assert HealthConfig().derived_fingerprint() != base
