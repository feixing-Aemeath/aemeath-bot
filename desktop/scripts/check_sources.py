"""Checks that do not require Java. The JVM tests still have to run in CI."""

import json
from pathlib import Path
import re
import sys


def main() -> None:
    desktop = Path(__file__).resolve().parents[1]
    root = desktop.parent
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    build = (desktop / "build.gradle.kts").read_text()
    settings = (desktop / "settings.gradle.kts").read_text()
    require('file("../android/core")' in settings, "必须直接引用 android/core")
    require('version "2.2.20"' in build, "必须固定 Kotlin 版本")
    require('id("org.jetbrains.compose") version "1.9.1"' in build, "必须固定 Compose 版本")
    require('TargetFormat.Msi' in build and 'upgradeUuid = "8aef6340-3d05-45a7-94a7-caa29d50d9ba"' in build, "MSI 格式或升级标识缺失")

    stickers = root / "frontend/Design/stickers"
    names = {path.stem for path in stickers.glob("*.png")}
    roles = json.loads((stickers / "app.json").read_text())
    characters = json.loads((stickers / "characters.json").read_text())
    for role in ("assistant", "knowledge"):
        require(roles[role]["sticker"] in names, f"{role} 缺少贴图")
        require(roles[role]["sticker"] in characters, f"{role} 缺少角色名")
    for folder in stickers.iterdir():
        if folder.is_dir() and (folder / "base.png").exists():
            for layer in ("base", "eyes_open", "eyes_closed", "eyes_mask"):
                require((folder / f"{layer}.png").exists(), f"贴图缺层：{folder.name}/{layer}")
    require((root / "frontend/App/AppIcon.icon/Assets/aemeath.png").is_file(), "缺少 App 图标原图")

    sources = list((desktop / "src").rglob("*.kt"))
    for path in sources:
        text = path.read_text()
        require(not re.search(r"^import android\.", text, re.MULTILINE), f"桌面不能依赖 Android：{path.name}")
        # String literals may contain ';'; executable statements should not share a line.
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", code, flags=re.DOTALL)
        require(";" not in code, f"一行一条语句：{path.name}")
        require(not re.search(r"\b(?:println|printStackTrace)\s*\(", code), f"不要记录令牌或聊天：{path.name}")

    for path in desktop.rglob("*"):
        if any(part in {"build", ".gradle", ".kotlin", "__pycache__"} for part in path.relative_to(desktop).parts):
            continue
        require(path.suffix.lower() not in {".png", ".ico", ".msi", ".jar", ".ttf", ".otf", ".woff", ".woff2"}, f"不应提交二进制素材：{path}")

    tests = sum(len(re.findall(r"@Test\b", path.read_text())) for path in (desktop / "src/test").rglob("*.kt"))
    require(tests >= 40, "桌面逻辑测试缺失")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)
    print(f"静态检查通过：{len(sources)} 个 Kotlin 文件，{tests} 个桌面 JVM 测试声明，素材直接引用 Mac 版。")


if __name__ == "__main__":
    main()
