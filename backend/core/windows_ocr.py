"""Windows.Media.Ocr (zh-Hans), behind a lazy platform boundary."""

import sys
from pathlib import Path


def line_blocks(lines, width, height):
    """Convert WinRT word rectangles to Vision-compatible line blocks (top-left fractions).

    Windows OCR does not report confidence. A recognized line uses 1.0; downstream
    extraction still applies its own confidence threshold to task interpretation.
    """
    result = []
    for line in lines:
        words = list(line.words)
        if not words or not line.text.strip():
            continue
        boxes = [word.bounding_rect for word in words]
        x = max(0, min(b.x for b in boxes))
        y = max(0, min(b.y for b in boxes))
        right = min(width, max(b.x + b.width for b in boxes))
        bottom = min(height, max(b.y + b.height for b in boxes))
        if right > x and bottom > y:
            result.append(
                {
                    "text": line.text,
                    "x": x / width,
                    "y": y / height,
                    "w": (right - x) / width,
                    "h": (bottom - y) / height,
                    "confidence": 1.0,
                }
            )
    return result


def languages():
    if sys.platform != "win32":
        return []
    from winrt.windows.media.ocr import OcrEngine

    return [language.language_tag for language in OcrEngine.available_recognizer_languages]


async def recognize(path):
    if sys.platform != "win32":
        raise RuntimeError("windows_required")
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode,
        BitmapDecoder,
        BitmapPixelFormat,
        BitmapTransform,
        ColorManagementMode,
        ExifOrientationMode,
    )
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    engine = OcrEngine.try_create_from_language(Language("zh-Hans"))
    if engine is None:
        raise RuntimeError("screen_ocr_language_missing")
    file = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
    stream = await file.open_async(FileAccessMode.READ)
    bitmap = None
    try:
        decoder = await BitmapDecoder.create_async(stream)
        transform = BitmapTransform()
        scale = min(
            1.0, OcrEngine.max_image_dimension / max(decoder.pixel_width, decoder.pixel_height)
        )
        transform.scaled_width = max(1, round(decoder.pixel_width * scale))
        transform.scaled_height = max(1, round(decoder.pixel_height * scale))
        bitmap = await decoder.get_software_bitmap_transformed_async(
            BitmapPixelFormat.GRAY8,
            BitmapAlphaMode.IGNORE,
            transform,
            ExifOrientationMode.RESPECT_EXIF_ORIENTATION,
            ColorManagementMode.DO_NOT_COLOR_MANAGE,
        )
        recognized = await engine.recognize_async(bitmap)
        return line_blocks(recognized.lines, bitmap.pixel_width, bitmap.pixel_height)
    finally:
        if bitmap is not None:
            bitmap.close()
        stream.close()
