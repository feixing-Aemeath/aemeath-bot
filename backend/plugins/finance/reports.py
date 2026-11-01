"""Immutable numeric reports; the Agent receives aggregates and appends commentary only."""

import json
import os
from datetime import date, timedelta

from core.db import dump
from core.documents import sha256
from core.knowledge_access import safe_path
from plugins.finance.config import SOURCES, FinanceError, cycle, yuan


def coverage_text(coverage):
    if not any(c["through"] for c in coverage.values()):
        return "还没导入任何账单"
    return (
        "；".join(
            f"{SOURCES[s]}账单截至 {c['through']}"
            + ("（仅按最晚交易日，未确认完整覆盖）" if c["basis"] != "statement" else "")
            if c["through"]
            else f"{SOURCES[s]}还没导入"
            for s, c in coverage.items()
        )
        # Only dates mean anything here: what comes after them is not in yet.
        + "；之后的还没导入"
    )


class Reports:
    def __init__(self, importer, ledger):
        self.importer, self.ledger, self.db = importer, ledger, ledger.db

    def row(self, start):
        rows = self.db.rows("SELECT * FROM finance_reports WHERE start=?", (str(start),))
        return rows[0] if rows else None

    def due(self, today):
        start, _ = cycle(today, self.ledger.config.get("allowance_day", 1))
        previous_start, end = cycle(
            start - timedelta(days=1), self.ledger.config.get("allowance_day", 1)
        )
        old = self.row(previous_start)
        if old:
            return (previous_start, end) if old["status"] != "ready" else None
        imports = self.db.rows("SELECT source,start,end,coverage FROM finance_imports")

        def covers(source):
            cursor = previous_start
            for item in sorted(
                (i for i in imports if i["source"] == source and i["coverage"] == "statement"),
                key=lambda i: i["start"],
            ):
                first, last = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
                if first > cursor:
                    break
                if last >= cursor:
                    cursor = last + timedelta(days=1)
                if cursor > end:
                    return True
            return False

        configured = self.ledger.config.get("sources", list(SOURCES))
        complete = all(covers(source) for source in configured)
        if complete or today >= start + timedelta(days=4):
            return previous_start, end
        return None

    def prepare(self, start, end, today, clock):
        self.importer.ensure()
        existing = self.row(start)
        if existing:
            return existing
        data = self.ledger.previous_comparison(start, end, today)
        summary = data["current"]
        # Include readable amounts in the aggregate input, never local top transactions.
        data["display_yuan"] = {
            k.removesuffix("_cents"): yuan(v)
            for k, v in summary.items()
            if k.endswith("_cents") and v is not None
        }
        lines = [
            f"# 记账月报 {start} — {end}",
            "",
            "## 代码计算",
            "",
            f"预算：{yuan(summary['budget_cents'])} 元"
            + ("（按设置算）" if summary["budget_from_settings"] else ""),
            f"已花：{yuan(summary['spent_cents'])} 元；剩余：{yuan(summary['remaining_cents'])} 元",
            f"待报销：{yuan(summary['pending_reimbursement_cents'])} 元；已报销：{yuan(summary['reimbursed_cents'])} 元",
            f"大件（自己的积蓄，不算在已花里）：{yuan(summary['big_cents'])} 元",
            f"额外收入：{yuan(summary['extra_income_cents'])} 元",
            "",
            "## 各类别与上周期比较",
            "",
            "| 类别 | 本周期（元） | 上周期（元） | 变化（元） |",
            "| --- | ---: | ---: | ---: |",
        ]
        lines += [
            f"| {category} | {yuan(value)} | {yuan(data['previous']['categories'][category])} | "
            f"{yuan(data['category_change_cents'][category])} |"
            for category, value in summary["categories"].items()
        ]
        lines += [
            "",
            "## 数据覆盖",
            "",
            coverage_text(summary["coverage"]),
            "",
            summary["coverage_note"],
            "",
            "## 最大的几笔（仅本地）",
            "",
        ]
        rows = [
            r
            for r in self.ledger.rows(True)
            if r["kind"] == "spend" and start <= self.ledger.day(r) <= end
        ]
        # The local file is private; never interpolate these rows into the Agent prompt/input.
        for row in sorted(rows, key=lambda r: r["amount_cents"], reverse=True)[:5]:
            counterpart = row["counterpart"].replace("\n", " ").replace("\r", " ")
            lines.append(f"- {self.ledger.day(row)} {counterpart}：{yuan(row['amount_cents'])} 元")
        lines += ["", "## 记账的话", ""]
        root = self.importer.context.memory.root
        folder = safe_path(root, self.importer.area + "/月报")
        folder.mkdir(parents=True, exist_ok=True)
        index = 1
        while True:
            name = f"{start:%Y-%m}" + ("" if index == 1 else f" ({index})") + ".md"
            target = safe_path(root, self.importer.area + "/月报/" + name)
            try:
                descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                    stream.write("\n".join(lines))
                break
            except FileExistsError:
                index += 1
        self.db.execute(
            "INSERT INTO finance_reports VALUES(?,?,?,?,?,'numeric',?)",
            (
                str(start),
                str(end),
                target.relative_to(root).as_posix(),
                sha256(target),
                dump(data),
                clock,
            ),
        )
        return self.row(start)

    def input(self, start):
        row = self.row(start)
        if row is None:
            raise FinanceError("月报数字尚未生成")
        return json.loads(row["input_json"])

    def submit(self, start, text, today):
        self.importer.ensure()
        row = self.row(start)
        if row is None:
            raise FinanceError("月报数字尚未生成")
        if row["status"] == "ready":
            return {"accepted": True, "path": row["path"]}
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
            raise FinanceError("月报文字长度无效")
        if any(
            word in text
            for word in ("买入", "卖出", "股票", "基金", "投资", "借贷", "贷款", "理财")
        ):
            raise FinanceError("只允许描述记账数字和花钱节奏")
        if any(
            r["counterpart"] and len(r["counterpart"]) >= 2 and r["counterpart"] in text
            for r in self.ledger.rows()
        ):
            raise FinanceError("月报解读只能包含汇总")
        target = safe_path(self.importer.context.memory.root, row["path"])
        if sha256(target) != row["sha256"]:
            raise FinanceError("月报已被用户修改，保留原文件")
        # Append only; never replace or truncate a user's file. A changed file is refused.
        with target.open("a", encoding="utf-8", newline="") as stream:
            stream.write(text.strip() + "\n")
        self.db.execute(
            "UPDATE finance_reports SET status='ready',sha256=? WHERE start=?",
            (sha256(target), str(start)),
        )
        self.importer.context.store.digest(
            str(today), "finance", f"记账的月报（{start}）：\n{text.strip()}", append=True
        )
        return {"accepted": True, "path": row["path"]}


def report_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise FinanceError("月报周期日期无效") from None
