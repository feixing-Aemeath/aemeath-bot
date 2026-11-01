"""Optional CPU ASR for video_notes, using a preinstalled local faster-whisper model.

CLI output and flags match the mlx_whisper boundary used by the Mac plugin. No model is
downloaded here; install the windows-asr extra and supply a CTranslate2 model directory.
"""

import argparse
import json
import sys
from pathlib import Path


def parser():
    result = argparse.ArgumentParser(description="本地离线语音识别")
    result.add_argument("path", type=Path)
    result.add_argument("--model", required=True, type=Path)
    result.add_argument("--output-dir", required=True, type=Path)
    result.add_argument("--output-format", choices=["json"], default="json")
    result.add_argument("--verbose", default="False")
    return result


def transcribe(args, factory):
    if not (args.model / "model.bin").is_file() or not (args.model / "config.json").is_file():
        raise ValueError("local_asr_model_required")
    model = factory(
        str(args.model.resolve()), device="cpu", compute_type="int8", local_files_only=True
    )
    parts, _ = model.transcribe(str(args.path.resolve()), beam_size=5)
    data = {"segments": [{"start": part.start, "text": part.text} for part in parts]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / (args.path.stem + ".json")).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if sys.platform != "win32":
            raise RuntimeError("windows_required")
        from faster_whisper import WhisperModel

        transcribe(args, WhisperModel)
        return 0
    except Exception:
        print("local_asr_failed: check windows-asr extra and local model", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
