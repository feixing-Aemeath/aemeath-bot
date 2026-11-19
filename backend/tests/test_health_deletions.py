from datetime import date

import pytest

from plugins.health.config import HealthConfig
from plugins.health.engine import HealthEngine
from plugins.health.storage import read_days
from tests.test_api import client_for


def sample(identifier="S"):
    return {
        "id": identifier,
        "type": "step_count",
        "value": 42,
        "unit": "count",
        "source": "Apple Watch",
        "start_at": "2026-11-05T01:00:00Z",
        "end_at": "2026-11-05T02:00:00Z",
    }


def test_deleted_health_sample_is_removed_from_storage_and_analysis(service):
    with client_for(service) as client:
        assert (
            client.post("/api/v1/health/samples", json={"samples": [sample()]}).status_code == 200
        )
        engine = HealthEngine(service.db, service.settings.data_dir, HealthConfig())
        engine.refresh()
        assert read_days(service.db)[date(2026, 11, 5)]["metrics"]["steps"]["value"] == 42
        for _ in range(2):
            response = client.post("/api/v1/health/samples", json={"deleted": ["S"]})
            assert response.status_code == 200, response.text
        assert service.db.rows("SELECT * FROM health_samples") == []
        engine.refresh()
        assert engine.observations.bounds() == {}
        assert read_days(service.db) == {}
        # Deleting the maximum SQLite rowid must not make the next upload invisible.
        client.post("/api/v1/health/samples", json={"samples": [sample("next")]})
        engine.refresh()
        assert read_days(service.db)[date(2026, 11, 5)]["metrics"]["steps"]["value"] == 42


def test_health_deletion_preserves_other_samples_and_export_observations(service):
    with client_for(service) as client:
        client.post("/api/v1/health/samples", json={"samples": [sample(), sample("same")]})
        engine = HealthEngine(service.db, service.settings.data_dir, HealthConfig())
        engine.refresh()
        engine.observations.add(
            [("step_count", 1793754000, 1793757600, 50, "", "export watch", "watch")], "export"
        )
        response = client.post(
            "/api/v1/health/samples", json={"samples": [], "deleted": ["S", "missing"]}
        )
        assert response.status_code == 200, response.text
        assert [r["id"] for r in service.db.rows("SELECT id FROM health_samples")] == ["same"]
        with engine.observations.connect() as conn:
            assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 2


@pytest.mark.parametrize(
    "body",
    [{}, {"samples": []}, {"deleted": []}, {"deleted": [""]}, {"deleted": ["x"] * 1001}],
)
def test_health_deletion_rejects_empty_or_invalid_batches(service, body):
    with client_for(service) as client:
        assert client.post("/api/v1/health/samples", json=body).status_code == 422
