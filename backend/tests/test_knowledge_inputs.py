"""Two knowledge inputs with synthetic files, fake PhotoKit/OCR and fake timetables."""

import os
from datetime import datetime, timedelta

import httpx
import pytest

from core.api import create_app
from core.documents import MAX_FILE, MAX_PDF, MAX_TEXT, FakeExtractor, MacExtractor
from plugins.class_photos.photos import FakePhotoLibrary, MacPhotoLibrary
from plugins.class_photos.plugin import Plugin as Photos
from plugins.goodnotes.plugin import Plugin as GoodNotes
from scripts.doctor import Report, Result, check_photo_export

NOW = datetime.fromisoformat("2026-10-29T20:00:00+08:00")


@pytest.fixture
async def goodnotes(service):
    plugin = GoodNotes()
    await plugin.setup(service.plugins.context("goodnotes"), {})
    plugin.now = lambda: NOW
    return plugin


def backup(plugin, name, content=b"pdf", age=180):
    path = plugin.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    stamp = (NOW - timedelta(seconds=age)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


async def test_goodnotes_matching_longest_mapping_wait_and_format(service, goodnotes):
    service.knowledge.topic("数据结构")
    service.knowledge.topic("Math")
    for name in (" 数据结构/第5章.pdf", " math.PDF", "未知.pdf", "native.goodnotes", "fresh.pdf"):
        backup(goodnotes, name, age=10 if name == "fresh.pdf" else 180)
    goodnotes.config["mapping"] = {"other/": "总类", "other/deep/": "专类"}
    backup(goodnotes, "other/deep/a.pdf")
    backup(goodnotes, "other/b.pdf")
    await goodnotes.scan()
    state = {n["path"]: n for n in goodnotes.status()["notebooks"]}
    assert state[" 数据结构/第5章.pdf"]["topic"] == "数据结构"
    assert state[" math.PDF"]["topic"] == "Math"
    assert state["other/deep/a.pdf"]["topic"] == "专类"
    assert state["other/b.pdf"]["topic"] == "总类"
    assert state["未知.pdf"]["status"] == "unmapped"
    assert state["fresh.pdf"]["status"] == "waiting"
    assert state["native.goodnotes"]["status"] == "wrong_format"
    assert "PDF" in state["native.goodnotes"]["message"]
    assert (
        service.knowledge.access.settings["default_readers"]
        == next(a for a in service.knowledge.access.discover() if a["name"] == "专类")["readers"]
    )
    assert service.knowledge.source("知识库/专类/GoodNotes/a.pdf")["status"] == "pending"


async def test_goodnotes_replacement_conflict_missing_and_unowned(service, goodnotes):
    service.knowledge.topic("课程")
    source = backup(goodnotes, "课程/笔记.pdf", b"first")
    await goodnotes.scan()
    target = service.memory.root / "知识库/课程/GoodNotes/笔记.pdf"
    assert target.read_bytes() == b"first"
    backup(goodnotes, "课程/笔记.pdf", b"second")
    await goodnotes.scan()
    assert target.read_bytes() == b"second"
    target.write_bytes(b"user edit")
    backup(goodnotes, "课程/笔记.pdf", b"third")
    await goodnotes.scan()
    assert target.read_bytes() == b"user edit"
    assert goodnotes.status()["notebooks"][0]["status"] == "conflict"
    source.unlink()
    await goodnotes.scan()
    assert target.read_bytes() == b"user edit"
    assert goodnotes.status()["notebooks"][0]["status"] == "missing"
    # Same title in another folder, or a user's pre-existing file, never becomes owned.
    goodnotes.config["mapping"] = {"else/": "课程"}
    backup(goodnotes, "else/笔记.pdf", b"other")
    await goodnotes.scan()
    assert target.read_bytes() == b"user edit"
    assert (
        next(n for n in goodnotes.status()["notebooks"] if n["path"] == "else/笔记.pdf")["status"]
        == "conflict"
    )


async def test_goodnotes_wait_then_copy_and_validate_topics(service, goodnotes):
    service.knowledge.topic("课程")
    source = backup(goodnotes, "课程/笔记.pdf", age=119)
    await goodnotes.scan()
    assert goodnotes.status()["notebooks"][0]["status"] == "waiting"
    stamp = (NOW - timedelta(seconds=120)).timestamp()
    os.utime(source, (stamp, stamp))
    await goodnotes.scan()
    assert goodnotes.status()["notebooks"][0]["status"] == "copied"
    for name in ("../逃逸", "整理", ".hidden", "bad\nname"):
        with pytest.raises(ValueError):
            await GoodNotes().setup(service.plugins.context("goodnotes"), {"mapping": {"": name}})


async def test_goodnotes_skips_symlinks_and_never_overwrites_user_file(
    service, goodnotes, tmp_path
):
    service.knowledge.topic("课程")
    backup(goodnotes, "课程/a.pdf")
    folder = service.memory.root / "知识库/课程/GoodNotes"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"user original")
    (goodnotes.root / "link.pdf").symlink_to(tmp_path / "private.pdf")
    await goodnotes.scan()
    assert (folder / "a.pdf").read_bytes() == b"user original"
    assert len(goodnotes.status()["notebooks"]) == 1
    assert goodnotes.status()["notebooks"][0]["status"] == "conflict"


