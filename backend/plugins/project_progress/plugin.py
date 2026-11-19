"""知识库的项目进度: commits counted live from local git repos, the rest from one Markdown file."""

import asyncio
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.knowledge_access import safe_path
from core.plugins import Plugin as BasePlugin

# The repository the backend runs from: backend/plugins/project_progress/plugin.py.
OWN_REPO = Path(__file__).resolve().parents[3]
DEFAULT_FILE = "Bots/知识库/项目进度.md"
CACHE_SECONDS = 300
MAX_FILE_BYTES = 200_000
PR_MERGE = re.compile(r"^Merge pull request #(\d+)\b")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FIELD = re.compile(r"^[-*]\s+([^：:]{1,10})[：:]\s*(.*?)\s*$")
TASK = re.compile(r"^[-*]\s+(?:\[( |x|X)\]\s+)?(.*?)\s*$")
MILESTONE = re.compile(r"^[-*]\s+(\d{4}-\d{2}-\d{2})[\s：:]+(.*?)\s*$")
STREAM_KEYS = {"状态": "state", "时间": "when", "现在": "now", "下一步": "next"}
# The App draws a row's detail as a one-line label; longer states go into the subtitle.
STATE_LABEL_CHARS = 8


def parse_progress(text):
    """The progress file's three sections; anything else in the file is ignored.

    ## 工作线 -> ### <name> with `- 状态：…`, `- 时间：…`, `- 现在：…`, `- 下一步：…`
    ## 接下来 -> `- [ ] item` (open) or `- [x] item` (done, left off the card)
    ## 里程碑 -> `- 2026-11-12 what happened`
    """
    streams, tasks, milestones = [], [], []
    section = stream = None
    for raw in text.splitlines():
        line = raw.strip()
        heading = HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            if level <= 2:
                section, stream = title, None
            elif level == 3 and section == "工作线":
                stream = {"name": title, "state": "", "when": "", "now": "", "next": ""}
                streams.append(stream)
            continue
        if section == "工作线" and stream is not None:
            field = FIELD.match(line)
            if field and field.group(1).strip() in STREAM_KEYS:
                stream[STREAM_KEYS[field.group(1).strip()]] = field.group(2)
        elif section == "接下来":
            task = TASK.match(line)
            if task and task.group(2):
                tasks.append({"text": task.group(2), "done": (task.group(1) or " ") != " "})
        elif section == "里程碑":
            milestone = MILESTONE.match(line)
            if milestone:
                try:
                    day = date.fromisoformat(milestone.group(1))
                except ValueError:
                    continue
                milestones.append({"date": day, "text": milestone.group(2)})
    return {"streams": streams, "tasks": tasks, "milestones": milestones}


def git(path, *args):
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
        env=env,
    )
    return result.stdout


