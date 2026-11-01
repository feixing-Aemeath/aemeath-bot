import json
from types import SimpleNamespace as Obj

import pytest

from core.windows_documents import docx_text, extract
from core.windows_ocr import line_blocks
from plugins.screen.regions import Rect, Regions, screen_point
from plugins.screen.windows_helper import Output, WindowsHelper, row_match, title_match


class Image:
    """Synthetic window with the same dividers as WindowShot; no bitmap dependency needed."""

    def __init__(self, size=(1000, 800), region=None):
        self.size, self.region = size, region

    def convert(self, _):
        return self

    def tobytes(self):
        width, height = self.size
        return bytes(
            210
            if x >= 800
            else 220
            if x >= 320 and (y < 80 or y >= 600)
            else 240
            if x >= 320
            else 225
            if x >= 60
            else 200
            for y in range(height)
            for x in range(width)
        )

    def crop(self, box):
        return Image((box[2] - box[0], box[3] - box[1]), box)

    def save(self, path, **_):
        path.write_text(json.dumps({"size": self.size, "region": self.region}))


class Desktop:
    def __init__(self):
        self.title, self.events, self.is_locked, self.switch_after_click = (
            "其他群",
            [],
            False,
            False,
        )

    def locked(self):
        return self.is_locked

    def windows(self, app):
        return [Obj(handle=1, frame=Rect(-1000, 0, 1000, 800), title="学校个人日程 - Chrome")]

    def capture(self, window):
        return Image()

    def activate(self, window):
        self.events.append("activate")

    def click_row(self, window, rect, point, size):
        assert rect.contains(point) and point[0] < 320
        self.events.append("click_row")
        self.title = "错误的群" if self.switch_after_click else "计算机一班（42）"

    def scroll_chat(self, window, rect, size, ticks):
        assert rect.y > 70 and rect.y + rect.h <= 600
        self.events.append(("scroll_chat", ticks))

    async def ocr(self, path):
        data = json.loads(path.read_text())
        text = self.title if data["size"][1] < 100 else "计算机一班"
        return [{"text": text, "x": 0.1, "y": 0.2, "w": 0.5, "h": 0.03, "confidence": 1}]


async def pause(_):
    pass


def test_regions_follow_window_dividers_and_dpi_coordinates():
    image = Image()
    regions = Regions.detect(image.tobytes(), *image.size)
    assert regions.found == ["nav", "list", "panel", "header", "input"]
    assert regions.list == Rect(61, 0, 259, 800)
    assert regions.chat == Rect(321, 81, 479, 519)
    assert screen_point((600, 300), Rect(-1200, 50, 800, 600), (1200, 900)) == (-800, 250)
    fallback = Regions.detect(bytes([240]) * 10000, 100, 100)
    assert fallback.found == []
    assert fallback.header.h == 9


def test_ocr_blocks_have_top_left_fractional_coordinates():
    lines = [
        Obj(
            text="老师 中文",
            words=[
                Obj(bounding_rect=Obj(x=10, y=20, width=30, height=10)),
                Obj(bounding_rect=Obj(x=50, y=20, width=40, height=12)),
            ],
        )
    ]
    assert line_blocks(lines, 200, 100) == [
        {"text": "老师 中文", "x": 0.05, "y": 0.2, "w": 0.4, "h": 0.12, "confidence": 1.0}
    ]


def test_chat_names_never_match_another_complete_title():
    assert title_match("计算机一班", ["计算机一班（42）"]) == "计算机一班"
    assert title_match("计算机一班", ["计算机一班家长群"]) is None
    assert row_match("计算机一班", "计算机一班 21:05")
    assert not row_match("计算机一班", "计算机一班家长群")


async def test_preview_layout_browser_and_announcement_send_no_input(tmp_path):
    desktop = Desktop()
    helper = WindowsHelper(desktop, desktop.ocr, pause=pause)
    for action in ("preview", "layout", "capture_announcement", "capture_page", "permissions"):
        reply = await helper.call(
            {
                "action": action,
                "app": "chrome" if action == "capture_page" else "qq",
                "title": "个人日程",
                "out_dir": str(tmp_path),
            }
        )
        assert reply
    assert desktop.events == []
    assert list(tmp_path.glob("*.png"))