class Timetable:
    def __init__(self, known=True, estimated=True):
        self.known, self.estimated = known, estimated
        self.calls = []

    async def timetable_days(self, arguments):
        self.calls.append(arguments)
        return {
            "days": [
                {
                    "date": "2026-10-29",
                    "known": self.known,
                    "estimated_from": "2026-10-19" if self.estimated else None,
                    "entries": [
                        {
                            "kind": "课程",
                            "title": "高等数学I（推测）",
                            "start": "08:00",
                            "end": "09:35",
                        },
                        {"kind": "课程", "title": "体育", "start": "10:00", "end": "11:35"},
                        {"kind": "自己的日程", "title": "散步", "start": "12:00", "end": "13:00"},
                    ],
                }
            ]
        }


class TextExtractor:
    def extract(self, path):
        return {"text": path.read_text()}


@pytest.fixture
async def photos(service):
    plugin = Photos()
    await plugin.setup(
        service.plugins.context("class_photos"),
        {"course_topics": {"高等数学I": "高数"}, "skip_courses": ["体育"]},
    )
    plugin.now = lambda: NOW
    plugin.photos = FakePhotoLibrary()
    service.plugins.instances["timetable"] = Timetable()
    service.memory.documents.extractor = TextExtractor()
    return plugin


def add_photo(
    plugin,
    tmp_path,
    name,
    clock,
    text="这是课堂上拍下的高等数学课件和板书的识别文字",
    day="2026-10-29",
):
    identifier = f"{name:0>8}/L0/001"
    source = tmp_path / f"{name}.heic"
    source.write_text(text + (str(name) if text else ""))
    plugin.photos.items.append(
        {
            "id": identifier,
            "created_at": f"{day}T{clock}+08:00",
            "is_screenshot": False,
            "width": 1000,
            "height": 2000,
        }
    )
    plugin.photos.files[identifier] = source
    return identifier


@pytest.mark.parametrize(
    "clock,in_class",
    [
        ("07:54:59", False),
        ("07:55:00", True),
        ("08:00:00", True),
        ("09:35:00", True),
        ("09:45:00", True),
        ("09:45:01", False),
        ("12:30:00", False),
    ],
)
async def test_photo_time_boundaries_and_metadata_only(photos, tmp_path, clock, in_class):
    identifier = add_photo(photos, tmp_path, "00000001", clock)
    await photos.scan()
    assert photos.photos.exports == ([identifier] if in_class else [])
    rows = photos.db.rows("SELECT * FROM class_photos_files")
    assert rows[0]["status"] == ("copied" if in_class else "outside_class")
    if in_class:
        assert rows[0]["estimated_from"] == "2026-10-19"
        assert rows[0]["course"] == "高等数学I" and rows[0]["topic"] == "高数"
    assert not list(photos.staging.iterdir())


async def test_photo_rejected_skipped_and_once_including_user_deleted(photos, service, tmp_path):
    identifier = add_photo(photos, tmp_path, "one", "08:10:00")
    add_photo(photos, tmp_path, "short", "08:20:00", "短")
    add_photo(photos, tmp_path, "empty", "08:25:00", "")
    skipped = add_photo(photos, tmp_path, "sport", "10:10:00")
    await photos.scan()
    rows = {r["id"]: r for r in photos.db.rows("SELECT * FROM class_photos_files")}
    assert rows[identifier]["status"] == "copied"
    assert rows[skipped]["status"] == "skipped_course" and skipped not in photos.photos.exports
    assert (await photos.status())["skipped_not_slides"] == 2
    output = service.memory.root / rows[identifier]["output_path"]
    assert output.suffix == ".heic" and output.exists()
    assert service.knowledge.source(rows[identifier]["output_path"])["status"] == "pending"
    output.unlink()
    service.plugins.context("class_photos").put("cursor", (NOW - timedelta(days=3)).isoformat())
    await photos.scan()
    assert photos.photos.exports.count(identifier) == 1 and not output.exists()
    assert not list(photos.staging.iterdir())