def read_repo(path, zone):
    """Every commit on a local branch (merged or not), by author day, and distinct PR merges."""
    days, seen = {}, set()
    for line in git(path, "log", "--branches", "--format=%H %at").splitlines():
        sha, _, stamp = line.partition(" ")
        if not stamp or sha in seen:
            continue
        seen.add(sha)
        day = datetime.fromtimestamp(int(stamp), zone).date()
        days[day] = days.get(day, 0) + 1
    prs = set()
    for subject in git(
        path, "log", "--branches", "--remotes", "--merges", "--format=%s"
    ).splitlines():
        merge = PR_MERGE.match(subject)
        if merge:
            prs.add(int(merge.group(1)))
    return {"days": days, "commits": len(seen), "prs": len(prs)}


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.repos = [
            {"name": repo["name"], "path": Path(repo["path"]).expanduser()}
            for repo in config.get("repos") or []
        ] or [{"name": "主仓库", "path": OWN_REPO}]
        self.progress_file = config.get("progress_file", DEFAULT_FILE)
        self.chart_days = config.get("chart_days", 14)
        self._git_cache = None

    def zone(self):
        return ZoneInfo(self.context.store.settings.timezone)

    def today(self):
        return datetime.now(self.zone()).date()

    async def cards(self):
        return await asyncio.to_thread(self.build)

    def git_stats(self):
        if self._git_cache and time.monotonic() - self._git_cache[0] < CACHE_SECONDS:
            return self._git_cache[1]
        zone, stats = self.zone(), []
        for repo in self.repos:
            try:
                stats.append({"name": repo["name"], **read_repo(repo["path"], zone)})
            except (OSError, subprocess.SubprocessError, ValueError):
                stats.append({"name": repo["name"], "error": True})
        self._git_cache = (time.monotonic(), stats)
        return stats

    def read_progress(self):
        """(parsed, problem). The file is the user's or 知识库's; a missing file is not an error."""
        root = getattr(self.context.memory, "root", None)
        if root is None:
            return None, "没有记忆目录"
        try:
            path = safe_path(Path(root), self.progress_file)
        except ValueError:
            return None, "进度文件的位置不在记忆目录里"
        if not path.is_file():
            return None, None
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, "进度文件太大（上限 200 KB）"
        try:
            return parse_progress(path.read_text(encoding="utf-8")), None
        except (OSError, UnicodeDecodeError):
            return None, "进度文件读不出来，请存成 UTF-8"

    def build(self):
        today = self.today()
        stats = self.git_stats()
        progress, problem = self.read_progress()
        cards = []
        readable = [s for s in stats if not s.get("error")]
        active = [s["name"] for s in (progress or {}).get("streams", []) if s["state"] == "进行中"]
        if readable:
            first = min((day for s in readable for day in s["days"]), default=None)
            parts = [f"{sum(s['commits'] for s in readable)} 次提交"]
            prs = sum(s["prs"] for s in readable)
            if prs:
                parts.append(f"{prs} 个合并的 PR")
            if active:
                parts.append("在做：" + "、".join(active))
            unreadable = len(stats) - len(readable)
            if unreadable:
                parts.append(f"{unreadable} 个仓库读不到")
            cards.append(
                {
                    "type": "stat",
                    "title": "开发天数",
                    "data": {
                        "value": (today - first).days + 1 if first else 0,
                        "unit": "天",
                        "label": " · ".join(parts),
                        "symbol": "hammer",
                        "wide": True,
                    },
                }
            )
            cards.append(self.commit_chart(readable, today))
        else:
            cards.append(
                {
                    "type": "text",
                    "title": "开发天数",
                    "data": {"text": "读不到 git 仓库：在插件设置里检查 repos 的路径。"},
                }
            )
        if progress is None:
            cards.append(
                {
                    "type": "text",
                    "title": "项目进度",
                    "data": {
                        "text": problem
                        or f"还没有进度文件。在记忆目录里建 `{self.progress_file}`，写上工作线、接下来和里程碑，这里就会显示。",
                    },
                }
            )
            return cards
        if progress["streams"]:
            cards.append(
                {
                    "type": "list",
                    "title": "工作线",
                    "data": {"items": [self.stream_item(s) for s in progress["streams"]]},
                }
            )
        open_tasks = [t["text"] for t in progress["tasks"] if not t["done"]]
        if open_tasks:
            cards.append({"type": "list", "title": "接下来", "data": {"items": open_tasks[:8]}})
        past = sorted(
            (m for m in progress["milestones"] if m["date"] <= today),
            key=lambda m: m["date"],
            reverse=True,
        )
        if past:
            cards.append(
                {
                    "type": "list",
                    "title": "最近的里程碑",
                    "data": {
                        "items": [
                            {"title": m["text"], "subtitle": f"{m['date'].month}/{m['date'].day}"}
                            for m in past[:5]
                        ]
                    },
                }
            )
        return cards

    @staticmethod
    def stream_item(stream):
        """A list row's `detail` sits on the right on one line, sized to its text, so it is
        for a short label like 运行中. The long 现在 text goes in the subtitle, which wraps
        under the name; a state too long for the label joins it there."""
        state, when, now = stream["state"], stream["when"], stream["now"]
        short = state if state and len(state) <= STATE_LABEL_CHARS else ""
        subtitle = " · ".join(x for x in (state if not short else "", when, now) if x)
        return {
            "title": stream["name"],
            **({"subtitle": subtitle} if subtitle else {}),
            **({"detail": short} if short else {}),
        }

    def commit_chart(self, stats, today):
        days = [today - timedelta(days=n) for n in range(self.chart_days - 1, -1, -1)]
        label = lambda d: f"{d.month}/{d.day}"  # noqa: E731
        title = f"最近 {self.chart_days} 天的提交"
        if len(stats) == 1:
            counts = stats[0]["days"]
            points = [
                {
                    "x": label(d),
                    "y": counts.get(d, 0),
                    **({"label": str(counts[d])} if counts.get(d) else {}),
                }
                for d in days
            ]
            return {
                "type": "chart",
                "title": title,
                "data": {"kind": "bar", "unit": "次", "points": points},
            }
        series = [
            {"name": s["name"], "points": [{"x": label(d), "y": s["days"].get(d, 0)} for d in days]}
            for s in stats
        ]
        return {
            "type": "chart",
            "title": title,
            "data": {"kind": "bar", "unit": "次", "series": series},
        }
