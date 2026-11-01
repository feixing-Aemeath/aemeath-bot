"""Local document helper on Windows: PDFium text/rendering and Windows OCR.

JSON protocol matches doc-text: `image` -> {text}, `pdf` -> {pages: [{page, text}]}.
The helper runs once per extraction with a process-tree timeout. Nothing leaves this PC.
"""

import asyncio
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

MAX_XML = 20 * 1024 * 1024
MAX_PAGES = 500
MAX_TEXT = 2_000_000


def docx_text(path):
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_XML:
            raise ValueError("document_too_large")
        content = archive.read(info)
    if b"<!DOCTYPE" in content or b"<!ENTITY" in content:
        raise ValueError("document_invalid_xml")
    root = ET.fromstring(content)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n".join(
        "".join(node.text or "" for node in paragraph.iter(ns + "t"))
        for paragraph in root.iter(ns + "p")
    )


async def extract(path, mode, *, ocr=None, pdf_factory=None):
    if mode == "docx":
        return {"text": docx_text(path)}
    if ocr is None:
        from core.windows_ocr import recognize

        ocr = recognize
    if mode == "image":
        return {"text": "\n".join(b["text"] for b in await ocr(path))}
    if mode != "pdf":
        raise ValueError("document_invalid_request")
    if pdf_factory is None:
        if sys.platform != "win32":
            raise RuntimeError("windows_required")
        import pypdfium2

        pdf_factory = pypdfium2.PdfDocument
    document = pdf_factory(str(path))
    try:
        pages, size = [], 0
        truncated = len(document) > MAX_PAGES
        with tempfile.TemporaryDirectory(prefix="aemeath-ocr-") as directory:
            for index in range(min(len(document), MAX_PAGES)):
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        text = text_page.get_text_range()
                    finally:
                        text_page.close()
                    if not text.strip():
                        bitmap = page.render(scale=min(2, 4000 / max(page.get_size())))
                        try:
                            image = bitmap.to_pil()
                            image.thumbnail((4000, 4000))
                            image_path = Path(directory) / "page.png"
                            image.save(image_path)
                            text = "\n".join(b["text"] for b in await ocr(image_path))
                        finally:
                            bitmap.close()
                    remaining = MAX_TEXT - size
                    raw = text.encode("utf-8")
                    pages.append(
                        {
                            "page": index + 1,
                            "text": raw[:remaining].decode("utf-8", errors="ignore"),
                        }
                    )
                    size += len(raw)
                    if size >= MAX_TEXT:
                        truncated = True
                        break
                finally:
                    page.close()
        return {"pages": pages, "truncated": truncated}
    finally:
        document.close()


def main():
    try:
        if sys.platform != "win32":
            raise RuntimeError("windows_required")
        request = json.loads(sys.stdin.buffer.read(65537))
        result = asyncio.run(extract(Path(request["path"]), sys.argv[1]))
        code = 0
    except Exception:
        result, code = {"error": "document_extraction_failed"}, 1
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
