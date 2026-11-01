import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import windows_service
from scripts.doctor import Report, Result
from scripts.windows_doctor import SETTINGS, TASK, check_windows, power_timeouts, powershell


def test_service_config_and_log_redirection_do_not_expose_token(tmp_path):
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=" + "x" * 40, encoding="utf-8")
    config = windows_service.configuration(tmp_path)
    assert "token" not in json.dumps(config)
    assert config["logs"] == str(tmp_path / ".state/logs")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].write(b"started\n")
        kwargs["stderr"].write(b"failed\n")
        assert kwargs["stdin"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=7)

    assert windows_service.run(config, runner=run) == 7
    assert calls[0][0] == [sys.executable, "-X", "utf8", "-m", "core"]
    assert calls[0][1]["env"]["PYTHONUTF8"] == "1"
    assert (tmp_path / ".state/logs/service-error.log").read_bytes() == b"failed\n"


def test_service_check_validates_without_starting_or_creating_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(windows_service, "configuration", lambda: {"host": "127.0.0.1"})
    monkeypatch.setattr(windows_service, "run", lambda _: pytest.fail("must not start"))
    assert windows_service.main(["--check"]) == 0
    assert "127.0.0.1" in capsys.readouterr().out
    assert not (tmp_path / ".state").exists()


class Probe:
    platform = "win32"

    def __init__(self, backend):
        self.backend = backend
        self.timeouts = (0, 0)
        self.settings = {"processes": ["QQ", "Weixin"], "screen_saver_active": "0"}
        self.ocr = {"languages": ["en-US", "zh-Hans"], "locked": False}
        self.task = {
            "state": "Running",
            "interactive": "Interactive",
            "restart_count": 999,
            "working_directory": str(backend),
        }
        self.calls = []

    def run(self, argv):
        self.calls.append(argv)
        if argv[0] == "powercfg.exe":
            return Result(0, "\n".join(f"当前索引: 0x{n:08x}" for n in self.timeouts))
        data = (
            self.settings
            if argv == powershell(SETTINGS)
            else self.task
            if argv == powershell(TASK)
            else self.ocr
        )
        return Result(0, json.dumps(data))


def test_windows_doctor_checks_native_settings_without_changing_them(tmp_path):
    probe = Probe(tmp_path)
    report = Report(probe)
    check_windows(report, tmp_path)
    checks = {check.name: check for check in report.checks}
    for name in (
        "windows_sleep",
        "windows_display",
        "windows_ocr",
        "windows_desktop",
        "windows_task",
    ):
        assert checks[name].status == "ok"
    assert checks["windows_auto_login"].status == "manual"
    assert checks["qq_login"].status == "manual"
    assert all("/change" not in call for call in probe.calls)
    assert power_timeouts("当前交流电设置索引: 0x00000000\n当前直流电设置索引: 0x0000003c") == (
        0,
        60,
    )


def test_windows_doctor_reports_missing_ocr_locked_screen_and_sleep(tmp_path):
    probe = Probe(tmp_path)
    probe.timeouts = (60, 120)
    probe.ocr, probe.task, probe.settings = {}, {}, {"dynamic_lock": 1}
    report = Report(probe)
    check_windows(report, tmp_path)
    checks = {check.name: check for check in report.checks}
    for name in (
        "windows_sleep",
        "windows_display",
        "windows_ocr",
        "windows_desktop",
        "windows_task",
        "windows_lock",
        "qq_login",
    ):
        assert checks[name].status == "fail"


@pytest.mark.skipif(sys.platform != "win32", reason="WinRT ABI smoke test on native Windows CI")
def test_windows_dependency_projections_load():
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import StorageFile

    assert Language("zh-Hans").language_tag == "zh-Hans"
    assert OcrEngine.max_image_dimension > 0
    assert BitmapDecoder and StorageFile


def test_windows_asr_uses_a_local_model_and_preserves_cli_protocol(tmp_path):
    from scripts.windows_transcribe import parser, transcribe

    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "model.bin").touch()
    args = parser().parse_args(
        [
            str(tmp_path / "音频.wav"),
            "--model",
            str(model),
            "--output-dir",
            str(tmp_path),
            "--output-format",
            "json",
            "--verbose",
            "False",
        ]
    )
    calls = []

    def factory(path, **kwargs):
        calls.append((path, kwargs))
        return SimpleNamespace(
            transcribe=lambda *args, **kwargs: ([SimpleNamespace(start=1, text="本地音频")], None)
        )

    transcribe(args, factory)
    assert calls[0][1] == {"device": "cpu", "compute_type": "int8", "local_files_only": True}
    assert json.loads((tmp_path / "音频.json").read_text(encoding="utf-8")) == {
        "segments": [{"start": 1, "text": "本地音频"}]
    }
