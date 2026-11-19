"""项目进度: real temporary git repos and a temporary vault; nothing outside tmp_path is read."""

import asyncio
import os
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.plugins import PluginContext
from plugins.project_progress.plugin import Plugin, parse_progress

MANIFEST = Path(__file__).resolve().parents[1] / "plugins" / "project_progress" / "manifest.yaml"
TODAY = date(2026, 11, 12)
PROGRESS = """# 项目进度

前言随便写，不会被读。

## 工作线

### 后端与插件
- 状态：运行中
- 时间：2026-10-12 起
- 现在：Mac mini 上常驻运行
- 下一步：重编读屏辅助程序

### DSH 插件版
- 状态: 进行中
- 时间：2026-11-12 起
- 现在：联调和审查 22:03 继续
- 备注：这一行不认识，跳过

## 接下来
- [ ] 把最新 App 重装到 iPhone
- [x] 做一个项目看板
- 买 HDMI 欺骗器

## 里程碑
- 2026-10-12 开工
- 2026-11-12：DSH 插件版开工
- 2026-11-13 目标：周额度重置前做完
- 2026-13-01 日期不对，跳过
"""


def commit(repo, day, subject, *extra):
    stamp = f"{day}T12:00:00+08:00"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", subject, *extra],
        check=True,
        env=env,
    )


def make_repo(path, days):
    path.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.name", "T"],
        ["config", "user.email", "t@example.com"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    for day in days:
        commit(path, day, f"work {day}")
    return path


def plugin_for(tmp_path, config, progress=PROGRESS):
    vault = (tmp_path / "vault").resolve()
    (vault / "Bots" / "知识库").mkdir(parents=True)
    if progress is not None:
        (vault / "Bots" / "知识库" / "项目进度.md").write_text(progress, encoding="utf-8")
    store = SimpleNamespace(settings=SimpleNamespace(timezone="Asia/Shanghai"))
    context = PluginContext(
        store=store,
        data_dir=tmp_path,
        plugin_id="project_progress",
        memory=SimpleNamespace(root=vault),
    )
    plugin = Plugin()
    asyncio.run(plugin.setup(context, config))
    plugin.today = lambda: TODAY
    return plugin, vault


def branch_commit(repo, branch, subject):
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)
    commit(repo, "2026-11-12", subject)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)


def merge(repo, branch, subject):
    stamp = "2026-11-12T13:00:00+08:00"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-ff", "-m", subject, branch],
        check=True,
        env=env,
    )


@pytest.fixture
def repos(tmp_path):
    # main: 3 commits, three PR merges (each 1 commit + 1 merge; PR #26 twice), 1 unmerged = 10.
    main = make_repo(tmp_path / "main", ["2026-10-12", "2026-11-11", "2026-11-12"])
    for branch, number in (("a", 26), ("b", 7), ("c", 26)):
        branch_commit(main, branch, f"work for #{number}")
        merge(main, branch, f"Merge pull request #{number} from someone/{branch}")
    branch_commit(main, "unmerged", "work not merged yet")
    side = make_repo(tmp_path / "side", ["2026-11-12", "2026-11-12"])
    return main, side


def test_manifest_belongs_to_the_librarian_and_validates():
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["id"] == "project_progress"
    assert manifest["mascot"]["sticker"] == "orange_daisy"
    assert manifest["provides"] == ["cards"]


def test_parse_reads_the_three_sections_and_skips_the_rest():
    parsed = parse_progress(PROGRESS)
    assert [s["name"] for s in parsed["streams"]] == ["后端与插件", "DSH 插件版"]
    assert parsed["streams"][1] == {
        "name": "DSH 插件版",
        "state": "进行中",
        "when": "2026-11-12 起",
        "now": "联调和审查 22:03 继续",
        "next": "",
    }
    assert parsed["tasks"] == [
        {"text": "把最新 App 重装到 iPhone", "done": False},
        {"text": "做一个项目看板", "done": True},
        {"text": "买 HDMI 欺骗器", "done": False},
    ]
    assert [(m["date"].isoformat(), m["text"]) for m in parsed["milestones"]] == [
        ("2026-10-12", "开工"),
        ("2026-11-12", "DSH 插件版开工"),
        ("2026-11-13", "目标：周额度重置前做完"),
    ]