@pytest.mark.parametrize("permission", ["denied", "not_determined", "restricted", "unavailable"])
async def test_photo_without_permission_has_status_and_does_not_read(photos, permission, tmp_path):
    add_photo(photos, tmp_path, "private", "08:00:00")
    photos.photos.permission = permission
    await photos.scan()
    assert (await photos.status())["permission"] == permission
    assert photos.photos.exports == [] and photos.context.get("cursor") is None


async def test_unknown_timetable_does_not_export(photos, service, tmp_path):
    service.plugins.instances["timetable"] = Timetable(known=False)
    add_photo(photos, tmp_path, "private", "08:00:00")
    await photos.scan()
    assert photos.photos.exports == []


@pytest.mark.parametrize("missing_day", [False, True])
async def test_photos_wait_for_timetable_and_retry_recent_days(
    photos, service, tmp_path, missing_day, monkeypatch
):
    timetable = Timetable(known=False)
    service.plugins.instances["timetable"] = timetable
    original = timetable.timetable_days

    async def days(arguments):
        data = await original(arguments)
        return {"days": []} if missing_day and not timetable.known else data

    monkeypatch.setattr(timetable, "timetable_days", days)
    identifier = add_photo(photos, tmp_path, "waiting", "08:00:00")
    await photos.scan()
    assert photos.photos.exports == []
    assert (
        photos.db.rows("SELECT status FROM class_photos_files")[0]["status"] == "waiting_timetable"
    )

    photos.now = lambda: NOW + timedelta(days=2)
    timetable.known = True
    await photos.scan()
    await photos.scan()
    assert photos.photos.exports == [identifier]
    row = photos.db.rows("SELECT * FROM class_photos_files")[0]
    assert row["status"] == "copied" and row["output_path"]


async def test_confirmed_outside_class_and_old_unknown_photos_stay_skipped(
    photos, service, tmp_path
):
    add_photo(photos, tmp_path, "outside", "12:00:00")
    await photos.scan()
    timetable = service.plugins.instances["timetable"]
    timetable.known = False
    add_photo(photos, tmp_path, "unknown", "08:00:00")
    await photos.scan()
    photos.now = lambda: NOW + timedelta(days=4)
    timetable.known = True
    await photos.scan()
    assert photos.photos.exports == []
    assert sorted(r["status"] for r in photos.db.rows("SELECT status FROM class_photos_files")) == [
        "outside_class",
        "waiting_timetable",
    ]


async def test_limited_permission_and_private_topic(photos, service, tmp_path):
    photos.photos.permission = "limited"
    service.knowledge.access.patch(default_readers=[])
    identifier = add_photo(photos, tmp_path, "slide", "08:00:00")
    await photos.scan()
    assert photos.photos.exports == [identifier]
    area = next(a for a in service.knowledge.access.discover() if a["path"] == "知识库/高数")
    assert area["readers"] == []
    row = photos.db.rows("SELECT output_path FROM class_photos_files WHERE id=?", (identifier,))[0]
    assert service.knowledge.source(row["output_path"])["status"] == "skipped"


