"""Isolated, read-only OCR/desktop probe. Never reads windows or captures screenshots."""

import json
import sys


def main():
    state = {"languages": [], "locked": None}
    if sys.platform == "win32":
        try:
            from core.windows_ocr import languages

            state["languages"] = languages()
        except Exception:
            pass
        try:
            from plugins.screen.windows_native import Desktop

            state["locked"] = Desktop().locked()
        except Exception:
            pass
    sys.stdout.buffer.write(json.dumps(state).encode("utf-8"))


if __name__ == "__main__":
    main()
