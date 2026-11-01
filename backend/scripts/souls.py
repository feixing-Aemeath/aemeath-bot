"""Import user-written souls and list character memory files, without starting the core.

uv run python -m scripts.souls import ~/Downloads/人设
uv run python -m scripts.souls import ~/Downloads/人设 --replace
uv run python -m scripts.souls list
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from core.bots import CHARACTERS
from scripts.tokens import BACKEND, table


class Location(BaseSettings):
    """The import only needs the vault location, never credentials or a database."""

    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_", env_file=BACKEND / ".env", extra="ignore"
    )
    data_dir: Path = Path(".state")
    vault_dir: Path | None = None


def vault(directory):
    location = Location()
    folder = directory or location.vault_dir or location.data_dir / "vault"
    return (BACKEND / folder).resolve()


def target(root, name, file):
    path = root / "Bots" / name / file
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError(f"角色文件不能是符号链接或指向记忆目录外：{path}")
    return path


def import_souls(root, args):
    if not args.folder.is_dir():
        raise ValueError(f"找不到人设文件夹：{args.folder}")
    for source in sorted(args.folder.iterdir()):
        if not source.is_file():
            continue
        name = source.name.removesuffix(".soul.md")
        if not source.name.endswith(".soul.md") or name not in CHARACTERS.values():
            print(f"跳过「{source.name}」：认不出角色文件名，应为 <角色名>.soul.md。")
            continue
        path = target(root, name, "soul.md")
        if path.exists() and not args.replace:
            print(f"跳过「{source.name}」：已有 soul.md，用 --replace 才会覆盖。")
            continue
        # Validate locally before touching the destination; do not print soul contents.
        content = source.read_bytes()
        content.decode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.replace:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".soul-", delete=False
            ) as output:
                temp = Path(output.name)
                try:
                    output.write(content)
                    output.close()
                    os.replace(temp, path)
                finally:
                    temp.unlink(missing_ok=True)
        else:
            try:
                with path.open("xb") as output:
                    output.write(content)
            except FileExistsError:
                print(f"跳过「{source.name}」：已有 soul.md，用 --replace 才会覆盖。")
                continue
        print(f"已导入「{name}」：{path.relative_to(root).as_posix()}")


def show(root, args):
    rows = [["角色", "soul.md", "记忆.md"]]
    for name in CHARACTERS.values():
        soul, notes = target(root, name, "soul.md"), target(root, name, "记忆.md")
        rows.append(
            [
                name,
                "已有" if soul.is_file() else "还没有",
                f"{notes.stat().st_size} 字节" if notes.is_file() else "还没有（0 字节）",
            ]
        )
    table(rows)


def parser():
    root = argparse.ArgumentParser(
        prog="python -m scripts.souls", description="导入角色人设、查看各角色的长期记忆。"
    )
    root.add_argument("--vault-dir", type=Path, help="记忆目录，默认 ASSISTANT_VAULT_DIR")
    commands = root.add_subparsers(dest="command", required=True)
    imported = commands.add_parser("import", help="导入文件夹里的 <角色名>.soul.md")
    imported.add_argument("folder", type=Path, help="存放人设文件的文件夹（不递归）")
    imported.add_argument("--replace", action="store_true", help="覆盖已有的人设文件")
    imported.set_defaults(run=import_souls)
    listed = commands.add_parser("list", help="列出人设是否存在、记忆文件大小")
    listed.set_defaults(run=show)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.run(vault(args.vault_dir), args)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"操作失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
