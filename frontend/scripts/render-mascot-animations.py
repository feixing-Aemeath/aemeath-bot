#!/usr/bin/env python3
"""macOS: compile the production mascot views and export every act with ImageRenderer."""
import argparse
from pathlib import Path
import platform
import subprocess
import tempfile

frontend = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="mascot-render-") as temporary:
    build = Path(temporary)
    sources = frontend / "Packages/AssistantCore/Sources"
    common = ["xcrun", "swiftc", "-swift-version", "5", "-target",
              f"{platform.machine()}-apple-macosx14.0", "-module-cache-path", str(build / "cache")]
    subprocess.run(common + ["-emit-library", "-emit-module", "-module-name", "AssistantCore",
                            "-emit-module-path", str(build / "AssistantCore.swiftmodule"),
                            "-o", str(build / "libAssistantCore.dylib")]
                   + [str(p) for p in sorted((sources / "AssistantCore").glob("*.swift"))], check=True)
    executable = build / "render"
    subprocess.run(common + ["-parse-as-library", "-I", str(build), "-L", str(build),
                            "-lAssistantCore", "-Xlinker", "-rpath", "-Xlinker", str(build),
                            str(sources / "AssistantUI/MascotArtwork.swift"),
                            str(sources / "AssistantUI/MascotAnimationView.swift"),
                            str(frontend / "scripts/render-mascot-animations.swift"),
                            "-o", str(executable)], check=True)
    subprocess.run([str(executable), str(frontend / "Design/stickers"), str(args.output.resolve())], check=True)