async def test_chat_clicks_only_requested_row_and_checks_title_before_scrolling(tmp_path):
    desktop = Desktop()
    helper = WindowsHelper(desktop, desktop.ocr, pause=pause)
    request = {
        "action": "capture_chat",
        "app": "qq",
        "chat": "计算机一班",
        "page": 0,
        "out_dir": str(tmp_path),
    }
    shot = await helper.call(request)
    assert shot["chat"] == "计算机一班" and not shot["at_top"]
    assert desktop.events == ["activate", "click_row", ("scroll_chat", -120)]
    shot = await helper.call(request | {"page": 1})
    assert shot["at_top"]
    desktop.switch_after_click, desktop.title, desktop.events = True, "其他群", []
    with pytest.raises(RuntimeError, match="screen_chat_not_open"):
        await helper.call(request)
    assert desktop.events == ["activate", "click_row"]
    desktop.title, desktop.events = "其他群", []
    with pytest.raises(RuntimeError, match="screen_chat_not_open"):
        await helper.call(request | {"page": 1})
    assert "click_row" not in desktop.events


async def test_locked_screen_and_recognition_path_are_fail_closed(tmp_path):
    desktop = Desktop()
    helper = WindowsHelper(desktop, desktop.ocr, pause=pause)
    desktop.is_locked = True
    with pytest.raises(RuntimeError, match="screen_locked"):
        await helper.call({"action": "preview", "app": "qq", "out_dir": str(tmp_path)})
    for action in ("send", "type", "click", None):
        with pytest.raises(RuntimeError, match="screen_invalid_request"):
            await helper.call({"action": action})
    output = Output(tmp_path / "screens")
    assert not output.contains(str(tmp_path / "other.png"))
    with pytest.raises(RuntimeError, match="screen_invalid_request"):
        await helper.call(
            {
                "action": "recognize",
                "path": str(tmp_path / "other.png"),
                "out_dir": str(output.directory),
            }
        )
    assert desktop.events == []


async def test_document_image_ocr_and_docx_are_local(tmp_path):
    import zipfile

    path = tmp_path / "课程.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>课程笔记</w:t></w:r></w:p></w:body></w:document>',
        )
    assert docx_text(path) == "课程笔记"
    assert await extract(path, "docx") == {"text": "课程笔记"}

    async def ocr(path):
        return [{"text": "本机识别"}]

    assert await extract(tmp_path / "资料.png", "image", ocr=ocr) == {"text": "本机识别"}


async def test_pdf_text_and_scanned_pages_use_the_same_protocol(tmp_path):
    closed = []

    class Page:
        def __init__(self, text):
            self.text = text

        def get_textpage(self):
            return Obj(get_text_range=lambda: self.text, close=lambda: closed.append("text"))

        def render(self, scale):
            return Obj(
                to_pil=lambda: Obj(
                    thumbnail=lambda size: None, save=lambda path: path.write_bytes(b"fake image")
                ),
                close=lambda: closed.append("bitmap"),
            )

        def get_size(self):
            return (600, 800)

        def close(self):
            closed.append("page")

    class PDF:
        def __len__(self):
            return 2

        def __getitem__(self, index):
            return Page("文字层" if index == 0 else "")

        def close(self):
            closed.append("pdf")

    async def ocr(path):
        assert path.read_bytes() == b"fake image"
        return [{"text": "扫描页"}]

    result = await extract(tmp_path / "资料.pdf", "pdf", ocr=ocr, pdf_factory=lambda path: PDF())
    assert result == {
        "pages": [{"page": 1, "text": "文字层"}, {"page": 2, "text": "扫描页"}],
        "truncated": False,
    }
    assert closed.count("page") == 2 and "bitmap" in closed and closed[-1] == "pdf"
