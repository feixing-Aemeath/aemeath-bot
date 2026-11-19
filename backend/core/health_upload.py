"""Apply HealthKit deletions to uploads and their rebuildable analysis caches."""

import json


def upload(service, batch):
    if not batch.deleted:
        return service.store.health(batch)
    # Inside the function, like core's other plugin imports: core starts without any plugin.
    from plugins.health.config import HealthConfig
    from plugins.health.engine import DERIVED_DATE_COLUMNS, HealthEngine

    plugin = service.plugins.instances.get("health")
    engine = getattr(plugin, "engine", None)
    if engine is None:
        path = service.settings.data_dir / "health" / "observations.sqlite3"
        if not path.exists():
            return service.store.health(batch)
        rows = service.db.rows("SELECT config_json FROM plugins WHERE id='health'")
        config = json.loads(rows[0]["config_json"]) if rows else {}
        config.pop("reports", None)
        engine = HealthEngine(service.db, service.settings.data_dir, HealthConfig.from_dict(config))
    with engine.lock:
        result = service.store.health(batch)
        # Observations have no sample UUID. Rebuild only the upload origin so equal-valued
        # surviving UUIDs and imported Apple exports stay intact. Repeating this is safe.
        with engine.observations.connect() as conn:
            conn.execute(
                "DELETE FROM observations WHERE source_id IN "
                "(SELECT id FROM sources WHERE origin='upload')"
            )
        engine.put_kv("upload_cursor", 0)
        engine.ingest_uploads()
        with service.db.connect() as conn:
            for table, _ in DERIVED_DATE_COLUMNS:
                conn.execute(f"DELETE FROM {table}")
        engine.recompute()
        return result