def test_cards_count_every_local_branch_and_each_pr_once(tmp_path, repos):
    main, side = repos
    plugin, _ = plugin_for(
        tmp_path,
        {"repos": [{"name": "主仓库", "path": str(main)}, {"name": "DSH", "path": str(side)}]},
    )
    cards = asyncio.run(plugin.cards())
    assert [c["title"] for c in cards] == [
        "开发天数",
        "最近 14 天的提交",
        "工作线",
        "接下来",
        "最近的里程碑",
    ]
    stat = cards[0]["data"]
    # 10-12 to 11-12 inclusive; 10 commits in main (one only on an unmerged branch) + 2 in side.
    assert stat["value"] == 32 and stat["unit"] == "天" and stat["wide"] is True
    assert stat["label"] == "12 次提交 · 2 个合并的 PR · 在做：DSH 插件版"
    chart = cards[1]["data"]
    assert chart["kind"] == "bar" and [s["name"] for s in chart["series"]] == ["主仓库", "DSH"]
    assert len(chart["series"][0]["points"]) == 14
    assert chart["series"][0]["points"][-1] == {"x": "11/12", "y": 8}
    assert chart["series"][0]["points"][-2] == {"x": "11/11", "y": 1}
    assert chart["series"][1]["points"][-1] == {"x": "11/12", "y": 2}
    # The long 现在 text wraps in the subtitle; the right-hand detail is only the short state.
    assert cards[2]["data"]["items"][0] == {
        "title": "后端与插件",
        "subtitle": "2026-10-12 起 · Mac mini 上常驻运行",
        "detail": "运行中",
    }
    assert cards[3]["data"]["items"] == ["把最新 App 重装到 iPhone", "买 HDMI 欺骗器"]
    # Newest first, nothing from the future.
    assert cards[4]["data"]["items"] == [
        {"title": "DSH 插件版开工", "subtitle": "11/12"},
        {"title": "开工", "subtitle": "10/12"},
    ]


def test_one_repo_draws_labelled_bars_and_edits_show_up_at_once(tmp_path, repos):
    main, _ = repos
    plugin, vault = plugin_for(
        tmp_path, {"repos": [{"name": "主仓库", "path": str(main)}], "chart_days": 7}
    )
    chart = asyncio.run(plugin.cards())[1]
    assert chart["title"] == "最近 7 天的提交"
    assert chart["data"]["points"][-1] == {"x": "11/12", "y": 8, "label": "8"}
    assert chart["data"]["points"][0] == {"x": "11/6", "y": 0}
    (vault / "Bots" / "知识库" / "项目进度.md").write_text(
        PROGRESS.replace("进行中", "完成"), encoding="utf-8"
    )
    stat = asyncio.run(plugin.cards())[0]["data"]
    assert "在做" not in stat["label"]


def test_missing_file_and_unreadable_repo_say_so(tmp_path, repos):
    main, _ = repos
    plugin, _ = plugin_for(
        tmp_path,
        {
            "repos": [
                {"name": "主仓库", "path": str(main)},
                {"name": "没有", "path": str(tmp_path / "nowhere")},
            ]
        },
        progress=None,
    )
    cards = asyncio.run(plugin.cards())
    assert [c["type"] for c in cards] == ["stat", "chart", "text"]
    assert cards[0]["data"]["label"].endswith("1 个仓库读不到")
    assert "Bots/知识库/项目进度.md" in cards[2]["data"]["text"]


def test_no_readable_repo_and_a_path_outside_the_vault(tmp_path):
    plugin, _ = plugin_for(
        tmp_path,
        {
            "repos": [{"name": "没有", "path": str(tmp_path / "nowhere")}],
            "progress_file": "../secret.md",
        },
    )
    cards = asyncio.run(plugin.cards())
    assert cards[0]["type"] == "text" and "读不到 git 仓库" in cards[0]["data"]["text"]
    assert cards[1]["data"]["text"] == "进度文件的位置不在记忆目录里"


def test_default_repo_is_the_one_the_backend_runs_from(tmp_path):
    plugin, _ = plugin_for(tmp_path, {})
    assert plugin.repos == [{"name": "主仓库", "path": Path(__file__).resolve().parents[2]}]


def test_stream_rows_keep_the_right_hand_label_short():
    item = Plugin.stream_item
    assert item(
        {
            "name": "DSH 插件版",
            "state": "进行中",
            "when": "2026-11-12 起",
            "now": "四次独立审查的问题都修完了",
        }
    ) == {
        "title": "DSH 插件版",
        "subtitle": "2026-11-12 起 · 四次独立审查的问题都修完了",
        "detail": "进行中",
    }
    # A state too long for the one-line label moves into the subtitle instead of overflowing.
    long_state = {
        "name": "Windows 版",
        "state": "没合进 main，等用户决定",
        "when": "",
        "now": "CI 通过",
    }
    assert item(long_state) == {
        "title": "Windows 版",
        "subtitle": "没合进 main，等用户决定 · CI 通过",
    }
    assert item({"name": "空", "state": "", "when": "", "now": ""}) == {"title": "空"}
