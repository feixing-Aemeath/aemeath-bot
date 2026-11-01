"""Create, list and revoke device tokens on the Mac that runs the backend.

    uv run python -m scripts.tokens create 学校电脑 --scope mcp --days 180
    uv run python -m scripts.tokens create iPhone --scope app
    uv run python -m scripts.tokens list [--all]
    uv run python -m scripts.tokens revoke 学校电脑      # or the ID, e.g. dev_1a2b3c4d5e6f

A token is printed once, when it is created; the database keeps only its SHA-256. Changes
take effect on the next request, without restarting the backend. Uses the database in
ASSISTANT_DATA_DIR (from the environment or backend/.env, default .state).
"""

import argparse
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from core.db import Database
from core.tokens import SCOPES, DeviceTokens

BACKEND = Path(__file__).resolve().parents[1]
STATUS = {"active": "有效", "expired": "已过期", "revoked": "已作废"}


class Location(BaseSettings):
    """Only where the data lives; the CLI never needs ASSISTANT_TOKEN."""

    model_config = SettingsConfigDict(
        env_prefix="ASSISTANT_", env_file=BACKEND / ".env", extra="ignore"
    )
    data_dir: Path = Path(".state")


def database(data_dir):
    folder = Path(data_dir) if data_dir else Location().data_dir
    if not folder.is_absolute():
        folder = BACKEND / folder
    path = folder / "assistant.sqlite3"
    if not path.exists():
        raise FileNotFoundError(
            f"找不到数据库 {path}。先启动一次后端，或者用 --data-dir 指定数据目录。"
        )
    return Database(path)


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def table(rows):
    sizes = [max(width(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        cells = [cell + " " * (size - width(cell)) for cell, size in zip(row, sizes, strict=True)]
        print("  ".join(cells).rstrip())


def local(stamp):
    if not stamp:
        return "-"
    return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")


def scopes_of(values):
    return [part for value in values for part in value.split(",")]


def create(tokens, args):
    token, view = tokens.create(args.name, scopes_of(args.scope), args.days, args.reader)
    if args.quiet:
        print(token)
        return
    print(f"已创建令牌「{view['name']}」（{view['id']}，权限：{', '.join(view['scopes'])}）。")
    if view["expires_at"]:
        print(f"到期：{local(view['expires_at'])}")
    print("下面这串令牌只显示这一次，数据库里只保存它的哈希值。现在复制到那台设备上：")
    print()
    print(token)
    print()
    print(f"作废：uv run python -m scripts.tokens revoke {view['id']}")


def show(tokens, args):
    views = tokens.list(include_revoked=args.all)
    if not views:
        print("还没有设备令牌。创建：uv run python -m scripts.tokens create <名称> --scope app")
        return
    rows = [["ID", "名称", "权限", "读者", "开头", "创建", "上次使用", "到期", "状态"]]
    for view in views:
        rows.append(
            [
                view["id"],
                view["name"],
                ",".join(view["scopes"]),
                view["reader"] or "未限定",
                view["hint"] + "…",
                local(view["created_at"]),
                local(view["last_used_at"]),
                local(view["expires_at"]),
                STATUS[view["status"]],
            ]
        )
    table(rows)


def revoke(tokens, args):
    view = tokens.revoke(args.ref)
    print(f"已作废「{view['name']}」（{view['id']}），这台设备的下一个请求就会被拒绝。")


def parser():
    scopes = "；".join(f"{name}：{text}" for name, text in SCOPES.items())
    root = argparse.ArgumentParser(
        prog="python -m scripts.tokens", description="在运行后端的 Mac 上创建、列出、作废设备令牌。"
    )
    root.add_argument("--data-dir", help="数据目录，默认 ASSISTANT_DATA_DIR")
    commands = root.add_subparsers(dest="command", required=True)
    made = commands.add_parser("create", help="给一台设备创建令牌")
    made.add_argument("name", help="设备名称，如「学校电脑」「iPhone」")
    made.add_argument(
        "--scope", action="append", required=True, help=f"可重复或用逗号分隔。{scopes}"
    )
    made.add_argument("--days", type=int, help="有效天数（默认不过期）")
    made.add_argument("--reader", help="Agent 读者身份，如 Claude Code 或 Codex")
    made.add_argument("--quiet", action="store_true", help="只输出令牌本身")
    made.set_defaults(run=create)
    listed = commands.add_parser("list", help="列出令牌（不显示令牌本身）")
    listed.add_argument("--all", action="store_true", help="也列出已作废的")
    listed.set_defaults(run=show)
    revoked = commands.add_parser("revoke", help="作废一个令牌")
    revoked.add_argument("ref", help="令牌 ID，或有效令牌的名称")
    revoked.set_defaults(run=revoke)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.run(DeviceTokens(database(args.data_dir)), args)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    except KeyError as error:
        print(f"没有找到令牌 {error.args[0]}（用 list --all 查看 ID）", file=sys.stderr)
        return 1
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
