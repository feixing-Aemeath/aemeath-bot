"""Private SQLite ledger, reconciliation and integer-cent arithmetic. No model calls."""

import re
from datetime import date, timedelta

from core.db import uid
from plugins.finance.categories import AWAY, CATEGORIES, categorize, takeout
from plugins.finance.config import (
    FinanceError,
    allowance_anchor,
    anchor,
    at_home,
    cycle,
    money,
    shift_month,
    timestamp,
)
from plugins.finance.parsers import OWN_ACCOUNT

SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_imports(
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, source TEXT, start TEXT, end TEXT,
 row_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, archive_path TEXT,
 coverage TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_files(
 path TEXT PRIMARY KEY, sha256 TEXT, status TEXT NOT NULL, error TEXT, archive_path TEXT,
 updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_transactions(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), direction TEXT NOT NULL, kind TEXT NOT NULL,
 counterpart TEXT NOT NULL DEFAULT '', item TEXT NOT NULL DEFAULT '', method TEXT NOT NULL DEFAULT '',
 category TEXT NOT NULL, reimburse TEXT NOT NULL, linked_id TEXT, note TEXT NOT NULL DEFAULT '',
 import_id TEXT, created_at TEXT NOT NULL, merchant_id TEXT NOT NULL DEFAULT '',
 original_id TEXT NOT NULL DEFAULT '', full_refund INTEGER NOT NULL DEFAULT 0, refund_of TEXT,
 category_override INTEGER NOT NULL DEFAULT 0, note_override INTEGER NOT NULL DEFAULT 0,
 notified_at TEXT, reimburse_override INTEGER NOT NULL DEFAULT 0,
 extra_type TEXT NOT NULL DEFAULT '', refunded_cents INTEGER NOT NULL DEFAULT 0,
 big INTEGER NOT NULL DEFAULT 0, split_cents INTEGER NOT NULL DEFAULT 0, UNIQUE(source,source_id));
CREATE TABLE IF NOT EXISTS finance_rules(counterpart TEXT PRIMARY KEY, category TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_balances(account TEXT NOT NULL, at TEXT NOT NULL,
 amount_cents INTEGER NOT NULL, origin TEXT NOT NULL, PRIMARY KEY(account, at, origin));
CREATE TABLE IF NOT EXISTS finance_home(start TEXT NOT NULL, end TEXT NOT NULL,
 PRIMARY KEY(start, end));
CREATE TABLE IF NOT EXISTS finance_reports(
 start TEXT PRIMARY KEY, end TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def card_digits(method):
    """「招商银行储蓄卡(1234)」, 「招商银行(1234)」 -> 「1234」."""
    match = re.search(r"\((\d{4})\)", method or "")
    return match[1] if match else None


class Ledger:
    def __init__(self, db, config, zone):
        self.db, self.config, self.zone = db, config, zone
        with db.connect() as conn:
            conn.executescript(SCHEMA)
            # Added after the first release: how much of a purchase WeChat says came back,
            # whether the user bought it from their savings, and how much of it is a part of
            # its own.
            columns = {r[1] for r in conn.execute("PRAGMA table_info(finance_transactions)")}
            for column in ("refunded_cents", "big", "split_cents"):
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE finance_transactions "
                        f"ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            # Earlier readers kept the holder's own name on transfers between their accounts
            # (the only neutral 招商银行 rows); a counterparty rule may name them too.
            own = "source='cmb' AND kind='transfer' AND direction='neutral' AND counterpart!=?"
            conn.execute(
                f"DELETE FROM finance_rules WHERE counterpart IN "
                f"(SELECT counterpart FROM finance_transactions WHERE {own})",
                (OWN_ACCOUNT,),
            )
            conn.execute(
                f"UPDATE finance_transactions SET counterpart=? WHERE {own}",
                (OWN_ACCOUNT, OWN_ACCOUNT),
            )

    def family(self, row):
        return any(name in row["counterpart"] for name in self.config.get("family", []))

    def family_pays(self, row):
        """A charge the family pays back whatever day it is (AI memberships, API credit): a
        `family_pays` keyword in the merchant or the item."""
        text = (row.get("counterpart", "") + " " + row.get("item", "")).casefold()
        return any(word.casefold() in text for word in self.config.get("family_pays", []))

    def default_reimburse(self, row, at, clock, periods):
        """What a charge the user has said nothing about is: the family's to pay back (yes),
        spent at home (ask), or the user's own (no)."""
        if self.family_pays(row):
            return "yes"
        if row["category"] not in AWAY and self.home(at, clock, periods):
            return "ask"
        return "no"

    def day(self, row):
        return timestamp(row["occurred_at"], self.zone).date()

    def rows(self, visible=False):
        return self.db.rows(
            "SELECT * FROM finance_transactions"
            + (" WHERE linked_id IS NULL" if visible else "")
            + " ORDER BY occurred_at,id"
        )

    def get(self, identity):
        rows = self.db.rows("SELECT * FROM finance_transactions WHERE id=?", (identity,))
        if not rows:
            raise FinanceError("没有这笔交易")
        row = rows[0]
        return self.get(row["linked_id"]) if row["linked_id"] else row

    def prepare(self, source, raw, import_id, clock):
        row = {
            "id": uid(),
            "source": source,
            "import_id": import_id,
            "created_at": clock,
            "linked_id": None,
            "reimburse": "no",
            "category_override": 0,
            "note_override": 0,
            "merchant_id": "",
            "original_id": "",
            "full_refund": 0,
            "refunded_cents": 0,
            "big": 0,
            "refund_of": None,
            "notified_at": None,
            "reimburse_override": 0,
            "extra_type": "",
            **raw,
        }
        at = timestamp(row["occurred_at"], self.zone)
        text = row.get("counterpart", "") + " " + row.get("item", "")
        if row["direction"] == "in" and row["kind"] == "income":
            if (
                source == "wechat" and ("红包" in text or row["extra_type"] == "red_packet")
            ) or any(k in text for k in self.config.get("extra_income_keywords", ["分红"])):
                row["kind"] = "extra_income"
            elif source == "cmb":
                anchor = allowance_anchor(at.date(), self.config.get("allowance_day", 1))
                family = self.config.get("family", [])
                # It may come a few days late (on the 6th instead of the 1st), rarely early.
                if (
                    -2 <= (at.date() - anchor).days <= 7
                    and row["amount_cents"] * 100
                    >= money(self.config.get("allowance_amount", 2000)) * 80
                    and (not family or any(name in row["counterpart"] for name in family))
                ):
                    row["kind"] = "allowance"
        if row["kind"] == "extra_income" and "红包" in text + row.get("note", ""):
            row["extra_type"] = "red_packet"
        remembered = {
            r["counterpart"]: r["category"] for r in self.db.rows("SELECT * FROM finance_rules")
        }
        row.setdefault(
            "category",
            categorize(
                row.get("counterpart", ""),
                row.get("item", ""),
                remembered,
                self.config.get("rules", {}),
            ),
        )
        if row["kind"] == "spend":
            row["reimburse"] = self.default_reimburse(row, at, clock, self.home_periods())
        return row

    def home_periods(self):
        return [
            (date.fromisoformat(r["start"]), date.fromisoformat(r["end"]))
            for r in self.db.rows("SELECT * FROM finance_home ORDER BY start")
        ]

    def home(self, at, clock, periods):
        """Whether spending at `at` was at home, so the family pays it back: within a period
        the user named, or on a weekend when every weekend counts (recent ones only, so a
        first import of months of bills does not ask about every past weekend)."""
        if any(start <= at.date() <= end for start, end in periods):
            return True
        if not self.config.get("every_weekend_at_home", False):
            return False
        recent, _ = cycle(timestamp(clock, self.zone).date(), self.config.get("allowance_day", 1))
        recent = shift_month(recent, -1, self.config.get("allowance_day", 1))
        return at_home(at, self.config) and at.date() >= recent

    def set_home(self, start, end, remove, clock):
        if not start <= end or (end - start).days > 62:
            raise FinanceError("在家的日子要从早到晚，最长两个月")
        if remove:
            self.db.execute(
                "DELETE FROM finance_home WHERE start=? AND end=?", (str(start), str(end))
            )
        else:
            self.db.execute(
                "INSERT OR IGNORE INTO finance_home VALUES(?,?)", (str(start), str(end))
            )
        self.refresh_reimburse(clock)
        return {
            "periods": [{"start": str(a), "end": str(b)} for a, b in self.home_periods()],
            "ask": len([r for r in self.rows(True) if r["reimburse"] == "ask"]),
        }

    def refresh_reimburse(self, clock):
        """Judge again what the user has not decided, after the home or family-pays rules
        changed; what was paid back stays so."""
        periods = self.home_periods()
        with self.db.connect() as conn:
            for r in self.rows():
                if (
                    r["kind"] != "spend"
                    or r["reimburse_override"]
                    or r["reimburse"] not in {"ask", "no", "yes"}
                ):
                    continue
                wanted = self.default_reimburse(
                    r, timestamp(r["occurred_at"], self.zone), clock, periods
                )
                if wanted != r["reimburse"]:
                    conn.execute(
                        "UPDATE finance_transactions SET reimburse=? WHERE id=?", (wanted, r["id"])
                    )
        # What the family already sent may pay back a charge that is theirs only now.
        self.reconcile()

    @staticmethod
    def insert(conn, row):
        existing = conn.execute(
            "SELECT * FROM finance_transactions WHERE source=? AND source_id=?",
            (row["source"], row["source_id"]),
        ).fetchone()
        if existing:
            if any(existing[k] != row[k] for k in ("amount_cents", "direction")):
                raise FinanceError("同一单号的账单内容冲突")
            conn.execute(
                "UPDATE finance_transactions SET full_refund=max(full_refund,?),"
                "refunded_cents=max(refunded_cents,?) WHERE id=?",
                (row["full_refund"], row["refunded_cents"], existing["id"]),
            )
            return False
        conn.execute(
            f"INSERT INTO finance_transactions({','.join(row)}) "
            f"VALUES({','.join('?' for _ in row)})",
            tuple(row.values()),
        )
        return True

    def imported(self, statement, digest, clock):
        identity = uid()
        record = (
            "INSERT INTO finance_imports(id,sha256,source,start,end,row_count,status,"
            "coverage,created_at) VALUES(?,?,?,?,?,?,'imported',?,?)",
            (
                identity,
                digest,
                statement.source,
                str(statement.start),
                str(statement.end),
                len(statement.rows),
                statement.coverage,
                clock,
            ),
        )
        return identity, self.add_rows(statement, identity, clock, record)

    def add_rows(self, statement, import_id, clock, record=None):
        """Add a statement's rows the ledger does not have yet; those it has stay as they are.
        `record` (the import's own row) goes in the same transaction."""
        rows = [self.prepare(statement.source, r, import_id, clock) for r in statement.rows]
        # One allowance a period, the first: another large transfer that week is something else.
        day = self.config.get("allowance_day", 1)
        taken = {
            allowance_anchor(self.day(r), day) for r in self.rows() if r["kind"] == "allowance"
        }
        for row in sorted(rows, key=lambda r: r["occurred_at"]):
            if row["kind"] == "allowance":
                anchor = allowance_anchor(self.day(row), day)
                if anchor in taken and not self.db.rows(
                    "SELECT 1 FROM finance_transactions WHERE source=? AND source_id=?",
                    (row["source"], row["source_id"]),
                ):
                    row["kind"] = "income"
                taken.add(anchor)
        with self.db.connect() as conn:
            if record:
                conn.execute(*record)
            count = sum(self.insert(conn, row) for row in rows)
            for account, cents in statement.balances.items():
                conn.execute(
                    "INSERT OR REPLACE INTO finance_balances VALUES(?,?,?,'statement')",
                    (account, str(statement.end), cents),
                )
        self.reconcile()
        return count

    def add(self, args, clock):
        direction = args.get("direction", "out")
        kind = args.get("kind", "spend" if direction == "out" else "income")
        if direction not in {"out", "in", "neutral"} or kind not in {
            "spend",
            "refund",
            "allowance",
            "income",
            "extra_income",
            "transfer",
            "reimbursement",
        }:
            raise FinanceError("收支类型无效")
        if (
            (kind == "spend" and direction != "out")
            or (
                kind in {"refund", "allowance", "income", "extra_income", "reimbursement"}
                and direction != "in"
            )
            or (direction == "neutral" and kind != "transfer")
        ):
            raise FinanceError("收支方向与类型不一致")
        category = args.get("category", "其他")
        if category not in CATEGORIES:
            raise FinanceError("类别无效")
        raw = {
            "source_id": args.get("request_id") or uid(),
            "occurred_at": timestamp(args.get("occurred_at", clock), self.zone).isoformat(),
            "amount_cents": money(args.get("amount")),
            "direction": direction,
            "kind": kind,
            "counterpart": args.get("counterpart", ""),
            "item": args.get("item", ""),
            "method": "手动",
            "note": args.get("note", ""),
            "category": category,
            "category_override": int("category" in args),
            "note_override": int("note" in args),
        }
        row = self.prepare("manual", raw, None, clock)
        with self.db.connect() as conn:
            self.insert(conn, row)
        self.reconcile()
        match = self.db.rows(
            "SELECT id FROM finance_transactions WHERE source='manual' AND source_id=?",
            (raw["source_id"],),
        )[0]
        return self.get(match["id"])

    def reconcile(self):
        rows = self.rows()
        # Stable one-to-one matches; ambiguous equal-amount candidates remain visible.
        with self.db.connect() as conn:
            used = {r["linked_id"] for r in rows if r["linked_id"]}
            for bank in rows:
                # The bank names the merchant, not 财付通 or 支付宝, for a WeChat or Alipay
                # payment from the card; the platform names the card with its last digits.
                if bank["source"] != "cmb" or bank["linked_id"] or bank["kind"] == "transfer":
                    continue
                card = card_digits(bank["method"])
                candidates = [
                    p
                    for p in rows
                    if p["source"] in {"wechat", "alipay"}
                    and not p["linked_id"]
                    and p["id"] not in used
                    and ("招商" in p["method"] or "招行" in p["method"])
                    and not (card and card_digits(p["method"]) not in {None, card})
                    and p["amount_cents"] == bank["amount_cents"]
                    and (p["direction"] == bank["direction"] or p["kind"] == "transfer")
                    and abs((self.day(p) - self.day(bank)).days) <= 2
                ]
                if len(candidates) == 1:
                    p = candidates[0]
                    bank["linked_id"] = p["id"]
                    used.add(p["id"])
                    conn.execute(
                        "UPDATE finance_transactions SET linked_id=? WHERE id=?",
                        (p["id"], bank["id"]),
                    )
                    self.carry(conn, bank, p)
            # Manual notes follow the imported transaction, including after a bank link.
            used_manual = set()
            for manual in rows:
                if manual["source"] != "manual" or manual["kind"] != "spend":
                    continue
                existing = manual["linked_id"]
                candidates = [
                    p
                    for p in rows
                    if p["source"] != "manual"
                    and not p["linked_id"]
                    and p["kind"] == "spend"
                    and p["amount_cents"] == manual["amount_cents"]
                    and abs(
                        (
                            timestamp(p["occurred_at"], self.zone)
                            - timestamp(manual["occurred_at"], self.zone)
                        ).total_seconds()
                    )
                    <= 86400
                    and p["id"] not in used_manual
                ]
                if existing:
                    target = next((p for p in rows if p["id"] == existing), None)
                    if target and target["linked_id"]:
                        existing = target["linked_id"]
                    candidates = [p for p in rows if p["id"] == existing]
                if len(candidates) == 1:
                    p = candidates[0]
                    used_manual.add(p["id"])
                    manual["linked_id"] = p["id"]
                    conn.execute(
                        "UPDATE finance_transactions SET linked_id=? WHERE id=?",
                        (p["id"], manual["id"]),
                    )
                    for field in ("category", "note"):
                        if manual[field + "_override"]:
                            p[field] = manual[field]
                            conn.execute(
                                f"UPDATE finance_transactions SET {field}=?,{field}_override=1 WHERE id=?",
                                (manual[field], p["id"]),
                            )
                    # Never back from 已报销 to 待报销: the money came after the manual note.
                    if (
                        manual["reimburse_override"] or manual["reimburse"] in {"yes", "reimbursed"}
                    ) and not (p["reimburse"] == "reimbursed" and manual["reimburse"] == "yes"):
                        p["reimburse"] = manual["reimburse"]
                        conn.execute(
                            "UPDATE finance_transactions SET reimburse=? WHERE id=?",
                            (p["reimburse"], p["id"]),
                        )
                    if manual["notified_at"]:
                        conn.execute(
                            "UPDATE finance_transactions SET notified_at=? WHERE id=?",
                            (manual["notified_at"], p["id"]),
                        )
                    if manual["big"]:
                        p["big"] = 1
                        conn.execute("UPDATE finance_transactions SET big=1 WHERE id=?", (p["id"],))
            visible = [r for r in rows if not r["linked_id"]]
            for refund in visible:
                if refund["kind"] != "refund" or refund["refund_of"]:
                    continue
                candidates = [
                    p
                    for p in visible
                    if p["source"] == refund["source"]
                    and p["kind"] == "spend"
                    and self.day(refund) >= self.day(p)
                    and refund["original_id"]
                    and refund["original_id"] == p["source_id"]
                ]
                if not candidates and refund["merchant_id"]:
                    candidates = [
                        p
                        for p in visible
                        if p["source"] == refund["source"]
                        and p["kind"] == "spend"
                        and self.day(refund) >= self.day(p)
                        and p["merchant_id"] == refund["merchant_id"]
                    ]
                if not candidates:
                    candidates = [
                        p
                        for p in visible
                        if p["kind"] == "spend"
                        and p["source"] == refund["source"]
                        and refund["counterpart"]
                        and p["counterpart"] == refund["counterpart"]
                        and p["amount_cents"] == refund["amount_cents"]
                        and 0 <= (self.day(refund) - self.day(p)).days <= 30
                    ]
                if not candidates:
                    # WeChat gives a refund row its own order number and a platform name
                    # (「美团平台商户」), but marks the purchase as refunded: tie them by amount.
                    # A partly refunded purchase can take several refunds, up to what WeChat
                    # says came back.
                    back = {}
                    for r in visible:
                        if r["refund_of"]:
                            back[r["refund_of"]] = back.get(r["refund_of"], 0) + r["amount_cents"]
                    candidates = [
                        p
                        for p in visible
                        if p["kind"] == "spend"
                        and p["source"] == refund["source"]
                        and 0 <= (self.day(refund) - self.day(p)).days <= 60
                        and (
                            (
                                p["full_refund"]
                                and p["id"] not in back
                                and p["amount_cents"] == refund["amount_cents"]
                            )
                            or back.get(p["id"], 0) + refund["amount_cents"] <= p["refunded_cents"]
                        )
                    ]
                    candidates = candidates[-1:]  # the latest purchase before the refund
                if len(candidates) == 1:
                    refund["refund_of"] = candidates[0]["id"]
                    conn.execute(
                        "UPDATE finance_transactions SET refund_of=? WHERE id=?",
                        (candidates[0]["id"], refund["id"]),
                    )
            # Reimbursements arriving after confirmed or family-paid spending, not other income:
            # a transfer, or a red packet from family (they pay some back that way).
            family = self.config.get("family", [])
            for income in visible:
                from_family = any(n in income["counterpart"] for n in family)
                if (
                    income["source"] not in {"cmb", "wechat"}
                    or income["direction"] != "in"
                    or not (
                        (income["kind"] == "income" and (from_family or not family))
                        or (
                            income["kind"] == "extra_income"
                            and income["extra_type"] == "red_packet"
                            and from_family
                        )
                    )
                ):
                    continue
                pending = [
                    p
                    for p in visible
                    if p["kind"] == "spend"
                    and p["reimburse"] == "yes"
                    and 0 <= (self.day(income) - self.day(p)).days <= 14
                ]
                if not pending:
                    continue
                costs = self.costs(visible, self.day(income))
                if abs(sum(costs[p["id"]] for p in pending) - income["amount_cents"]) > 100:
                    # One family-paid charge paid back on its own within days, rounded up to
                    # ten yuan (45 back as 50); the latest such charge.
                    pending = [
                        p
                        for p in pending
                        if self.family_pays(p)
                        and (self.day(income) - self.day(p)).days <= 3
                        and 0
                        < costs[p["id"]]
                        <= income["amount_cents"]
                        <= -(-costs[p["id"]] // 1000) * 1000
                    ][-1:]
                if pending:
                    for p in pending:
                        p["reimburse"] = "reimbursed"
                        # The rows merged into it too, or a manual one copies 待报销 back.
                        conn.execute(
                            "UPDATE finance_transactions SET reimburse='reimbursed' "
                            "WHERE id=? OR linked_id=?",
                            (p["id"], p["id"]),
                        )
                    income["kind"] = "reimbursement"
                    conn.execute(
                        "UPDATE finance_transactions SET kind='reimbursement' WHERE id=?",
                        (income["id"],),
                    )

    @staticmethod
    def carry(conn, bank, platform):
        """What the user decided on a bank row before its WeChat or Alipay row came in (大件, a
        category, a note, reimbursement) moves to the platform row, which is the one shown."""
        moved = {}
        if bank["big"] and not platform["big"]:
            moved["big"] = 1
        for field in ("category", "note", "reimburse"):
            if bank[field + "_override"] and not platform[field + "_override"]:
                moved |= {field: bank[field], field + "_override": 1}
        if "reimburse" not in moved and bank["reimburse"] in {"yes", "reimbursed"}:
            if platform["reimburse"] not in {"yes", "reimbursed"}:
                moved["reimburse"] = bank["reimburse"]
        if bank["notified_at"] and not platform["notified_at"]:
            moved["notified_at"] = bank["notified_at"]
        if moved:
            platform.update(moved)
            conn.execute(
                f"UPDATE finance_transactions SET {','.join(k + '=?' for k in moved)} WHERE id=?",
                (*moved.values(), platform["id"]),
            )

    def categorize(self, identity, category, remember):
        if category not in CATEGORIES:
            raise FinanceError("类别无效")
        row = self.get(identity)
        with self.db.connect() as conn:
            if remember and row["counterpart"]:
                conn.execute(
                    "INSERT OR REPLACE INTO finance_rules VALUES(?,?)",
                    (row["counterpart"], category),
                )
                # A part split off keeps the category it was given.
                conn.execute(
                    "UPDATE finance_transactions SET category=?,category_override=1 "
                    "WHERE counterpart=? AND source!='split'",
                    (category, row["counterpart"]),
                )
            else:
                conn.execute(
                    "UPDATE finance_transactions SET category=?,category_override=1 WHERE id=?",
                    (category, row["id"]),
                )
            # Keep a merged manual record from reverting this edit on the next import.
            conn.execute(
                "UPDATE finance_transactions SET category=?,category_override=0 WHERE linked_id=? AND source='manual'",
                (category, row["id"]),
            )
        return self.get(row["id"])

    def change_kind(self, identity, kind):
        row = self.get(identity)
        if row["direction"] != "in" or kind not in {
            "income",
            "extra_income",
            "allowance",
            "reimbursement",
            "transfer",
        }:
            raise FinanceError("只能把转入改成收入、额外收入、生活费、报销或转账")
        self.db.execute("UPDATE finance_transactions SET kind=? WHERE id=?", (kind, row["id"]))
        return self.get(row["id"])

    def mark_big(self, identity, big, note=None):
        """A big purchase from the user's savings (a phone, a watch) is listed on its own and
        stays out of the period's living budget. `note` names it in the list."""
        row = self.get(identity)
        if row["kind"] != "spend":
            raise FinanceError("只能把支出记成大件")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE finance_transactions SET big=? WHERE id=? OR linked_id=?",
                (int(big), row["id"], row["id"]),
            )
            if note is not None:
                conn.execute(
                    "UPDATE finance_transactions SET note=?,note_override=1 WHERE id=?",
                    (note, row["id"]),
                )
        return self.get(row["id"])

    def bigs(self, start, end):
        """Big purchases in [start, end] the user paid for, with what each cost after refunds."""
        rows = self.rows(True)
        costs = self.costs(rows, end)
        return [
            (r, costs[r["id"]])
            for r in rows
            if r["kind"] == "spend"
            and r["big"]
            and r["reimburse"] not in {"yes", "reimbursed"}
            and start <= self.day(r) <= end
        ]

    def split(self, identity, amount, category, clock):
        """Part of a charge that is unlike the rest, as a game top-up inside an Apple charge
        the family pays for: a row of its own, the user's, in `category`; the charge keeps the
        rest. Again on the same charge replaces the part; amount 0 joins them back."""
        row = self.get(identity)
        if row["kind"] != "spend" or row["source"] == "split":
            raise FinanceError("只能拆开一笔支出")
        if category not in CATEGORIES:
            raise FinanceError("类别无效")
        cents = 0 if amount == 0 and not isinstance(amount, bool) else money(amount)
        if cents >= row["amount_cents"]:
            raise FinanceError("拆出来的部分要少于整笔")
        part = None
        if cents:
            part = self.prepare(
                "split",
                {
                    "source_id": row["id"],
                    "occurred_at": row["occurred_at"],
                    "amount_cents": cents,
                    "direction": "out",
                    "kind": "spend",
                    "counterpart": row["counterpart"],
                    "item": row["item"],
                    "method": row["method"],
                    "note": row["note"],
                    "category": category,
                    "category_override": 1,
                },
                None,
                clock,
            )
            part |= {"reimburse": "no", "reimburse_override": 1}
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM finance_transactions WHERE source='split' AND source_id=?",
                (row["id"],),
            )
            conn.execute(
                "UPDATE finance_transactions SET split_cents=? WHERE id=?", (cents, row["id"])
            )
            if part:
                self.insert(conn, part)
        return {"charge": self.get(row["id"]), "part": part and self.get(part["id"])}

    def reimburse(self, ids, decision):
        if decision not in {"yes", "no"}:
            raise FinanceError("报销决定只能是 yes 或 no")
        rows = self.selected(ids, "ask")
        with self.db.connect() as conn:
            for row in rows:
                conn.execute(
                    "UPDATE finance_transactions SET reimburse=?,reimburse_override=1 WHERE id=?",
                    (decision, row["id"]),
                )
                conn.execute(
                    "UPDATE finance_transactions SET reimburse=?,reimburse_override=1 WHERE linked_id=?",
                    (decision, row["id"]),
                )
        self.reconcile()
        return {"updated": len(rows)}

    def selected(self, ids, status):
        if ids == "all_ask" and status == "ask":
            return [r for r in self.rows(True) if r["reimburse"] == status and r["kind"] == "spend"]
        if ids is None and status == "yes":
            return [r for r in self.rows(True) if r["reimburse"] == status and r["kind"] == "spend"]
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) > 500
            or not all(isinstance(i, str) for i in ids)
        ):
            raise FinanceError("交易 ID 列表无效")
        rows = {self.get(i)["id"]: self.get(i) for i in ids}
        if any(r["kind"] != "spend" for r in rows.values()):
            raise FinanceError("只能确认支出的报销")
        return list(rows.values())

    def reimbursed(self, ids, amount):
        all_rows = self.rows(True)
        costs = self.costs(all_rows, max((self.day(r) for r in all_rows), default=date.min))
        rows = self.selected(ids, "yes")
        if any(r["reimburse"] != "yes" for r in rows):
            raise FinanceError("这些支出还没有确认报销")
        if amount is not None and abs(money(amount) - sum(costs[r["id"]] for r in rows)) > 100:
            raise FinanceError("到账金额与选中报销合计不符，请指定交易")
        with self.db.connect() as conn:
            for row in rows:
                conn.execute(
                    "UPDATE finance_transactions SET reimburse='reimbursed' WHERE id=? OR linked_id=?",
                    (row["id"], row["id"]),
                )
        return {"updated": len(rows)}

    def set_balance(self, account, amount, at, remove):
        """What the user says an account holds now (or on `at`); remove forgets the account."""
        if not isinstance(account, str) or not 1 <= len(account.strip()) <= 30 or "\n" in account:
            raise FinanceError("账户名称要 1–30 个字")
        account = account.strip()
        if remove:
            self.db.execute("DELETE FROM finance_balances WHERE account=?", (account,))
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO finance_balances VALUES(?,?,?,'user')",
                (account, str(at), money(amount, signed=True)),
            )
        return self.assets()

    def assets(self):
        """What the user has: the newest balance known for each account, from a statement or
        from the user (who wins on the same day). A bank's total (「招商银行」, as its App
        shows) stands for all its cards until a newer statement gives the cards'."""
        latest = {}
        for r in self.db.rows("SELECT * FROM finance_balances ORDER BY at, origin='user'"):
            latest[r["account"]] = r
        banks = {}
        for account, r in latest.items():
            banks.setdefault(account.split("(")[0], {})[account] = r
        rows = []
        for bank, members in banks.items():
            total = members.pop(bank, None)
            cards = list(members.values())
            if cards and (total is None or max(c["at"] for c in cards) > total["at"]):
                rows += cards
            elif total:
                rows.append(total)
        accounts = [
            {
                "account": r["account"],
                "amount_cents": r["amount_cents"],
                "as_of": r["at"],
                "origin": r["origin"],
            }
            for r in sorted(rows, key=lambda r: r["account"])
        ]
        return {
            "accounts": accounts,
            "total_cents": sum(a["amount_cents"] for a in accounts),
            "as_of": min((a["as_of"] for a in accounts), default=None),
            "note": "各账户是最近一次知道的余额，截至各自的日期；之后的收支不在里面。",
        }

    def sources(self):
        """The sources the user uses, and any other that has data anyway."""
        used = set(self.config.get("sources", ["wechat", "alipay", "cmb"]))
        used |= {r["source"] for r in self.db.rows("SELECT DISTINCT source FROM finance_imports")}
        return [s for s in ("wechat", "alipay", "cmb") if s in used]

    def coverage(self):
        result = {}
        for source in self.sources():
            imports = self.db.rows(
                "SELECT end,coverage FROM finance_imports WHERE source=? AND status IN ('imported','archived') ORDER BY end DESC",
                (source,),
            )
            days = [self.day(r).isoformat() for r in self.rows() if r["source"] == source]
            ends = [i["end"] for i in imports] + days
            result[source] = {
                "through": max(ends) if ends else None,
                "basis": imports[0]["coverage"] if imports else None,
            }
        return result

    def covered(self):
        """The first and the last day that every source's bills cover; None if one has none."""
        first, last = [], []
        for source, c in self.coverage().items():
            start = self.db.rows(
                "SELECT MIN(start) AS start FROM finance_imports WHERE source=? AND status IN ('imported','archived')",
                (source,),
            )[0]["start"]
            if not start or not c["through"]:
                return None, None
            first.append(start)
            last.append(c["through"])
        if not first:
            return None, None
        return date.fromisoformat(max(first)), date.fromisoformat(min(last))

    def costs(self, rows, as_of):
        refunds = {}
        for r in rows:
            if r["kind"] == "refund" and r["refund_of"] and self.day(r) <= as_of:
                refunds[r["refund_of"]] = refunds.get(r["refund_of"], 0) + r["amount_cents"]
        return {
            r["id"]: (
                # WeChat's status on the purchase and the matched refund rows tell the same
                # refund twice; count it once.
                0
                if r["full_refund"]
                else max(
                    0,
                    r["amount_cents"]
                    - r["split_cents"]
                    - max(r["refunded_cents"], refunds.get(r["id"], 0)),
                )
            )
            for r in rows
            if r["kind"] == "spend"
        }

    def summary(self, period, today):
        start, end = cycle(today, self.config.get("allowance_day", 1))
        if period == "previous":
            start, end = cycle(start - timedelta(days=1), self.config.get("allowance_day", 1))
        elif period == "year":
            start, end = today.replace(month=1, day=1), today.replace(month=12, day=31)
        elif period != "cycle":
            raise FinanceError("周期无效")
        result = self.range_summary(start, end, today)
        result["home_periods"] = [{"start": str(a), "end": str(b)} for a, b in self.home_periods()]
        # The total only: account names can carry card digits.
        assets = self.assets()
        result["assets_cents"] = assets["total_cents"] if assets["accounts"] else None
        result["assets_as_of"] = assets["as_of"]
        if period == "year":
            # Annual budget is the sum of cycles already begun, with a per-cycle fallback.
            cycles = []
            cursor = anchor(today.year, 1, self.config.get("allowance_day", 1))
            while cursor <= today:
                finish = shift_month(cursor, 1, self.config.get("allowance_day", 1)) - timedelta(
                    days=1
                )
                cycles.append(self.range_summary(cursor, finish, today))
                cursor = shift_month(cursor, 1, self.config.get("allowance_day", 1))
            result["budget_cents"] = sum(c["budget_cents"] for c in cycles)
            result["budget_from_settings"] = any(c["budget_from_settings"] for c in cycles)
            result["remaining_cents"] = result["budget_cents"] - result["spent_cents"]
            result["daily_cents"] = None
            result["budget_note"] = (
                "本年已开始周期的生活费合计；缺失周期逐期用设置金额，全年不预测每天可花。"
            )
        return result

    def range_summary(self, start, end, today):
        rows = self.rows(True)
        current = [r for r in rows if start <= self.day(r) <= min(end, today)]
        costs = self.costs(rows, today)
        categories = {category: 0 for category in CATEGORIES}
        # What was spent at home (ask) is the family's until the user says otherwise, like what
        # they pay back (yes: confirmed home spending, family-paid charges such as AI renewals).
        own = [
            r
            for r in current
            if r["kind"] == "spend" and r["reimburse"] not in {"ask", "yes", "reimbursed"}
        ]
        # A big purchase from savings is the user's own money but not living costs.
        for r in own:
            if not r["big"]:
                categories[r["category"]] += costs[r["id"]]
        delivered = [r for r in own if not r["big"] and takeout(r) and costs[r["id"]]]
        # Money back that belongs to no purchase found here (a red packet nobody opened, a
        # transfer sent back, part of an order) still lowers that period's spending.
        for r in current:
            if r["kind"] == "refund" and not r["refund_of"]:
                categories[r["category"]] -= min(r["amount_cents"], categories[r["category"]])
        allowances = [
            r
            for r in rows
            if r["kind"] == "allowance"
            and self.day(r) <= today
            and start <= allowance_anchor(self.day(r), self.config.get("allowance_day", 1)) <= end
        ]
        configured = not allowances
        budget = (
            sum(r["amount_cents"] for r in allowances)
            if allowances
            else money(self.config.get("allowance_amount", 2000))
        )
        spent = sum(categories.values())
        remaining = budget - spent
        days = max(0, (end - max(today, start)).days + 1)
        extras = [r for r in current if r["kind"] == "extra_income"]
        return {
            "start": str(start),
            "end": str(end),
            "as_of": str(today),
            "budget_cents": budget,
            "budget_from_settings": configured,
            "spent_cents": spent,
            "remaining_cents": remaining,
            "days_remaining": days,
            "daily_cents": round(remaining / days) if days else None,
            "categories": categories,
            "takeout_cents": sum(costs[r["id"]] for r in delivered),
            "takeout_count": len(delivered),
            "ask_cents": sum(
                costs[r["id"]] for r in current if r["reimburse"] == "ask" and r["kind"] == "spend"
            ),
            "pending_reimbursement_cents": sum(
                costs[r["id"]] for r in current if r["reimburse"] == "yes" and r["kind"] == "spend"
            ),
            "reimbursed_cents": sum(
                costs[r["id"]]
                for r in current
                if r["reimburse"] == "reimbursed" and r["kind"] == "spend"
            ),
            "big_cents": sum(costs[r["id"]] for r in own if r["big"]),
            "extra_income_cents": sum(r["amount_cents"] for r in extras),
            "red_packet_cents": sum(
                r["amount_cents"] for r in extras if r["extra_type"] == "red_packet"
            ),
            "dividend_cents": sum(
                r["amount_cents"] for r in extras if r["extra_type"] != "red_packet"
            ),
            # Other money from family (topping up, paying for something) is known, not unclear.
            "family_income_cents": sum(
                r["amount_cents"] for r in current if r["kind"] == "income" and self.family(r)
            ),
            "unknown_income_cents": sum(
                r["amount_cents"] for r in current if r["kind"] == "income" and not self.family(r)
            ),
            "coverage": self.coverage(),
            "coverage_note": "截至日期仅表示已导入范围；之后及范围内的缺口不代表没有花钱。退款归回原支出的周期。",
        }

    def previous_comparison(self, start, end, today):
        previous_start = shift_month(start, -1, self.config.get("allowance_day", 1))
        current = self.range_summary(start, end, today)
        previous = self.range_summary(previous_start, start - timedelta(days=1), today)
        return {
            "current": current,
            "previous": previous,
            "category_change_cents": {
                k: current["categories"][k] - previous["categories"][k] for k in CATEGORIES
            },
        }
