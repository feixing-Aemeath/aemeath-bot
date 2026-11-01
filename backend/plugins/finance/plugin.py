"""记账: local bookkeeping only, never payment, banking login or financial advice."""

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.plugins import Plugin as BasePlugin
from core.prompts import render
from core.tokens import Grant, current_grant
from plugins.finance.cards import cards
from plugins.finance.config import PRIVATE, FinanceError, validate, yuan
from plugins.finance.importing import Importer
from plugins.finance.ledger import Ledger
from plugins.finance.reports import Reports, report_date


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        validate(config)
        self.zone = ZoneInfo(context.store.settings.timezone)
        self.ledger = Ledger(context.store.db, config, self.zone)
        self.importer = Importer(context, config, self.ledger)
        self.reports = Reports(self.importer, self.ledger)
        self.lock = asyncio.Lock()
        self.stopped = False
        try:
            self.importer.ensure(create=True)
        except FinanceError:
            pass  # Remain available for the error card, state and doctor; all writes refuse.
        # The home and family-pays rules may have changed with the configuration.
        self.ledger.refresh_reimburse(self.now().isoformat())

    def now(self):
        return datetime.now(self.zone)

    def state_error(self):
        try:
            self.importer.ensure()
            return None
        except Exception:
            return PRIVATE

    def jobs(self):
        return {
            "scan": {"cron": self.config.get("cron", "*/5 * * * *"), "callback": self.scan},
            "reimburse_check": {
                "cron": self.config.get("reimburse_cron", "0 21 * * sun"),
                "callback": self.reimburse_check,
            },
            "monthly_report": {
                "cron": self.config.get("report_cron", "0 9 * * *"),
                "callback": self.monthly_report,
            },
        }

    def mcp_tools(self):
        return {
            name: getattr(self, name)
            for name in (
                "finance_summary",
                "finance_transactions",
                "finance_add",
                "finance_categorize",
                "finance_reimburse",
                "finance_reimbursed",
                "finance_big",
                "finance_split",
                "finance_balance",
                "finance_unlock",
                "finance_import_now",
                "finance_home",
                "finance_reread",
                "finance_report_input",
                "finance_report_write",
            )
        }

    def arguments(self, args, allowed, *, summary=False, report=False):
        grant = current_grant.get()
        unscoped = grant.reader is None and grant.kind is None
        carlotta = grant.reader == "记账" and grant.kind == "bot"
        reporter = report and grant.kind == "finance_report"
        if not (summary or unscoped or carlotta or reporter):
            raise FinanceError("只有记账能看明细")
        if not isinstance(args, dict) or set(args) - set(allowed.split()):
            raise FinanceError("工具参数无效")
        if reporter and args.get("start") != grant.area:
            raise FinanceError("只能写本次月报")
        self.importer.ensure()

    async def work(self, method, *args):
        """Join a cancelled worker before disable/reconfigure can publish anything else."""
        async with self.lock:
            if self.stopped:
                raise FinanceError("finance_stopped")
            task = asyncio.create_task(asyncio.to_thread(method, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except FinanceError:
                raise
            except Exception:
                raise FinanceError("finance_operation_failed") from None

    async def teardown(self):
        self.stopped = True
        async with self.lock:
            pass

    async def scan(self):
        return await self.work(self.importer.scan, self.now().isoformat())

    async def finance_import_now(self, arguments):
        """记账/MAIN only. {} scans only the private topic's root csv/xlsx/zip/pdf;
        failures stay in place. No login, payment or transfer. Never guess unknown formats."""
        self.arguments(arguments, "")
        return await self.work(self.importer.scan, self.now().isoformat(), True)

    async def finance_unlock(self, arguments):
        """记账/MAIN only. {file: inbox basename, password: zip password}. Password is
        used once, never saved/logged/echoed. Never ask for bank/payment credentials.
        needs_unzip means the user must extract locally and upload the inner statement."""
        self.arguments(arguments, "file password")
        password = arguments.get("password")
        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
            raise FinanceError("需要有效的 zip 密码")
        filename = arguments.get("file")
        if not isinstance(filename, str) or not filename.lower().endswith(".zip"):
            raise FinanceError("只能解锁收件箱里的 zip")
        grant = current_grant.get()
        if grant.kind == "bot" and self.context.redact_chat:
            self.context.redact_chat(grant.reader, password)
        return await self.work(self.importer.one, filename, self.now().isoformat(), password)

    async def finance_reread(self, arguments):
        """记账/MAIN only. {} reads the archived bills again with the current readers and
        adds only the rows missed before; existing rows and decisions stay."""
        self.arguments(arguments, "")
        return await self.work(self.importer.reread, self.now().isoformat())

    async def finance_home(self, arguments):
        """记账/MAIN only. {start: YYYY-MM-DD, end: YYYY-MM-DD, remove?: false}. The user was
        at home these days (a weekend back, a holiday): their spending then (ask) is the
        family's and leaves the budget unless the user says no; renewals (会员订阅) stay theirs.
        remove=true takes a period out. Returns all periods."""
        self.arguments(arguments, "start end remove")
        remove = arguments.get("remove", False)
        if not isinstance(remove, bool):
            raise FinanceError("remove 必须是布尔值")
        try:
            start = date.fromisoformat(arguments.get("start"))
            end = date.fromisoformat(arguments.get("end"))
        except (TypeError, ValueError):
            raise FinanceError("日期要写成 YYYY-MM-DD") from None
        return await self.work(self.ledger.set_home, start, end, remove, self.now().isoformat())

    async def finance_summary(self, arguments):
        """All Bots may read {period: cycle(default)|previous|year}. Code-computed cents:
        budget, spending, remaining, daily allowance, categories, takeout (takeout_cents,
        takeout_count), spending at home (ask_cents, not in spending), what the family pays
        back (pending_reimbursement_cents, reimbursed_cents: confirmed home spending and
        family_pays charges such as AI memberships; not in spending), extra income, source cutoffs and the assets total (assets_cents, oldest balance
        date assets_as_of). No merchants/counterparts/accounts. Missing imports never
        mean no spending. Describe ledger facts only; no investment/lending advice."""
        self.arguments(arguments, "period", summary=True)
        return await self.work(
            self.ledger.summary, arguments.get("period", "cycle"), self.now().date()
        )

    async def finance_transactions(self, arguments):
        """记账/MAIN only. Optional start/end YYYY-MM-DD, source, direction, kind,
        category, reimburse, big (bool), limit 1..50 (default 20). Returns visible ledger
        details; linked duplicates are never returned. Facts only, no financial advice."""
        self.arguments(arguments, "start end source direction kind category reimburse big limit")
        limit = arguments.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise FinanceError("明细最多取 50 笔")

        def query():
            rows = self.ledger.rows(True)
            for key in ("source", "direction", "kind", "category", "reimburse", "big"):
                if key in arguments:
                    rows = [r for r in rows if r[key] == arguments[key]]
            for key in ("start", "end"):
                if key in arguments:
                    day = report_date(arguments[key])
                    rows = [
                        r
                        for r in rows
                        if (
                            self.ledger.day(r) >= day
                            if key == "start"
                            else self.ledger.day(r) <= day
                        )
                    ]
            return {"items": list(reversed(rows))[:limit], "total": len(rows)}

        return await self.work(query)

    async def finance_add(self, arguments):
        """记账/MAIN only. {amount: positive yuan, category?, note?, occurred_at?: ISO,
        direction: out(default)|in|neutral, kind?: spend/refund/allowance/income/extra_income/
        transfer/reimbursement, counterpart?, item?, request_id?: retry id}. Record lunch,
        red packets or dividends locally; never move money. Extra income is not budget.
        Reuse request_id when retrying the same chat record."""
        self.arguments(
            arguments, "amount category note occurred_at direction kind counterpart item request_id"
        )
        for field in ("note", "counterpart", "item", "request_id"):
            if field in arguments and (
                not isinstance(arguments[field], str) or len(arguments[field]) > 2000
            ):
                raise FinanceError("记账文字格式无效")
        return await self.work(self.ledger.add, arguments, self.now().isoformat())

    async def finance_categorize(self, arguments):
        """记账/MAIN only. {id, category, remember: true(default)}. Categories: 吃饭、交通、
        购物、娱乐、会员订阅、学习、通讯、医疗、其他. Remembered counterpart rules override
        keywords and update that counterpart's history. Alternatively {id, kind: income|
        extra_income|allowance|reimbursement|transfer} corrects an incoming entry without
        adding a duplicate: a red packet from family that paid something back is a
        reimbursement, not extra income. No model classification."""
        self.arguments(arguments, "id category remember kind")
        if not isinstance(arguments.get("remember", True), bool):
            raise FinanceError("remember 必须是布尔值")
        if "kind" in arguments:
            if "category" in arguments:
                raise FinanceError("请分别修改类别和收入类型")
            return await self.work(self.ledger.change_kind, arguments.get("id"), arguments["kind"])
        return await self.work(
            self.ledger.categorize,
            arguments.get("id"),
            arguments.get("category"),
            arguments.get("remember", True),
        )

    async def finance_reimburse(self, arguments):
        """记账/MAIN only. {ids: [transaction ids] | "all_ask", decision: yes|no}.
        no puts a home expense, or a family_pays charge the family does not pay (a game
        top-up through Apple), back in the budget; ask/yes/reimbursed stay out of it. The
        decision sticks. No money is transferred."""
        self.arguments(arguments, "ids decision")
        return await self.work(
            self.ledger.reimburse, arguments.get("ids"), arguments.get("decision")
        )

    async def finance_reimbursed(self, arguments):
        """记账/MAIN only. {amount?: received yuan, ids?: transaction ids}; defaults to
        all confirmed yes expenses. Mark bookkeeping reimbursement received, never transfer.
        Amount must match selected expenses within one yuan; otherwise specify ids."""
        self.arguments(arguments, "amount ids")
        return await self.work(
            self.ledger.reimbursed, arguments.get("ids"), arguments.get("amount")
        )

    async def finance_big(self, arguments):
        """记账/MAIN only. {id, big: true(default), note?: what it is, e.g. 「手表」}. A big
        purchase the user paid from their own savings: listed on its own, out of the period's
        living budget. big=false puts it back. Spending only; ask the user, never guess."""
        self.arguments(arguments, "id big note")
        big = arguments.get("big", True)
        if not isinstance(big, bool):
            raise FinanceError("big 必须是布尔值")
        note = arguments.get("note")
        if note is not None and (not isinstance(note, str) or not 1 <= len(note) <= 100):
            raise FinanceError("大件名称格式无效")
        return await self.work(self.ledger.mark_big, arguments.get("id"), big, note)

    async def finance_split(self, arguments):
        """记账/MAIN only. {id, amount: yuan, category}. Part of one charge is the user's own
        and unlike the rest (a game top-up inside an Apple charge the family pays for): it
        becomes an entry of its own in `category`, in the budget; the charge keeps the rest
        and its decisions. Again replaces the part; amount 0 joins them back. Ask the user,
        never guess."""
        self.arguments(arguments, "id amount category")
        return await self.work(
            self.ledger.split,
            arguments.get("id"),
            arguments.get("amount"),
            arguments.get("category"),
            self.now().isoformat(),
        )

    async def finance_balance(self, arguments):
        """记账/MAIN only. {} lists what the user has in each account (the newest balance
        known, with its date). {account, amount: yuan, at?: YYYY-MM-DD (default today)} records
        what the user says an account holds: 「招商银行」 for the bank App's total over all
        cards, 「微信零钱」, 「支付宝余额」, 「现金」. {account, remove: true} forgets one.
        Bank statements record their card balances themselves. No advice on the money."""
        self.arguments(arguments, "account amount at remove")
        if not arguments:
            return await self.work(self.ledger.assets)
        remove = arguments.get("remove", False)
        if not isinstance(remove, bool) or (remove and "amount" in arguments):
            raise FinanceError("remove 必须是布尔值，且不和金额一起给")
        try:
            at = date.fromisoformat(arguments.get("at", str(self.now().date())))
        except (TypeError, ValueError):
            raise FinanceError("日期要写成 YYYY-MM-DD") from None
        if at > self.now().date():
            raise FinanceError("余额日期不能在今天之后")
        if not remove and "amount" not in arguments:
            raise FinanceError("要给金额")
        return await self.work(
            self.ledger.set_balance, arguments.get("account"), arguments.get("amount"), at, remove
        )

    async def reimburse_check(self):
        def check():
            self.importer.ensure()
            rows = [
                r
                for r in self.ledger.rows(True)
                if r["reimburse"] == "ask" and not r["notified_at"] and r["kind"] == "spend"
            ]
            if not rows:
                return
            # A digest also goes into the day's 今天.md, which every Bot reads: the count and the
            # total only; each one is in 记账's tools. They are the family's unless the user
            # says otherwise, so this only tells.
            costs = self.ledger.costs(self.ledger.rows(True), self.now().date())
            text = (
                f"在家时的花销 {len(rows)} 笔，共 {yuan(sum(costs[r['id']] for r in rows))} 元，"
                "算家里的，不占生活费。有家里没报的，跟我说是哪几笔。"
            )
            self.context.store.digest(str(self.now().date()), "finance", text, append=True)
            with self.ledger.db.connect() as conn:
                conn.executemany(
                    "UPDATE finance_transactions SET notified_at=? WHERE id=?",
                    [(self.now().isoformat(), r["id"]) for r in rows],
                )

        await self.work(check)

    async def monthly_report(self):
        self.importer.ensure()
        due = await self.work(self.reports.due, self.now().date())
        if due is None:
            return
        start, end = due
        await self.work(self.reports.prepare, start, end, self.now().date(), self.now().isoformat())
        if self.context.run_agent is None:
            raise FinanceError("finance_agent_unavailable")
        try:
            await self.context.run_agent(
                "plugin:finance:monthly_report",
                render("finance_report", start=str(start)),
                succeeded=lambda: self.reports.row(start)["status"] == "ready",
                incomplete_error="finance_report_not_written",
                grant=Grant(
                    None, "财务汇总月报", frozenset({"mcp"}), kind="finance_report", area=str(start)
                ),
            )
        except Exception:
            raise FinanceError("finance_report_agent_failed") from None

    async def finance_report_input(self, arguments):
        """记账/MAIN or this month's report runner only. {start: cycle start YYYY-MM-DD}.
        Code-computed aggregates and previous comparison only, no merchants/counterparts.
        Use this as the sole factual source for the report; never read local ledger files."""
        self.arguments(arguments, "start", report=True)
        return await self.work(self.reports.input, report_date(arguments.get("start")))

    async def finance_report_write(self, arguments):
        """记账/MAIN or scoped report runner only. {start: YYYY-MM-DD, text: 1..4000 chars}.
        Append summary/trends to 记账的话 and App digest. State coverage gaps; never invent
        transactions, names or numbers, give investment/wealth/lending advice, or echo passwords."""
        self.arguments(arguments, "start text", report=True)
        return await self.work(
            self.reports.submit,
            report_date(arguments.get("start")),
            arguments.get("text"),
            self.now().date(),
        )

    async def cards(self):
        if error := self.state_error():
            return [
                {
                    "type": "list",
                    "title": "账单",
                    "data": {"items": [{"title": error, "subtitle": "暂停导入，账单留在原地"}]},
                }
            ]
        return await self.work(self._cards)

    def _cards(self):
        return cards(self.ledger, self.now().date())

    async def daily(self, day):
        def note():
            self.importer.ensure()
            activity = [
                r
                for r in self.ledger.rows()
                if datetime.fromisoformat(r["created_at"]).astimezone(self.zone).date() == day
            ]
            # Preserve the day a manual entry was made even after a later import merges it.
            rows = list(
                {
                    self.ledger.get(r["id"])["id"]: self.ledger.get(r["id"]) for r in activity
                }.values()
            )
            imports = [
                r
                for r in self.ledger.db.rows("SELECT created_at FROM finance_imports")
                if datetime.fromisoformat(r["created_at"]).astimezone(self.zone).date() == day
            ]
            if not rows and not imports:
                return None
            outgoing = sum(r["amount_cents"] for r in rows if r["direction"] == "out")
            incoming = sum(r["amount_cents"] for r in rows if r["direction"] == "in")
            text = f"当天新增记账 {len(rows)} 笔；支出合计 {yuan(outgoing)} 元，转入合计 {yuan(incoming)} 元。历史账单按当天导入统计，不代表当天消费。"
            return {"file": "记账.md", "bot": "记账Bot", "summary": text, "body": text}

        return await self.work(note)
