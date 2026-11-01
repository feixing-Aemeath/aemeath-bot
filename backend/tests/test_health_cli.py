import json
import re
import zipfile

import jsonschema
import pytest

from scripts import health_import
from scripts.health_synthetic import write_export


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    path = tmp_path_factory.mktemp("cli") / "apple_health_export" / "export.xml"
    write_export(path, days=40)
    return path


def test_import_prints_last_14_days_and_alerts(export, tmp_path, capsys):
    data_dir = tmp_path / "state"
    assert health_import.main([str(export), "--data-dir", str(data_dir)]) == 0
    captured = capsys.readouterr()
    rows = [line for line in captured.out.splitlines() if re.match(r"\d{4}-\d{2}-\d{2} ", line)]
    assert len(rows) == 14 and rows[-1].startswith("2026-10-12")
    assert "resting_hr_high" in rows[-1] and "hrv_low" in rows[-1]
    assert "静息心率已连续 3 天明显高于本人近 30 天基线" in captured.out
    assert "SRI" in captured.out and "ACWR" in captured.out and "争议" in captured.out
    assert "示例用户" not in captured.out + captured.err
    assert "导入完成" in captured.err
    assert (data_dir / "assistant.sqlite3").exists()
    assert (data_dir / "health" / "observations.sqlite3").exists()
    # Running it again inserts nothing new and prints the same table.
    assert health_import.main([str(export), "--data-dir", str(data_dir)]) == 0
    again = capsys.readouterr()
    assert "新增 0 条观测" in again.err and again.out == captured.out


def test_zip_json_and_dry_run_leave_no_files(export, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.write(export, "apple_health_export/export.xml")
    assert health_import.main([str(archive), "--dry-run", "--json", "--days", "3"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"daily", "sleep", "baselines"}
    assert [d["date"] for d in payload["daily"]["days"]] == [
        "2026-10-10",
        "2026-10-11",
        "2026-10-12",
    ]
    assert list(tmp_path.iterdir()) == [archive]


def test_config_file_is_validated_and_max_hr_overrides(export, tmp_path, capsys):
    config = tmp_path / "health.json"
    config.write_text(json.dumps({"wear": {"min_hours": 99}}), encoding="utf-8")
    with pytest.raises(jsonschema.ValidationError):
        health_import.main([str(export), "--dry-run", "--config", str(config)])
    config.write_text(json.dumps({"sleep": {"day_cutoff_hour": 20}}), encoding="utf-8")
    arguments = [str(export), "--dry-run", "--json", "--config", str(config), "--max-hr", "195"]
    assert health_import.main(arguments) == 0
    load = json.loads(capsys.readouterr().out)["daily"]["training_load"]
    assert load["hr_max"] == {"value": 195, "source": "config"}
    with pytest.raises(SystemExit):
        health_import.main([str(tmp_path / "missing.xml")])


def test_truncated_export_is_reported_not_crashed(export, tmp_path, capsys):
    broken = tmp_path / "export.xml"
    broken.write_bytes(export.read_bytes()[:200_000])
    assert health_import.main([str(broken), "--dry-run"]) == 2
    assert "不是完整的 XML" in capsys.readouterr().err