async def test_knowledge_inputs_shape_disabled_and_auth(service, goodnotes, photos, tmp_path):
    service.knowledge.topic("数据结构")
    backup(goodnotes, "数据结构/第5章.pdf")
    await goodnotes.scan()
    add_photo(photos, tmp_path, "slide", "08:00:00")
    await photos.scan()
    service.plugins.instances["goodnotes"] = goodnotes
    service.plugins.instances["class_photos"] = photos
    token, _ = service.tokens.create("App test", ["app"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service=service)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        response = await client.get("/api/v1/knowledge/inputs")
        assert response.status_code == 200
        data = response.json()
        assert data["goodnotes"]["notebooks"][0]["status"] == "copied"
        assert data["class_photos"]["recent"] == [
            {"day": "2026-10-29", "course": "高等数学I", "topic": "高数", "count": 1}
        ]
        assert set(data["goodnotes"]) == {"enabled", "address_path", "last_backup_at", "notebooks"}
        assert set(data["class_photos"]) == {
            "enabled",
            "permission",
            "last_run_at",
            "recent",
            "skipped_not_slides",
        }
        service.plugins.instances.pop("goodnotes")
        service.plugins.instances.pop("class_photos")
        assert (await client.get("/api/v1/knowledge/inputs")).json() == {
            "goodnotes": {"enabled": False},
            "class_photos": {"enabled": False},
        }
        schema = (await client.get("/openapi.json")).json()
        assert "/api/v1/knowledge/inputs" in schema["paths"]
        assert not any(p.startswith("/dav") for p in schema["paths"])


def test_pdf_limit_and_text_truncation(service):
    folder = service.memory.root / "知识库/大课件"
    folder.mkdir(parents=True)
    pdf = folder / "big.pdf"
    with pdf.open("wb") as stream:
        stream.truncate(MAX_FILE + 1)
    service.memory.documents.extractor = FakeExtractor(
        {"big.pdf": {"pages": [{"page": 1, "text": "字" * MAX_TEXT}]}}
    )
    result = service.memory.documents.read(pdf)
    assert result["error"] is None and result["truncated"]
    service.knowledge.sync()
    assert service.knowledge.source("知识库/大课件/big.pdf")["status"] == "pending"
    assert service.knowledge.raw("知识库/大课件/big.pdf")[0] == pdf
    with pdf.open("wb") as stream:
        stream.truncate(MAX_PDF + 1)
    assert "500 MB" in service.memory.documents.read(pdf)["error"]
    image = folder / "big.png"
    with image.open("wb") as stream:
        stream.truncate(MAX_FILE + 1)
    assert "50 MB" in service.memory.documents.read(image)["error"]


def test_large_pdf_goes_to_doc_text_like_any_pdf(tmp_path, monkeypatch):
    pdf = tmp_path / "large.pdf"
    with pdf.open("wb") as stream:
        stream.truncate(MAX_FILE + 1)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return type(
            "Result", (), {"returncode": 0, "stdout": b'{"pages":[{"page":1,"text":"native"}]}'}
        )()

    monkeypatch.setattr("core.documents.subprocess.run", run)
    assert MacExtractor(tmp_path / "doc-text").extract(pdf)["pages"][0]["text"] == "native"
    assert calls[0][0] == [str(tmp_path / "doc-text"), "pdf"]


def test_photo_helper_boundary_uses_json_and_fixed_errors(tmp_path, monkeypatch):
    seen = []

    def run(args, **kwargs):
        seen.append((args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": b'{"permission":"authorized"}'})()

    monkeypatch.setattr("plugins.class_photos.photos.subprocess.run", run)
    assert MacPhotoLibrary(tmp_path / "helper").call("status")["permission"] == "authorized"
    assert seen[0][0][-1] == "status" and seen[0][1]["timeout"] == 30


@pytest.mark.parametrize(
    "permission,status", [("authorized", "ok"), ("limited", "ok"), ("denied", "fail")]
)
def test_doctor_photo_permission_without_request(tmp_path, permission, status):
    class Probe:
        platform = "darwin"
        home = tmp_path

        def exists(self, path):
            return True

        def run(self, args):
            assert args[-1] == "status"
            return Result(0, '{"permission":"' + permission + '"}')

    report = Report(Probe())
    check_photo_export(report, None)
    assert report.checks[0].status == status and permission in report.checks[0].detail


async def test_delayed_icloud_photo_is_found_without_reexport(photos, tmp_path):
    old = add_photo(photos, tmp_path, "old", "08:00:00")
    await photos.scan()
    late = add_photo(photos, tmp_path, "late", "08:30:00")
    photos.now = lambda: NOW + timedelta(hours=1)
    await photos.scan()
    assert photos.photos.exports == [old, late]


async def test_photo_existing_file_is_not_overwritten(photos, service, tmp_path):
    identifier = add_photo(photos, tmp_path, "existing", "08:00:00")
    target = service.memory.root / "知识库/高数/课堂照片/2026-10-29/0800-existing.heic"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user file")
    await photos.scan()
    assert target.read_bytes() == b"user file"
    assert (
        photos.db.rows("SELECT status FROM class_photos_files WHERE id=?", (identifier,))[0][
            "status"
        ]
        == "conflict"
    )
    await photos.scan()
    assert photos.photos.exports.count(identifier) == 1


async def test_status_actual_plugin_enable_disable_and_settings(service):
    from core.models import PluginPatch

    await service.plugins.discover()
    assert service.plugins.view("class_photos")["state"] == "unsupported"
    await service.plugins.patch("goodnotes", PluginPatch(enabled=True))
    assert "plugin:goodnotes:scan" in service.jobs.callbacks
    service.plugins.platform = "macos"
    await service.plugins.patch("class_photos", PluginPatch(enabled=True))
    service.plugins.instances["class_photos"].photos = FakePhotoLibrary(permission="denied")
    assert "plugin:class_photos:scan" in service.jobs.callbacks
    token, _ = service.tokens.create("App settings", ["app"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service=service)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        state = (await client.get("/api/v1/knowledge/inputs")).json()
        assert state["goodnotes"]["enabled"] and state["class_photos"]["permission"] == "denied"
        assert (
            await client.patch(
                "/api/v1/settings", json={"dav_max_file_bytes": 100, "dav_max_total_bytes": 1000}
            )
        ).status_code == 200
        for value in (0, -1, True, 1.1, None):
            assert (
                await client.patch("/api/v1/settings", json={"dav_max_file_bytes": value})
            ).status_code == 422
        for identifier in ("goodnotes", "class_photos"):
            assert (
                await client.patch("/api/v1/plugins/" + identifier, json={"enabled": False})
            ).status_code == 200
        assert (await client.get("/api/v1/knowledge/inputs")).json() == {
            "goodnotes": {"enabled": False},
            "class_photos": {"enabled": False},
        }
        assert "plugin:class_photos:scan" not in service.jobs.callbacks


async def test_photo_batch_cap_bisects_and_covers_all(photos):
    class CappedLibrary:
        def call(self, command, payload):
            first = datetime.fromisoformat(payload["from"])
            last = datetime.fromisoformat(payload["to"])
            return [
                {"id": str(n), "created_at": (NOW + timedelta(seconds=n)).isoformat()}
                for n in range(2100)
                if first <= NOW + timedelta(seconds=n) < last
            ][:2000]

    photos.photos = CappedLibrary()
    items = await photos.list_window(NOW, NOW + timedelta(hours=1))
    assert len(items) == 2100 and len({p["id"] for p in items}) == 2100


async def test_one_export_failure_does_not_block_later_photos(photos, tmp_path):
    failed = add_photo(photos, tmp_path, "broken", "08:00:00")
    successful = add_photo(photos, tmp_path, "later", "08:30:00")
    source = photos.photos.files.pop(failed)
    with pytest.raises(ValueError, match="class_photos_import_failed"):
        await photos.scan()
    assert (
        photos.db.rows("SELECT status FROM class_photos_files WHERE id=?", (successful,))[0][
            "status"
        ]
        == "copied"
    )
    assert not list(photos.staging.iterdir())
    photos.photos.files[failed] = source
    await photos.scan()
    assert photos.photos.exports.count(successful) == 1
    assert (
        photos.db.rows("SELECT status FROM class_photos_files WHERE id=?", (failed,))[0]["status"]
        == "copied"
    )


async def test_unsupported_image_is_discarded_without_blocking(photos, tmp_path):
    identifier = add_photo(photos, tmp_path, "raw", "08:00:00")
    raw = tmp_path / "raw.dng"
    raw.write_bytes(b"synthetic raw image")
    photos.photos.files[identifier] = raw
    await photos.scan()
    assert (
        photos.db.rows("SELECT status FROM class_photos_files WHERE id=?", (identifier,))[0][
            "status"
        ]
        == "unreadable"
    )
    assert not list(photos.staging.iterdir())


async def test_a_photo_that_keeps_failing_is_given_up(photos, tmp_path):
    broken = add_photo(photos, tmp_path, "broken", "08:00:00")
    photos.photos.files.pop(broken)
    for _ in range(2):
        with pytest.raises(ValueError, match="class_photos_import_failed"):
            await photos.scan()
    await photos.scan()  # the third failure records it and lets the run succeed
    assert (
        photos.db.rows("SELECT status FROM class_photos_files WHERE id=?", (broken,))[0]["status"]
        == "failed"
    )
    assert photos.context.get("failures") == {}


async def test_goodnotes_does_not_copy_an_unchanged_backup_again(service, goodnotes, monkeypatch):
    service.knowledge.topic("数据结构")
    backup(goodnotes, "数据结构/第5章.pdf")
    await goodnotes.scan()
    copies = []
    original = goodnotes.copy
    monkeypatch.setattr(goodnotes, "copy", lambda *args: copies.append(args) or original(*args))
    await goodnotes.scan()
    assert copies == []
    path = backup(goodnotes, "数据结构/第5章.pdf", content=b"pdf v2")
    stamp = (NOW - timedelta(seconds=150)).timestamp()
    os.utime(path, (stamp, stamp))
    await goodnotes.scan()
    assert len(copies) == 1
    copied = service.memory.root / "知识库/数据结构/GoodNotes/第5章.pdf"
    assert copied.read_bytes() == b"pdf v2"
