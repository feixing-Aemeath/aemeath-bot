"""Synthetic bills only. No real people, accounts, extractors, network or payments."""

import csv
import io
import json
import struct
import zipfile
import zlib
from contextlib import contextmanager
from datetime import date, datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import pytest

from core.documents import FakeExtractor
from core.mcp_server import create_mcp
from core.models import Card, PluginPatch
from core.plugins import PluginContext
from core.tokens import MAIN, Grant, current_grant
from plugins.finance import parsers
from plugins.finance.config import PRIVATE, FinanceError, cycle
from plugins.finance.plugin import Plugin
from scripts.doctor import Report, check_finance
from tests.test_chat import finish, ids
from tests.test_doctor import FakeProbe

ZONE = ZoneInfo("Asia/Shanghai")
WX = [
    "交易时间",
    "交易类型",
    "交易对方",
    "商品",
    "收/支",
    "金额(元)",
    "支付方式",
    "当前状态",
    "交易单号",
    "商户单号",
    "备注",
]
ALI = [
    "交易时间",
    "交易分类",
    "交易对方",
    "对方账号",
    "商品说明",
    "收/支",
    "金额",
    "收/付款方式",
    "交易状态",
    "交易订单号",
    "商家订单号",
    "备注",
]
PDF = """招商银行测试流水（非真实账单）
查询期间：2026-10-01 至 2026-10-31
记账日期 货币 交易金额 联机余额 交易摘要 对手信息
2026-10-01 CNY +2100.00 3000.00  生活费  同学甲
2026-10-20 CNY -18.00 2982.00  测试消费  测试商户
第 1 页 共 1 页
"""


def csv_bill(rows=None, source="wechat", *, start="2026-10-01", end="2026-10-31", encoding="utf-8"):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["测试账单，不含真实信息"])
    if start and end:
        writer.writerow([f"起始时间：{start} 00:00:00"])
        writer.writerow([f"终止时间：{end} 23:59:59"])
    writer.writerow(WX if source == "wechat" else ALI)
    for row in rows or [wx()]:
        writer.writerow(row)
    return stream.getvalue().encode(encoding)


def wx(
    identity="wx1",
    amount="18.00",
    at="2026-10-20 12:00:00",
    direction="支出",
    item="午饭",
    method="零钱",
    status="支付成功",
    counterpart="测试商户",
    kind="商户消费",
    note="",
):
    return [
        at,
        kind,
        counterpart,
        item,
        direction,
        amount,
        method,
        status,
        identity,
        "m" + identity,
        note,
    ]


def ali(
    identity="a1",
    amount="18.00",
    at="2026-10-20 12:00:00",
    method="招商银行(0000)",
    direction="支出",
    item="午饭",
    status="交易成功",
):
    return [
        at,
        "餐饮",
        "测试商户",
        "",
        item,
        direction,
        amount,
        method,
        status,
        identity,
        "m" + identity,
        "",
    ]


def xlsx(path, rows, shared=True):
    strings = list(dict.fromkeys(str(v) for row in rows for v in row))
    xmlrows = []
    for index, row in enumerate(rows, 1):
        cells = []
        for col, value in enumerate(row):
            ref = chr(65 + col) + str(index)
            if value is None:
                continue
            if shared:
                cells.append(f'<c r="{ref}" t="s"><v>{strings.index(str(value))}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        xmlrows.append(f'<row r="{index}">{"".join(cells)}</row>')
    ns = parsers.NS["s"]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="账单" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{ns}">'
            + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
            + "</sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{ns}"><sheetData>{"".join(xmlrows)}</sheetData></worksheet>',
        )


def encrypted_zip(path, data, password):
    """Make a tiny authentic ZipCrypto fixture in stdlib (zipfile only decrypts).
    Deterministic encryption header; synthetic CSV and password only."""
    keys = [0x12345678, 0x23456789, 0x34567890]

    def crc_byte(c, crc):
        return ((crc >> 8) ^ zipfile._crctable[(crc ^ c) & 0xFF]) & 0xFFFFFFFF

    if zipfile._crctable is None:
        zipfile._crctable = list(map(zipfile._gen_crc, range(256)))

    def update(c):
        keys[0] = crc_byte(c, keys[0])
        keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        keys[2] = crc_byte(keys[1] >> 24, keys[2])

    for byte in password.encode():
        update(byte)
    crc = zlib.crc32(data) & 0xFFFFFFFF
    encrypted = bytearray()
    for byte in bytes(range(11)) + bytes([crc >> 24]) + data:
        temp = keys[2] | 2
        encrypted.append(byte ^ ((temp * (temp ^ 1) >> 8) & 0xFF))
        update(byte)
    name = b"statement.csv"
    local = (
        struct.pack(
            "<4s5H3I2H", b"PK\x03\x04", 20, 1, 0, 0, 0, crc, len(encrypted), len(data), len(name), 0
        )
        + name
        + encrypted
    )
    central = (
        struct.pack(
            "<4s6H3I5H2I",
            b"PK\x01\x02",
            20,
            20,
            1,
            0,
            0,
            0,
            crc,
            len(encrypted),
            len(data),
            len(name),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        + name
    )
    end = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 1, 1, len(central), len(local), 0)
    path.write_bytes(local + central + end)


@contextmanager
def as_grant(grant):
    token = current_grant.set(grant)
    try:
        yield
    finally:
        current_grant.reset(token)


@pytest.fixture
async def finance(service):
    plugin = Plugin()
    await plugin.setup(
        PluginContext(service.store, service.settings.data_dir, "finance", service.memory), {}
    )
    plugin.now = lambda: datetime(2026, 10, 29, 12, tzinfo=ZONE)
    yield plugin
    await plugin.teardown()


def inbox(plugin, filename, data):
    path = plugin.importer.ensure() / filename
    path.write_bytes(data)
    return path


def raw(
    source_id,
    at="2026-10-20",
    amount=1800,
    direction="out",
    kind="spend",
    counterpart="测试商户",
    item="午饭",
    method="零钱",
    **kwargs,
):
    return {
        "source_id": source_id,
        "occurred_at": at + "T12:00:00+08:00",
        "amount_cents": amount,
        "direction": direction,
        "kind": kind,
        "counterpart": counterpart,
        "item": item,
        "method": method,
        "note": "",
        **kwargs,
    }


def ingest(plugin, rows, source="wechat", key=None):
    import hashlib

    dates = [date.fromisoformat(r["occurred_at"][:10]) for r in rows]
    return plugin.ledger.imported(
        parsers.Statement(source, rows, min(dates), max(dates)),
        hashlib.sha256((key or json.dumps(rows)).encode()).hexdigest(),
        plugin.now().isoformat(),
    )


def test_parsers_header_encoding_and_full_refund():
    result = parsers.tables(parsers.csv_rows(csv_bill([wx(status="已全额退款")])), ZONE)
    assert result.source == "wechat" and result.rows[0]["full_refund"] == 1
    assert result.rows[0]["amount_cents"] == 1800
    assert result.start == date(2026, 10, 1) and result.end == date(2026, 10, 31)
    assert result.rows[0]["occurred_at"].endswith("+08:00")
    alipay = parsers.tables(
        parsers.csv_rows(
            csv_bill(
                [
                    ali(),
                    ali("closed", status="交易关闭"),
                    ali("transfer", direction="不计收支", item="余额宝转入"),
                ],
                source="alipay",
                encoding="gbk",
            )
        ),
        ZONE,
    )
    assert alipay.source == "alipay" and len(alipay.rows) == 2
    assert alipay.rows[-1]["kind"] == "transfer"
    for content in (
        b"a,b,c\n1,2,3",
        csv_bill([wx(amount="bad")]),
        csv_bill([wx(direction="未知")]),
    ):
        with pytest.raises(FinanceError):
            parsers.tables(parsers.csv_rows(content), ZONE)


@pytest.mark.parametrize("shared", [True, False])
def test_xlsx_first_sheet_and_sparse_cells(tmp_path, shared):
    path = tmp_path / "anything.xlsx"
    xlsx(path, [["测试账单"], WX, wx()], shared=shared)
    statement = parsers.tables(parsers.xlsx_rows(path), ZONE)
    assert statement.source == "wechat" and statement.rows[0]["amount_cents"] == 1800
    assert statement.coverage == "transactions"


def test_cmb_pdf_strict_header_and_footer():
    statement = parsers.pdf(PDF, ZONE)
    assert statement.source == "cmb" and len(statement.rows) == 2
    assert statement.rows[0]["direction"] == "in" and statement.rows[1]["amount_cents"] == 1800
    assert statement.coverage == "statement"
    for text in (
        "未知账单",
        PDF.replace("CNY -18.00", "USD -18.00"),  # a dated line that is not a row
        PDF.replace("2982.00", "2981.00"),  # balances that do not follow
    ):
        with pytest.raises(FinanceError):
            parsers.pdf(text, ZONE)


# Made up, in the layout of a real 招商银行 交易流水 as core.documents joins its pages.
CMB_REAL_LAYOUT = """[第 1 页]
招商银行交易流水
Transaction Statement of China Merchants Bank
2026-09-01 -- 2026-10-28
户 名：测试用户
Name
账号：6214********0000
Account No
记账日期 货币 交易金额 联机余额 交易摘要 对手信息
Date Currency Transaction
Amount Balance Transaction Type Counter Party
2026-09-06 CNY 2,000.00 2,100.00 行内转账转入 家长甲
2026-09-07 CNY -3.00 2,097.00 快捷支付 上海市测试轨道交通集团运营有限公
司
GOOGLE*TESTAI\\ROPPONGI
2026-09-08 CNY -120.00 1,977.00 线上境外消费
HILLS TEST TOWER 10-
1\\TOKYO JPN
2026-09-09 CNY -100.00 1,877.00 转账汇款 测试用户
1/2

[第 2 页]
记账日期 货币 交易金额 联机余额 交易摘要 对手信息
Date Currency Transaction
Amount Balance Transaction Type Counter Party
2026-09-10 CNY -59.90 1,817.10 快捷支付 测试数码京东自营旗
舰店
2026-09-11 CNY 59.90 1,877.00 快捷退款 测试数码京东自营旗舰店
2026-09-12 CNY 50.00 1,927.00 柜台存现
2026-09-13 CNY 30.00 1,957.00 网联收款 测试用户
————————————————————
温馨提示：1.交易流水验真……
2/2
"""


def test_cmb_real_layout_wrapping_own_transfers_and_refunds():
    statement = parsers.pdf(CMB_REAL_LAYOUT, ZONE)
    assert (statement.start, statement.end) == (date(2026, 9, 1), date(2026, 10, 28))
    assert statement.coverage == "statement"
    rows = [
        (r["occurred_at"][:10], r["direction"], r["kind"], r["amount_cents"], r["counterpart"])
        for r in statement.rows
    ]
    assert rows == [
        ("2026-09-06", "in", "income", 200000, "家长甲"),
        ("2026-09-07", "out", "spend", 300, "上海市测试轨道交通集团运营有限公司"),
        # The first line of a tall cell comes above its row in PDF text order.
        (
            "2026-09-08",
            "out",
            "spend",
            12000,
            "GOOGLE*TESTAI\\ROPPONGIHILLS TEST TOWER 10-1\\TOKYO JPN",
        ),
        ("2026-09-09", "neutral", "transfer", 10000, "自己的账户"),  # to the holder's other card
        ("2026-09-10", "out", "spend", 5990, "测试数码京东自营旗舰店"),
        ("2026-09-11", "in", "refund", 5990, "测试数码京东自营旗舰店"),
        ("2026-09-12", "in", "income", 5000, ""),
        ("2026-09-13", "neutral", "transfer", 3000, "自己的账户"),  # from their WeChat balance
    ]
    assert {r["method"] for r in statement.rows} == {"招商银行(0000)"}
    # The holder's name is only compared, never kept.
    assert "测试用户" not in json.dumps(statement.rows, ensure_ascii=False)


def test_overlapping_cmb_exports_give_the_same_ids():
    # A later export wraps the same counterparty differently; the row is still the same row.
    first = parsers.pdf(CMB_REAL_LAYOUT, ZONE)
    rewrapped = CMB_REAL_LAYOUT.replace("测试数码京东自营旗\n舰店", "测试数码京东自营旗舰店")
    assert [r["source_id"] for r in parsers.pdf(rewrapped, ZONE).rows] == [
        r["source_id"] for r in first.rows
    ]


async def test_a_wechat_payment_from_the_card_is_counted_once(finance):
    # WeChat names the card by its last digits; the bank names the merchant, not 财付通.
    ingest(finance, [raw("wx", amount=499900, method="招商银行储蓄卡(0000)", counterpart="拼多多")])
    ingest(
        finance,
        [
            raw("bank", amount=499900, method="招商银行(0000)", counterpart="拼多多平台商户"),
            raw("other-card", amount=499900, method="招商银行(9999)", counterpart="拼多多平台商户"),
        ],
        "cmb",
    )
    linked = {r["source_id"]: r["linked_id"] for r in finance.ledger.rows()}
    assert linked["bank"] and not linked["wx"]
    assert not linked["other-card"]
    # A payment from one card never takes the bank row of another.
    ingest(finance, [raw("wx2", at="2026-10-22", amount=1200, method="招商银行储蓄卡(1111)")])
    ingest(finance, [raw("bank2", at="2026-10-22", amount=1200, method="招商银行(9999)")], "cmb")
    linked = {r["source_id"]: r["linked_id"] for r in finance.ledger.rows()}
    assert not linked["bank2"] and not linked["wx2"]


async def test_import_archive_no_overwrite_duplicate_and_failures(finance):
    root = finance.importer.ensure()
    original = root / "账单原件/微信/2026/bill.csv"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"user-file")
    content = csv_bill()
    inbox(finance, "bill.csv", content)
    result = await finance.scan()
    assert result["files"][0]["status"] == "archived"
    assert original.read_bytes() == b"user-file"
    assert original.with_name("bill (2).csv").read_bytes() == content
    assert len(finance.ledger.rows()) == 1
    inbox(finance, "bill.csv", content)
    result = await finance.scan()
    assert result["files"][0]["status"] == "duplicate"
    assert len(finance.ledger.rows()) == 1 and original.with_name("bill (3).csv").exists()
    unknown = inbox(finance, "unknown.csv", b"unrecognized")
    assert (await finance.scan())["files"][0]["status"] == "unrecognized"
    assert unknown.exists()
    sub = root / "ignored"
    sub.mkdir()
    (sub / "nested.csv").write_bytes(content)
    (root / "说明.md").write_text("用户已有说明")
    await finance.scan()
    assert (sub / "nested.csv").exists()
    assert (root / "说明.md").read_text() == "用户已有说明"


async def test_real_zipcrypto_password_not_persisted(finance, caplog):
    path = finance.importer.ensure() / "bill.zip"
    password = "only-test-secret-123"
    encrypted_zip(path, csv_bill(), password)
    assert (await finance.scan())["files"][0]["status"] == "needs_password"
    assert (await finance.finance_unlock({"file": path.name, "password": "incorrect"}))[
        "status"
    ] == "needs_password"
    assert (await finance.finance_unlock({"file": path.name, "password": password}))[
        "status"
    ] == "archived"
    assert not list(finance.importer.temporary.iterdir())
    with finance.ledger.db.connect() as conn:
        assert password not in "\n".join(conn.iterdump())
    for file in finance.context.data_dir.rglob("*"):
        if file.is_file():
            assert password.encode() not in file.read_bytes()
    assert password not in caplog.text


async def test_aes_and_hostile_zip_stay_inbox(finance, monkeypatch):
    path = finance.importer.ensure() / "aes.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.csv", csv_bill())

    def unsupported(*args, **kwargs):
        raise NotImplementedError("private contents must never escape")

    with monkeypatch.context() as patch:
        patch.setattr(zipfile.ZipFile, "read", unsupported)
        assert (await finance.finance_unlock({"file": "aes.zip", "password": "fake-secret"}))[
            "status"
        ] == "needs_unzip"
    assert path.exists()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.csv", csv_bill())
    assert (await finance.scan())["files"][0]["status"] == "failed"
    assert not (finance.context.data_dir / "escape.csv").exists()


async def test_pdf_calls_fake_documents_not_mac_helper(finance):
    inbox(finance, "statement.pdf", b"fake-pdf-not-real")
    finance.context.memory.documents.extractor = FakeExtractor(
        {"statement.pdf": {"pages": [{"page": 1, "text": PDF}]}}
    )
    assert (await finance.scan())["files"][0]["status"] == "archived"
    assert finance.context.memory.documents.extractor.calls == ["statement.pdf"]
    assert len(finance.ledger.rows()) == 2


async def test_archive_symlink_and_invalid_unlock_path(finance, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_bytes(csv_bill())
    link = finance.importer.ensure() / "linked.csv"
    link.symlink_to(secret)
    result = await finance.scan()
    assert result["files"][0]["status"] == "unsafe"
    assert link.is_symlink() and secret.exists() and not finance.ledger.rows()
    for filename in ("../secret.zip", "/tmp/secret.zip", "x/y.zip"):
        with pytest.raises(FinanceError):
            await finance.finance_unlock({"file": filename, "password": "fake"})


@pytest.mark.parametrize("bank_first", [True, False])
async def test_cross_source_one_to_one_and_own_transfer(finance, bank_first):
    bank = [raw("bank", method="招商银行", item="支付宝", at="2026-10-22")]
    platform = [raw("platform", method="招商银行(0000)")]
    pair = [(bank, "cmb"), (platform, "alipay")]
    for rows, source in pair if bank_first else reversed(pair):
        ingest(finance, rows, source)
    assert len(finance.ledger.rows(True)) == 1
    assert next(r for r in finance.ledger.rows() if r["source"] == "cmb")["linked_id"]
    own = parsers.tables(
        parsers.csv_rows(csv_bill([wx("recharge", direction="/", kind="零钱充值")])), ZONE
    )
    finance.ledger.imported(own, "own-test-sha", finance.now().isoformat())
    assert finance.ledger.summary("cycle", finance.now().date())["spent_cents"] == 1800
    assert (await finance.finance_transactions({}))["total"] == 2


async def test_ambiguous_bank_matches_are_not_guessed(finance):
    ingest(finance, [raw("p1", method="招商银行"), raw("p2", method="招商银行")], "alipay")
    ingest(finance, [raw("b1", item="支付宝", method="招商银行")], "cmb")
    assert len(finance.ledger.rows(True)) == 3


async def test_refunds_partial_full_and_merchant_match(finance):
    ingest(
        finance,
        [
            raw("spend", amount=5000),
            raw(
                "refund",
                amount=2000,
                direction="in",
                kind="refund",
                at="2026-10-21",
                original_id="spend",
            ),
        ],
    )
    assert finance.ledger.summary("cycle", finance.now().date())["spent_cents"] == 3000
    ingest(
        finance,
        [
            raw("full", amount=6000, full_refund=1),
            raw("same", amount=1800, at="2026-10-22", counterpart="同学甲"),
            raw(
                "rf2",
                amount=1800,
                direction="in",
                kind="refund",
                at="2026-10-23",
                counterpart="同学甲",
            ),
        ],
    )
    assert finance.ledger.summary("cycle", finance.now().date())["spent_cents"] == 3000
    # Re-import status change on a platform order updates refund status without duplicates.
    ingest(finance, [raw("spend", amount=5000, full_refund=1)], key="new-status")
    assert finance.ledger.summary("cycle", finance.now().date())["spent_cents"] == 0


async def test_manual_import_merge_preserves_notes_categories_and_hides_duplicate(finance):
    manual = await finance.finance_add(
        {
            "amount": "18",
            "category": "学习",
            "note": "测试备注",
            "occurred_at": "2026-10-20T11:00:00+08:00",
            "request_id": "message-test",
        }
    )
    await finance.finance_add(
        {
            "amount": "18",
            "category": "学习",
            "note": "测试备注",
            "occurred_at": "2026-10-20T11:00:00+08:00",
            "request_id": "message-test",
        }
    )
    assert len(finance.ledger.rows()) == 1
    ingest(finance, [raw("wx-import")])
    items = (await finance.finance_transactions({}))["items"]
    assert len(items) == 1 and items[0]["source"] == "wechat"
    assert items[0]["category"] == "学习" and items[0]["note"] == "测试备注"
    assert finance.ledger.get(manual["id"])["id"] == items[0]["id"]
    await finance.finance_categorize({"id": items[0]["id"], "category": "吃饭"})
    ingest(finance, [raw("next", amount=3000)])
    assert all(r["category"] == "吃饭" for r in finance.ledger.rows(True))


def test_cycle_boundaries_year_and_clamped_days():
    assert cycle(date(2027, 1, 1), 1) == (date(2027, 1, 1), date(2027, 1, 31))
    assert cycle(date(2026, 12, 31), 1) == (date(2026, 12, 1), date(2026, 12, 31))
    assert cycle(date(2027, 1, 4), 5) == (date(2026, 12, 5), date(2027, 1, 4))
    assert cycle(date(2027, 2, 28), 31) == (date(2027, 2, 28), date(2027, 3, 30))


async def test_allowance_early_late_family_and_extra_income(finance):
    finance.config["family"] = ["同学甲"]
    ingest(
        finance,
        [
            raw(
                "allow",
                at="2026-09-29",
                amount=220000,
                direction="in",
                kind="income",
                counterpart="同学甲",
            ),
            raw("unknown", at="2026-10-01", amount=200000, direction="in", kind="income"),
            raw(
                "low",
                at="2026-10-01",
                amount=100000,
                direction="in",
                kind="income",
                counterpart="同学甲",
            ),
        ],
        "cmb",
    )
    ingest(
        finance,
        [
            raw("red", amount=10000, direction="in", kind="income", item="微信红包"),
            raw("div", amount=230000, direction="in", kind="income", item="分红"),
        ],
    )
    summary = await finance.finance_summary({})
    assert summary["budget_cents"] == 220000 and not summary["budget_from_settings"]
    assert summary["extra_income_cents"] == 240000 and summary["red_packet_cents"] == 10000
    # The smaller transfer from family is family money, not unclear.
    assert (summary["unknown_income_cents"], summary["family_income_cents"]) == (200000, 100000)
    assert summary["remaining_cents"] == 220000
    fallback = finance.ledger.summary("previous", date(2026, 10, 29))
    assert fallback["budget_cents"] == 200000 and fallback["budget_from_settings"]


async def test_reimbursement_late_import_digest_once_and_automatic_receipt(finance):
    finance.config["every_weekend_at_home"] = True
    ingest(finance, [raw("weekend", at="2026-10-23", occurred_at="2026-10-23T17:00:00+08:00")])
    assert finance.ledger.rows()[0]["reimburse"] == "ask"
    await finance.reimburse_check()
    first = finance.context.store.digests(plugin="finance")["items"]
    await finance.reimburse_check()
    assert finance.context.store.digests(plugin="finance")["items"] == first
    assert "1 笔" in first[0]["summary"] and "18.00 元" in first[0]["summary"]
    # The digest reaches the daily folder, which every Bot reads: no merchant there.
    assert "测试商户" not in first[0]["summary"]
    await finance.finance_reimburse({"ids": "all_ask", "decision": "yes"})
    assert (await finance.finance_summary({}))["spent_cents"] == 0
    ingest(
        finance,
        [
            raw(
                "receipt",
                at="2026-10-27",
                amount=1850,
                direction="in",
                kind="income",
                counterpart="同学甲",
            )
        ],
    )
    assert (
        next(r for r in finance.ledger.rows() if r["source_id"] == "receipt")["kind"]
        == "reimbursement"
    )
    assert (
        next(r for r in finance.ledger.rows() if r["source_id"] == "weekend")["reimburse"]
        == "reimbursed"
    )
    manual = await finance.finance_add({"amount": 30, "occurred_at": "2026-10-25T12:00:00+08:00"})
    await finance.finance_reimburse({"ids": [manual["id"]], "decision": "no"})
    assert finance.ledger.get(manual["id"])["reimburse"] == "no"
    await finance.finance_reimburse({"ids": [manual["id"]], "decision": "yes"})
    await finance.finance_reimbursed({"ids": [manual["id"]], "amount": 30})
    assert finance.ledger.get(manual["id"])["reimburse"] == "reimbursed"


async def test_private_topic_required_setup_dynamic_state_doctor_and_import(service, finance):
    inbox(finance, "bill.csv", csv_bill())
    service.knowledge.patch_access("知识库/财务", ["知识库"])
    with pytest.raises(FinanceError, match=PRIVATE):
        await finance.scan()
    assert (await finance.cards())[0]["data"]["items"][0]["title"] == PRIVATE
    assert not finance.ledger.rows()
    again = Plugin()
    await again.setup(finance.context, {})
    assert again.state_error() == PRIVATE
    await again.teardown()
    await service.start()
    try:
        await service.plugins.patch("finance", PluginPatch(enabled=True))
        assert service.plugins.view("finance")["state"] == "error"
        assert service.plugins.view("finance")["error"] == PRIVATE
        report = Report(FakeProbe("linux"))
        check_finance(report, service.settings)
        assert report.checks[-1].status == "fail" and PRIVATE in report.checks[-1].detail
        service.knowledge.patch_access("知识库/财务", [])
        assert service.plugins.view("finance")["state"] == "enabled"
        report = Report(FakeProbe("linux"))
        check_finance(report, service.settings)
        assert report.checks[-1].status == "ok"
    finally:
        await service.close()


@pytest.mark.parametrize(
    "grant",
    [
        Grant(None, "bot", frozenset({"mcp"}), "知识库", "bot"),
        Grant(None, "fake", frozenset({"mcp"}), "记账", "agent"),
        Grant(None, "system", frozenset({"mcp"}), kind="system"),
    ],
)
async def test_all_detail_tools_enforce_reader_before_arguments(finance, grant):
    ingest(finance, [raw("private")])
    with as_grant(grant):
        assert "测试商户" not in json.dumps(await finance.finance_summary({}), ensure_ascii=False)
        for name, tool in finance.mcp_tools().items():
            if name != "finance_summary":
                with pytest.raises(FinanceError, match="只有记账能看明细"):
                    await tool({})
    for allowed in (
        MAIN,
        Grant("legacy", "old", frozenset({"mcp"})),
        Grant(None, "bot", frozenset({"mcp"}), "记账", "bot"),
    ):
        with as_grant(allowed):
            assert (await finance.finance_transactions({}))["total"] == 1


async def test_cards_daily_summary_and_safe_errors(finance, caplog):
    ingest(
        finance, [raw("weekday"), raw("ask", occurred_at="2026-10-23T18:00:00+08:00", amount=7199)]
    )
    cards = await finance.cards()
    assert cards[0]["data"]["first"] is True and cards[0]["data"]["wide"] is True
    assert cards[1]["title"] == "记账的建议" and cards[2]["data"]["kind"] == "donut"
    for card in cards:
        Card.model_validate(card)
    assert "之后的还没导入" in cards[0]["data"]["label"]
    daily = await finance.daily(finance.now().date())
    assert daily["bot"] == "记账Bot" and daily["file"] == "记账.md"
    assert "测试商户" not in str(daily) and "weekday" not in str(daily)
    summary = await finance.finance_summary({})
    assert all(
        name not in json.dumps(summary, ensure_ascii=False) for name in ("测试商户", "同学甲")
    )
    bad = inbox(finance, "bad.csv", csv_bill([wx(amount="71.99 测试商户")]))
    await finance.scan()
    assert bad.exists()
    assert "71.99" not in caplog.text and "测试商户" not in caplog.text
    errors = finance.ledger.db.rows("SELECT error FROM finance_files")
    assert "71.99" not in str(errors) and "测试商户" not in str(errors)


async def test_monthly_conditions_safe_input_no_clobber_and_failed_agent(finance):
    ingest(finance, [raw("before", at="2026-10-20")])
    assert finance.reports.due(date(2026, 11, 1)) is None
    assert finance.reports.due(date(2026, 11, 4)) is None
    assert finance.reports.due(date(2026, 11, 5)) == (date(2026, 10, 1), date(2026, 10, 31))
    for source in ("wechat", "alipay", "cmb"):
        finance.ledger.db.execute(
            "INSERT INTO finance_imports(id,sha256,source,start,end,status,coverage,created_at) VALUES(?,?,?,?,?,'imported','statement',?)",
            (source, source, source, "2026-10-01", "2026-10-31", finance.now().isoformat()),
        )
    assert finance.reports.due(date(2026, 11, 1)) is not None
    folder = finance.importer.ensure() / "月报"
    folder.mkdir()
    (folder / "2026-10.md").write_text("用户原有月报")
    finance.now = lambda: datetime(2026, 11, 1, 9, tzinfo=ZONE)

    async def failed(*args, **kwargs):
        assert kwargs["grant"].kind == "finance_report"
        raise RuntimeError("unsafe contents should not reach runs")

    finance.context.run_agent = failed
    with pytest.raises(FinanceError, match="finance_report_agent_failed"):
        await finance.monthly_report()
    assert (folder / "2026-10.md").read_text() == "用户原有月报"
    assert "代码计算" in (folder / "2026-10 (2).md").read_text()
    data = await finance.finance_report_input({"start": "2026-10-01"})
    assert "测试商户" not in json.dumps(data, ensure_ascii=False)
    with as_grant(
        Grant(None, "report", frozenset({"mcp"}), kind="finance_report", area="2026-10-01")
    ):
        with pytest.raises(FinanceError):
            await finance.finance_transactions({})
        with pytest.raises(FinanceError):
            await finance.finance_report_input({"start": "2026-09-01"})
        await finance.finance_report_write(
            {"start": "2026-10-01", "text": "本周期按设置计算预算，资料覆盖仍需核对。"}
        )
    assert finance.reports.due(date(2026, 11, 2)) is None
    assert "记账的月报" in finance.context.store.digests(plugin="finance")["items"][0]["summary"]


async def test_monthly_changed_user_file_is_never_appended(finance):
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    row = finance.reports.prepare(start, end, date(2026, 11, 5), finance.now().isoformat())
    path = finance.context.memory.root / row["path"]
    path.write_text("用户修改")
    with pytest.raises(FinanceError, match="用户修改"):
        await finance.finance_report_write({"start": "2026-10-01", "text": "测试解读"})
    assert path.read_text() == "用户修改"


async def test_finance_chat_keeps_what_the_user_said(service):
    # Only a password the Agent used to unlock a bill is hidden (see the unlock test); the rest
    # of the chat stays, so 记账 remembers the conversation and the App shows the user's lines.
    await service.start()
    try:
        await service.plugins.patch("finance", PluginPatch(enabled=True))
        prompts = []

        async def agent(job, prompt, **kwargs):
            assert kwargs["grant"].reader == "记账"
            prompts.append(prompt)
            service.chat.reply(ids(prompt)[1], "记好了。")

        service.jobs.run_agent = agent
        await service.chat.send("finance", "午饭 18")
        await finish(service, "finance")
        await service.chat.send("finance", "奶茶不报")
        await finish(service, "finance")
        texts = [m["text"] for m in service.chat.page("finance")["items"]]
        assert "午饭 18" in texts and "奶茶不报" in texts
        assert "午饭 18" in prompts[1]  # history
    finally:
        await service.close()


async def test_finance_mcp_failures_have_fixed_runs_and_no_sensitive_logs(service, caplog):
    mcp = create_mcp(service)
    await service.start()
    try:
        assert not service.plugins.view("finance")["enabled"]
        await service.plugins.patch("finance", PluginPatch(enabled=True))
        _, result = await mcp.call_tool("finance_add", {"arguments": {"amount": "71.99 测试商户"}})
        assert result is None or "71.99" not in str(result)
    except Exception as exc:
        assert "71.99" not in str(exc) and "测试商户" not in str(exc)
    finally:
        assert "71.99" not in caplog.text and "测试商户" not in caplog.text
        assert "71.99" not in str(service.db.rows("SELECT * FROM runs"))
        await service.close()


async def test_red_packet_type_and_income_correction_no_duplicate(finance):
    statement = parsers.tables(
        parsers.csv_rows(
            csv_bill(
                [wx("red-type", direction="收入", kind="微信红包", item="/", counterpart="同学甲")]
            )
        ),
        ZONE,
    )
    finance.ledger.imported(statement, "red-type-sha", finance.now().isoformat())
    summary = await finance.finance_summary({})
    assert summary["red_packet_cents"] == 1800
    row = await finance.finance_add({"amount": 2300, "direction": "in", "item": "待确认"})
    assert row["kind"] == "income"
    await finance.finance_categorize({"id": row["id"], "kind": "extra_income"})
    assert len(finance.ledger.rows()) == 2
    assert (await finance.finance_summary({}))["extra_income_cents"] == 231800


async def test_self_transfer_cmb_and_platform_are_both_excluded(finance):
    ingest(
        finance,
        [raw("recharge", kind="transfer", direction="neutral", method="招商银行", item="零钱充值")],
    )
    ingest(finance, [raw("bank-recharge", item="财付通", method="招商银行")], "cmb")
    assert len(finance.ledger.rows(True)) == 1
    assert (await finance.finance_summary({}))["spent_cents"] == 0


async def test_reimbursement_no_decision_and_notification_survive_manual_merge(finance):
    finance.config["every_weekend_at_home"] = True
    manual = await finance.finance_add({"amount": 18, "occurred_at": "2026-10-23T18:00:00+08:00"})
    await finance.reimburse_check()
    await finance.finance_reimburse({"ids": [manual["id"]], "decision": "no"})
    ingest(finance, [raw("late-weekend", occurred_at="2026-10-23T18:30:00+08:00")])
    assert finance.ledger.rows(True)[0]["reimburse"] == "no"
    assert finance.ledger.rows(True)[0]["notified_at"] is not None
    before = finance.context.store.digests(plugin="finance")
    await finance.reimburse_check()
    assert finance.context.store.digests(plugin="finance") == before


async def test_year_budget_and_timezone_allowance_boundary(finance):
    ingest(
        finance,
        [
            raw(
                "new-year",
                occurred_at="2026-12-31T16:00:00+00:00",
                amount=210000,
                direction="in",
                kind="income",
                counterpart="同学甲",
            )
        ],
        "cmb",
    )
    summary = finance.ledger.summary("cycle", date(2027, 1, 1))
    assert summary["budget_cents"] == 210000
    assert summary["days_remaining"] == 31
    assert finance.ledger.summary("year", date(2027, 2, 1))["budget_cents"] == 410000


async def test_echoed_unlock_password_is_redacted_from_chat_reply(service):
    await service.start()
    try:
        await service.plugins.patch("finance", PluginPatch(enabled=True))
        plugin = service.plugins.instances["finance"]
        encrypted_zip(plugin.importer.ensure() / "test.zip", csv_bill(), "transient-secret")

        async def agent(job, prompt, **kwargs):
            with as_grant(kwargs["grant"]):
                await plugin.finance_unlock({"file": "test.zip", "password": "transient-secret"})
                service.chat.reply(ids(prompt)[1], "用了 transient-secret，已归档。")

        service.jobs.run_agent = agent
        await service.chat.send("finance", "test.zip 密码 transient-secret")
        await finish(service, "finance")
        with service.db.connect() as conn:
            assert "transient-secret" not in "\n".join(conn.iterdump())
        assert not service.chat.turns
        reply, line = service.chat.page("finance")["items"][:2]
        assert "密码已隐藏" in reply["text"]
        # Only the password goes; the rest of what the user said stays in the chat.
        assert line["text"] == "test.zip 密码 （密码已隐藏）"
    finally:
        await service.close()


async def test_a_password_given_a_turn_before_the_file_is_hidden_too(service):
    await service.start()
    try:
        await service.plugins.patch("finance", PluginPatch(enabled=True))
        plugin = service.plugins.instances["finance"]
        encrypted_zip(plugin.importer.ensure() / "test.zip", csv_bill(), "earlier-secret")

        async def ask(job, prompt, **kwargs):
            service.chat.reply(ids(prompt)[1], "哪个文件？")

        service.jobs.run_agent = ask
        await service.chat.send("finance", "密码是 earlier-secret")
        await finish(service, "finance")

        async def unlock(job, prompt, **kwargs):
            with as_grant(kwargs["grant"]):
                await plugin.finance_unlock({"file": "test.zip", "password": "earlier-secret"})
                service.chat.reply(ids(prompt)[1], "已归档。")

        service.jobs.run_agent = unlock
        await service.chat.send("finance", "test.zip")
        await finish(service, "finance")
        with service.db.connect() as conn:
            assert "earlier-secret" not in "\n".join(conn.iterdump())
        assert "密码是 （密码已隐藏）" in [m["text"] for m in service.chat.page("finance")["items"]]
    finally:
        await service.close()


async def test_refund_order_id_can_match_after_thirty_days(finance):
    finance.now = lambda: datetime(2026, 11, 20, tzinfo=ZONE)
    ingest(finance, [raw("original", at="2026-10-01", amount=3000)])
    ingest(
        finance,
        [
            raw(
                "refund-late",
                at="2026-11-15",
                amount=1000,
                direction="in",
                kind="refund",
                original_id="original",
            )
        ],
    )
    assert finance.ledger.summary("previous", finance.now().date())["spent_cents"] == 2000


async def test_monthly_complete_coverage_can_come_from_multiple_imports(finance):
    ingest(finance, [raw("some-data")])
    for source in ("wechat", "alipay", "cmb"):
        for first, last in (("2026-10-01", "2026-10-15"), ("2026-10-16", "2026-10-31")):
            key = source + first
            finance.ledger.db.execute(
                "INSERT INTO finance_imports(id,sha256,source,start,end,status,coverage,created_at) VALUES(?,?,?,?,?,'imported','statement',?)",
                (key, key, source, first, last, finance.now().isoformat()),
            )
    assert finance.reports.due(date(2026, 11, 1)) == (date(2026, 10, 1), date(2026, 10, 31))


async def test_daily_manual_activity_survives_later_import_merge(finance):
    await finance.finance_add({"amount": 18, "occurred_at": "2026-10-20T12:00:00+08:00"})
    day = finance.now().date()
    before = await finance.daily(day)
    finance.now = lambda: datetime(2026, 10, 30, 12, tzinfo=ZONE)
    ingest(finance, [raw("later-import")])
    assert await finance.daily(day) == before
    assert "测试商户" not in str(await finance.daily(finance.now().date()))


async def test_monthly_deadline_generates_even_when_all_sources_are_missing(finance):
    assert finance.reports.due(date(2026, 11, 4)) is None
    assert finance.reports.due(date(2026, 11, 5)) == (date(2026, 10, 1), date(2026, 10, 31))


async def test_alipay_unknown_income_does_not_auto_confirm_reimbursement(finance):
    row = await finance.finance_add({"amount": 18, "occurred_at": "2026-10-23T18:00:00+08:00"})
    await finance.finance_reimburse({"ids": [row["id"]], "decision": "yes"})
    ingest(
        finance, [raw("unrelated-alipay", at="2026-10-27", direction="in", kind="income")], "alipay"
    )
    assert finance.ledger.get(row["id"])["reimburse"] == "yes"


async def test_an_allowance_is_never_taken_for_a_reimbursement(finance):
    finance.config["every_weekend_at_home"] = True
    # Confirmed weekend spending that happens to add up to the allowance.
    ingest(finance, [raw("weekend", occurred_at="2026-10-24T12:00:00+08:00", amount=210000)])
    await finance.finance_reimburse({"ids": "all_ask", "decision": "yes"})
    ingest(
        finance,
        [raw("pocket", at="2026-11-01", amount=210000, direction="in", kind="income")],
        "cmb",
    )
    kinds = {r["source_id"]: (r["kind"], r["reimburse"]) for r in finance.ledger.rows()}
    assert kinds["pocket"] == ("allowance", "no")
    assert kinds["weekend"] == ("spend", "yes")


async def test_a_first_import_of_old_bills_asks_only_about_recent_weekends(finance):
    finance.config["every_weekend_at_home"] = True
    ingest(
        finance,
        [
            raw("july", occurred_at="2026-07-25T12:00:00+08:00"),
            raw("last-cycle", occurred_at="2026-09-26T12:00:00+08:00"),
            raw("this-cycle", occurred_at="2026-10-24T12:00:00+08:00"),
        ],
    )
    asked = {r["source_id"]: r["reimburse"] for r in finance.ledger.rows()}
    assert asked == {"july": "no", "last-cycle": "ask", "this-cycle": "ask"}


async def test_a_bill_that_failed_waits_until_it_changes_or_the_user_asks(finance, monkeypatch):
    inbox(finance, "unknown.csv", "时间,金额\n2026-10-20,18\n".encode())
    reads = []
    real = finance.importer.parse
    monkeypatch.setattr(finance.importer, "parse", lambda path: reads.append(path) or real(path))
    await finance.scan()
    await finance.scan()
    assert len(reads) == 1  # the second scan leaves it alone
    await finance.finance_import_now({})
    assert len(reads) == 2
    (finance.importer.ensure() / "unknown.csv").write_bytes("时间,金额\n2026-10-21,9\n".encode())
    await finance.scan()
    assert len(reads) == 3


async def test_the_reminder_and_cards_name_no_spending_at_home(service, finance):
    finance.config["every_weekend_at_home"] = True
    ingest(finance, [raw("weekend", occurred_at="2026-10-24T12:00:00+08:00")])
    await finance.reimburse_check()
    digest = service.store.digests(plugin="finance")["items"][0]["summary"]
    row = finance.ledger.rows()[0]
    assert "测试商户" not in digest and row["id"] not in digest
    assert "不占生活费" in digest
    # The page has no list of it: the total in the 账单 card only.
    cards = json.dumps(await finance.cards(), ensure_ascii=False)
    assert "测试商户" not in cards and row["id"] not in cards and "待确认报销" not in cards
    assert "在家时的花销" in cards and "18.00 元" in cards


def test_the_report_runner_cannot_write_memory(service):
    grant = Grant(None, "report", frozenset({"mcp"}), kind="finance_report", area="2026-10-01")
    with pytest.raises(ValueError):
        service.memory.access.require_write("每日/2026-10-30/笔记.md", grant)


def test_coverage_says_plainly_when_nothing_is_imported():
    from plugins.finance.reports import coverage_text

    empty = {s: {"through": None, "basis": None} for s in ("wechat", "alipay", "cmb")}
    assert coverage_text(empty) == "还没导入任何账单"
    empty["wechat"] = {"through": "2026-10-25", "basis": "statement"}
    assert coverage_text(empty) == (
        "微信账单截至 2026-10-25；支付宝还没导入；招商银行还没导入；之后的还没导入"
    )


async def test_wechat_refunds_as_real_bills_write_them(finance):
    # As in a real export: the purchase carries the refund status, the refund row has its own
    # order number and the platform's name.
    statement = parsers.tables(
        parsers.csv_rows(
            csv_bill(
                [
                    wx("tea", amount="17.60", counterpart="测试茶饮", status="已退款(¥2.00)"),
                    wx(
                        "tea-back",
                        amount="2.00",
                        direction="收入",
                        counterpart="测试茶饮",
                        kind="测试茶饮-退款",
                        status="已退款¥2.00",
                        at="2026-10-21 09:00:00",
                    ),
                    wx(
                        "meal",
                        amount="26.20",
                        counterpart="美团",
                        item="测试煲仔饭",
                        status="已全额退款",
                    ),
                    wx(
                        "meal-back",
                        amount="26.20",
                        direction="收入",
                        counterpart="美团平台商户",
                        kind="美团平台商户-退款",
                        status="已全额退款",
                        at="2026-10-21 10:00:00",
                    ),
                    wx(
                        "sent",
                        amount="60.00",
                        kind="转账",
                        counterpart="同学甲",
                        item="转账备注:测试",
                        status="对方已退还",
                    ),
                    wx(
                        "sent-back",
                        amount="60.00",
                        direction="收入",
                        kind="转账-退款",
                        counterpart="/",
                        item="转账备注:测试",
                        status="已全额退款",
                        at="2026-10-22 10:00:00",
                    ),
                    wx("packet", amount="40.00", kind="QQ红包", counterpart="QQ红包", item="/"),
                    wx(
                        "packet-back",
                        amount="40.00",
                        direction="收入",
                        kind="QQ红包-退款",
                        counterpart="QQ红包",
                        item="QQ红包",
                        status="已全额退款",
                        at="2026-10-23 10:00:00",
                    ),
                ]
            )
        ),
        ZONE,
    )
    tea = next(r for r in statement.rows if r["source_id"] == "tea")
    assert (tea["full_refund"], tea["refunded_cents"]) == (0, 200)
    finance.ledger.imported(statement, "real-shape", finance.now().isoformat())
    summary = finance.ledger.summary("cycle", date(2026, 10, 29))
    # 15.60 of the tea is spent; the meal, the returned transfer and the red packet are not.
    assert summary["spent_cents"] == 1560
    refunds = {
        r["source_id"]: r["refund_of"] for r in finance.ledger.rows() if r["kind"] == "refund"
    }
    assert refunds["meal-back:refund"] and refunds["sent-back:refund"]


async def test_money_back_with_no_purchase_found_still_lowers_spending(finance):
    ingest(finance, [raw("order", amount=42400, counterpart="测试服装")], "cmb")
    ingest(
        finance,
        [
            raw(
                "part",
                at="2026-10-22",
                amount=19208,
                direction="in",
                kind="refund",
                counterpart="测试服装",
            )
        ],
        "cmb",
    )
    assert finance.ledger.summary("cycle", date(2026, 10, 29))["spent_cents"] == 42400 - 19208


def test_the_item_decides_before_the_platform():
    from plugins.finance.categories import categorize

    assert categorize("美团", "测试私教健身工作室", {}, {}) == "娱乐"
    assert categorize("美团", "老乡鸡(测试店)-美团App", {}, {}) == "吃饭"
    assert categorize("美团", "美团App-订单", {}, {}) == "吃饭"  # nothing else to go on
    assert categorize("上海市轨道交通集团运营有限公司", "快捷支付", {}, {}) == "交通"
    assert categorize("GOOGLE*CLAUDE BY ANTH", "线上境外消费", {}, {}) == "会员订阅"


async def test_a_late_allowance_from_family_counts_once_a_period(finance):
    finance.ledger.config = {"family": ["家长甲"]}
    ingest(
        finance,
        [
            raw(
                "early-big",
                at="2026-09-25",
                amount=320000,
                direction="in",
                kind="income",
                counterpart="家长甲",
            ),
            raw(
                "late",
                at="2026-10-06",
                amount=200000,
                direction="in",
                kind="income",
                counterpart="家长甲",
            ),
            raw(
                "same-week",
                at="2026-10-07",
                amount=300000,
                direction="in",
                kind="income",
                counterpart="家长甲",
            ),
            raw(
                "stranger",
                at="2026-11-02",
                amount=200000,
                direction="in",
                kind="income",
                counterpart="同学甲",
            ),
        ],
        "cmb",
    )
    kinds = {r["source_id"]: r["kind"] for r in finance.ledger.rows()}
    assert kinds == {
        "early-big": "income",  # six days before the 1st
        "late": "allowance",  # the 6th instead of the 1st
        "same-week": "income",  # one allowance a period
        "stranger": "income",  # not family
    }
    assert finance.ledger.summary("cycle", date(2026, 10, 29))["budget_cents"] == 200000


async def test_money_from_family_is_not_unclear_income(finance):
    finance.ledger.config = {"family": ["家长甲", "家长乙"]}
    ingest(
        finance,
        [
            raw("mum", amount=30000, direction="in", kind="income", counterpart="家长乙"),
            raw("friend", amount=5000, direction="in", kind="income", counterpart="同学甲"),
        ],
    )
    summary = finance.ledger.summary("cycle", date(2026, 10, 29))
    assert (summary["family_income_cents"], summary["unknown_income_cents"]) == (30000, 5000)
    items = next(c for c in await finance.cards() if c["title"] == "账单")["data"]["items"]
    assert {"title": "家里转来", "subtitle": "300.00 元（生活费和报销以外）"} in items


async def test_only_the_days_the_user_was_home_are_asked_about(finance):
    # Off by default: a weekend is not assumed to be at home.
    ingest(
        finance,
        [
            raw("holiday", occurred_at="2026-10-03T12:00:00+08:00"),
            raw("school-weekend", occurred_at="2026-10-24T12:00:00+08:00"),
            raw("decided", occurred_at="2026-10-04T12:00:00+08:00", amount=900),
        ],
    )
    assert {r["reimburse"] for r in finance.ledger.rows()} == {"no"}
    decided = next(r for r in finance.ledger.rows() if r["source_id"] == "decided")
    await finance.finance_reimburse({"ids": [decided["id"]], "decision": "no"})
    result = await finance.finance_home({"start": "2026-10-01", "end": "2026-10-07"})
    assert result["periods"] == [{"start": "2026-10-01", "end": "2026-10-07"}]
    asked = {r["source_id"]: r["reimburse"] for r in finance.ledger.rows()}
    # The user's own decision stays.
    assert asked == {"holiday": "ask", "school-weekend": "no", "decided": "no"}
    assert (await finance.finance_summary({}))["home_periods"] == result["periods"]
    await finance.finance_home({"start": "2026-10-01", "end": "2026-10-07", "remove": True})
    assert (
        next(r for r in finance.ledger.rows() if r["source_id"] == "holiday")["reimburse"] == "no"
    )
    with pytest.raises(FinanceError):
        await finance.finance_home({"start": "2026-10-07", "end": "2026-10-01"})


async def test_a_source_the_user_does_not_use_is_never_missing(finance):
    finance.config["sources"] = ["wechat", "cmb"]
    ingest(finance, [raw("w", at="2026-09-10")], key="w")
    assert list(finance.ledger.coverage()) == ["wechat", "cmb"]
    assert "支付宝" not in json.dumps(await finance.cards(), ensure_ascii=False)
    # The monthly report waits only for the sources the user uses.
    for source in ("wechat", "cmb"):
        finance.ledger.db.execute(
            "INSERT INTO finance_imports(id,sha256,source,start,end,status,coverage,created_at) "
            "VALUES(?,?,?,?,?,'imported','statement',?)",
            (source, source, source, "2026-10-01", "2026-10-31", finance.now().isoformat()),
        )
    assert finance.reports.due(date(2026, 11, 1)) == (date(2026, 10, 1), date(2026, 10, 31))


def test_paying_again_after_a_refund_is_a_second_payment():
    # As on the user's statement: paid, got it back, paid again; both payments end at the
    # same balance.
    text = CMB_REAL_LAYOUT.replace(
        "2026-09-13 CNY 30.00 1,957.00 网联收款 测试用户\n",
        "2026-09-13 CNY 30.00 1,957.00 网联收款 测试用户\n"
        "2026-09-14 CNY -57.00 1,900.00 快捷支付 测试手表京东自营旗舰店\n"
        "2026-09-14 CNY 57.00 1,957.00 快捷退款 测试手表京东自营旗舰店\n"
        "2026-09-14 CNY -57.00 1,900.00 快捷支付 测试手表京东自营旗舰店\n",
    )
    rows = parsers.pdf(text, ZONE).rows
    paid = [r for r in rows if r["kind"] == "spend" and r["amount_cents"] == 5700]
    assert len(paid) == 2 and paid[0]["source_id"] != paid[1]["source_id"]
    # The first keeps the id it always had, so rows imported before are not duplicated.
    before = {r["source_id"] for r in parsers.pdf(CMB_REAL_LAYOUT, ZONE).rows}
    assert before <= {r["source_id"] for r in rows}


async def test_rereading_archived_bills_adds_only_what_was_missed(finance, monkeypatch):
    path = inbox(finance, "bill.pdf", b"%PDF-synthetic")
    first = parsers.pdf(CMB_REAL_LAYOUT, ZONE)
    missed = first.rows.pop()  # an older reader that did not see the last row
    monkeypatch.setattr(finance.importer, "parse", lambda p: first)
    await finance.scan()
    assert not path.exists()
    decided = next(r for r in finance.ledger.rows() if r["amount_cents"] == 300)
    await finance.finance_categorize({"id": decided["id"], "category": "娱乐", "remember": False})
    monkeypatch.setattr(finance.importer, "parse", lambda p: parsers.pdf(CMB_REAL_LAYOUT, ZONE))
    result = await finance.finance_reread({})
    assert result == {"files": 1, "added": 1, "skipped": 0}
    assert missed["source_id"] in {r["source_id"] for r in finance.ledger.rows()}
    assert finance.ledger.get(decided["id"])["category"] == "娱乐"  # the user's change stays
    assert (await finance.finance_reread({}))["added"] == 0


async def test_a_big_purchase_is_listed_on_its_own_not_in_the_living_budget(finance):
    ingest(
        finance,
        [
            raw(
                "watch",
                at="2026-10-15",
                amount=579141,
                counterpart="测试手表店",
                method="招商银行(1234)",
            ),
            raw("lunch", at="2026-10-16"),
            raw("gift", at="2026-10-17", amount=5000, direction="in", kind="income"),
        ],
        "cmb",
    )
    watch, gift = (
        next(r for r in finance.ledger.rows() if r["source_id"] == key) for key in ("watch", "gift")
    )
    assert (await finance.finance_big({"id": watch["id"], "note": "测试手表"}))["big"] == 1
    summary = finance.ledger.summary("cycle", date(2026, 10, 29))
    assert (summary["spent_cents"], summary["big_cents"]) == (1800, 579141)
    assert sum(summary["categories"].values()) == 1800
    assert finance.ledger.summary("year", date(2026, 10, 29))["big_cents"] == 579141
    card = next(c for c in await finance.cards() if c["title"] == "今年的大件")
    Card.model_validate(card)
    assert card["data"]["items"] == [
        {"title": "2026-10-15 测试手表", "subtitle": "5791.41 元 · 自己的钱，不占生活费"}
    ]
    assert (await finance.finance_transactions({"big": True}))["total"] == 1
    with pytest.raises(FinanceError, match="只能把支出记成大件"):
        await finance.finance_big({"id": gift["id"]})
    with pytest.raises(FinanceError, match="big 必须是布尔值"):
        await finance.finance_big({"id": watch["id"], "big": "yes"})

    # Put back, it counts as living costs again and leaves the list.
    await finance.finance_big({"id": watch["id"], "big": False})
    assert finance.ledger.summary("cycle", date(2026, 10, 29))["spent_cents"] == 1800 + 579141
    assert not any(c["title"] == "今年的大件" for c in await finance.cards())

    # Marked in chat before the bill came in: the imported row takes it over.
    entry = await finance.finance_add(
        {"amount": 3000, "occurred_at": "2026-10-20T10:00:00+08:00", "request_id": "m1"}
    )
    await finance.finance_big({"id": entry["id"]})
    ingest(finance, [raw("phone", at="2026-10-20", amount=300000, counterpart="测试手机店")])
    phone = next(r for r in finance.ledger.rows(True) if r["source_id"] == "phone")
    assert phone["big"] == 1
    assert finance.ledger.summary("cycle", date(2026, 10, 29))["big_cents"] == 300000


async def test_assets_are_the_newest_balance_of_each_account(finance, monkeypatch):
    statement = parsers.pdf(CMB_REAL_LAYOUT, ZONE)
    assert statement.balances == {"招商银行(0000)": 195700}  # the last row's balance
    assert (await finance.finance_balance({}))["accounts"] == []
    assert not any(c["title"] == "现有资产" for c in await finance.cards())
    assert finance.ledger.summary("cycle", date(2026, 10, 29))["assets_cents"] is None

    # A bank statement records its card's balance on the statement's last day.
    inbox(finance, "bill.pdf", b"%PDF-synthetic")
    monkeypatch.setattr(finance.importer, "parse", lambda p: parsers.pdf(CMB_REAL_LAYOUT, ZONE))
    await finance.scan()
    assets = await finance.finance_balance({"account": "微信零钱", "amount": "256.40"})
    assert [(a["account"], a["amount_cents"], a["as_of"]) for a in assets["accounts"]] == [
        ("微信零钱", 25640, "2026-10-29"),
        ("招商银行(0000)", 195700, "2026-10-28"),
    ]
    card = next(c for c in await finance.cards() if c["title"] == "现有资产")
    Card.model_validate(card)
    assert card["data"]["value"] == "2213.40"
    assert "微信零钱 256.40（10-29）" in card["data"]["label"]

    # The bank App's total stands for all its cards until a newer statement.
    await finance.finance_balance({"account": "招商银行", "amount": 1520.50})
    summary = finance.ledger.summary("cycle", date(2026, 10, 29))
    assert (summary["assets_cents"], summary["assets_as_of"]) == (152050 + 25640, "2026-10-29")
    assert "招商银行" not in json.dumps(summary, ensure_ascii=False)
    card = next(c for c in await finance.cards() if c["title"] == "现有资产")
    assert card["data"]["label"] == "微信零钱 256.40 · 招商银行 1520.50；截至 10-29"
    later = parsers.pdf(CMB_REAL_LAYOUT.replace("2026-10-28", "2026-11-28"), ZONE)
    finance.ledger.add_rows(later, None, finance.now().isoformat())
    names = [a["account"] for a in finance.ledger.assets()["accounts"]]
    assert names == ["微信零钱", "招商银行(0000)"]

    # Re-reading the archived bills does not undo what the user said on the same day.
    await finance.finance_balance({"account": "招商银行(0000)", "amount": 100, "at": "2026-10-28"})
    await finance.finance_reread({})
    rows = finance.ledger.db.rows("SELECT * FROM finance_balances WHERE at='2026-10-28'")
    assert {r["origin"] for r in rows} == {"statement", "user"}

    await finance.finance_balance({"account": "微信零钱", "remove": True})
    assert "微信零钱" not in [a["account"] for a in finance.ledger.assets()["accounts"]]
    for bad, message in (
        ({"account": "现金"}, "要给金额"),
        ({"account": "现金", "amount": 1, "at": "2026-10-30"}, "今天之后"),
        ({"account": "", "amount": 1}, "账户名称"),
        ({"account": "现金", "amount": 1, "remove": True}, "remove"),
    ):
        with pytest.raises(FinanceError, match=message):
            await finance.finance_balance(bad)


async def test_a_holders_name_kept_by_an_earlier_reader_is_replaced(finance):
    from plugins.finance.ledger import Ledger

    ingest(
        finance,
        [
            raw(
                "own",
                direction="neutral",
                kind="transfer",
                counterpart="测试用户",
                method="招商银行(0000)",
            ),
            raw("shop", counterpart="测试商户", method="招商银行(0000)"),
        ],
        "cmb",
    )
    finance.ledger.db.execute("INSERT INTO finance_rules VALUES('测试用户','其他')")
    ledger = Ledger(finance.ledger.db, finance.config, ZONE)  # what the next start does
    names = {r["source_id"]: r["counterpart"] for r in ledger.rows()}
    assert names == {"own": "自己的账户", "shop": "测试商户"}
    assert not ledger.db.rows("SELECT * FROM finance_rules WHERE counterpart='测试用户'")


async def test_decisions_on_a_bank_row_move_to_its_later_wechat_row(finance):
    ingest(
        finance,
        [raw("bank", amount=600000, method="招商银行(0000)", counterpart="测试手表店")],
        "cmb",
    )
    bank = finance.ledger.rows()[0]
    await finance.finance_big({"id": bank["id"], "note": "测试手表"})
    await finance.finance_categorize({"id": bank["id"], "category": "娱乐", "remember": False})
    ingest(
        finance, [raw("wx", amount=600000, method="招商银行储蓄卡(0000)", counterpart="测试手表店")]
    )
    shown = finance.ledger.get(bank["id"])
    assert shown["source_id"] == "wx"
    assert (shown["big"], shown["category"], shown["note"]) == (1, "娱乐", "测试手表")
    summary = finance.ledger.summary("cycle", date(2026, 10, 29))
    assert (summary["spent_cents"], summary["big_cents"]) == (0, 600000)


async def test_an_automatic_reimbursement_stays_after_the_next_reconcile(finance):
    finance.ledger.config = {"family": ["家长甲"]}
    entry = await finance.finance_add(
        {"amount": 50, "occurred_at": "2026-10-24T11:00:00+08:00", "request_id": "m"}
    )
    await finance.finance_reimburse({"ids": [entry["id"]], "decision": "yes"})
    ingest(finance, [raw("shop", at="2026-10-24", amount=5000)])  # merges the manual note
    ingest(
        finance,
        [
            raw(
                "back",
                at="2026-10-26",
                amount=5000,
                direction="in",
                kind="income",
                counterpart="家长甲",
            )
        ],
        key="back",
    )
    shop = next(r for r in finance.ledger.rows(True) if r["source_id"] == "shop")
    assert shop["reimburse"] == "reimbursed"
    finance.ledger.reconcile()
    assert finance.ledger.get(shop["id"])["reimburse"] == "reimbursed"
    assert (await finance.finance_summary({}))["pending_reimbursement_cents"] == 0


async def test_a_purchase_refunded_in_parts_takes_each_refund_once(finance):
    ingest(
        finance,
        [
            raw("order", at="2026-10-10", amount=10000, refunded_cents=5000),
            raw(
                "part1",
                at="2026-10-12",
                amount=2000,
                direction="in",
                kind="refund",
                counterpart="测试平台商户",
            ),
            raw(
                "part2",
                at="2026-10-15",
                amount=3000,
                direction="in",
                kind="refund",
                counterpart="测试平台商户",
            ),
        ],
    )
    order = next(r for r in finance.ledger.rows() if r["source_id"] == "order")
    assert {r["refund_of"] for r in finance.ledger.rows() if r["kind"] == "refund"} == {order["id"]}
    assert finance.ledger.summary("cycle", date(2026, 10, 29))["spent_cents"] == 5000


def period(spent, budget=200000, *, start="2026-10-01", end="2026-10-31", days=3, **extra):
    """A cycle's summary as the ledger gives it, for the advice alone."""
    categories = extra.pop("categories", {"吃饭": spent})
    return {
        "start": start,
        "end": end,
        "budget_cents": budget,
        "spent_cents": spent,
        "remaining_cents": budget - spent,
        "days_remaining": days,
        "daily_cents": round((budget - spent) / days) if days else None,
        "categories": categories,
        "takeout_cents": 0,
        "takeout_count": 0,
        **extra,
    }


def test_the_advice_follows_the_money_left():
    from plugins.finance.cards import advice

    september = period(150000, start="2026-09-01", end="2026-09-30", days=0)
    # 600 in 28 of 31 days: about 1,336 left at the end, half of it in fifties for a treat.
    tip = advice(period(60000), september, 28)
    assert tip["title"] == "记账的建议" and tip["data"]["symbol"] == "gift.fill"
    assert tip["data"]["headline"] == "照这个节奏，月底能剩 1,336 元左右"
    assert "可以拿 650 元买点喜欢的东西" in tip["data"]["text"]
    # Spending too fast: a daily limit and the takeout.
    fast = period(190000, takeout_cents=42000, takeout_count=14)
    tip = advice(fast, september, 28)
    assert tip["data"]["headline"] == "照现在的速度，月底会超 104 元"
    assert "每天控制在 33 元以内" in tip["data"]["text"]
    assert "外卖 14 次，共 420 元，少点几次就能补回来" in tip["data"]["text"]
    # Over already: stop the takeout, and where most of it went.
    over = period(240000, categories={"会员订阅": 150000, "吃饭": 90000, "其他": 200000})
    tip = advice(over, september, 28)
    assert tip["data"]["headline"] == "10月已经超了 400 元"
    assert tip["data"]["symbol"] == "takeoutbag.and.cup.and.straw.fill"
    assert "去食堂吃" in tip["data"]["text"]
    assert "花得最多的是会员订阅（1,500 元）：看看有没有不用的会员" in tip["data"]["text"]
    # About even.
    assert advice(period(185000), september, 28)["data"]["headline"].startswith("节奏刚好")
    # Too few days of bills for a pace: last month's result.
    tip = advice(period(0, days=31), september, 0)
    assert tip["data"]["headline"] == "9月剩了 500 元"
    assert (
        "可以拿 250 元" in tip["data"]["text"] and "剩下的留着当这个月的备用" in tip["data"]["text"]
    )
    tip = advice(period(0, days=31), period(230000, start="2026-09-01", end="2026-09-30"), 2)
    assert (
        tip["data"]["headline"] == "9月超了 300 元" and "这个月每天约 65 元" in tip["data"]["text"]
    )
    empty = period(0, start="2026-09-01", end="2026-09-30", days=0)
    assert advice(period(0, days=31), empty, 0)["data"]["headline"] == "还没有账单"


async def test_spending_at_home_is_the_familys_but_renewals_are_not(finance):
    finance.config["sources"] = ["wechat"]
    await finance.finance_home({"start": "2026-10-23", "end": "2026-10-25"})
    ingest(
        finance,
        [
            raw("august", at="2026-09-01", amount=100),
            raw("dinner", at="2026-10-24", amount=5000),
            raw("renewal", at="2026-10-24", amount=14400, counterpart="GOOGLE*CLAUDE", item="会员"),
            raw("lunch", at="2026-10-20", amount=1800, counterpart="美团外卖", item="外卖"),
        ],
    )
    reimburse = {r["source_id"]: r["reimburse"] for r in finance.ledger.rows()}
    assert reimburse == {"august": "no", "dinner": "ask", "renewal": "no", "lunch": "no"}
    summary = await finance.finance_summary({})
    assert (summary["spent_cents"], summary["ask_cents"]) == (16200, 5000)
    assert (summary["takeout_cents"], summary["takeout_count"]) == (1800, 1)
    cards = await finance.cards()
    for card in cards:
        Card.model_validate(card)
    titles = [c["title"] for c in cards]
    assert titles[:4] == ["10月还能花", "记账的建议", "10月钱花在哪儿", "每月花销"]
    hero = cards[0]["data"]
    assert hero["value"] == "1,838" and hero["bar"]["max"] == 2000
    assert [s["x"] for s in hero["bar"]["segments"]] == ["会员订阅", "吃饭"]
    assert "已花 162 / 2,000 元 · 还有 3 天，每天约 613 元" in hero["label"]
    assert "账单截至 10/24" in hero["label"]
    ring = cards[2]["data"]
    assert ring["value"] == "162" and ring["points"][0] == {
        "x": "会员订阅",
        "y": 144.0,
        "label": "144 元",
    }
    assert "外卖 1 次 18 元" in ring["label"]
    # Only the months every source's bills cover in full, against the allowance.
    bars = cards[3]["data"]
    assert [p["x"] for p in bars["points"]] == ["9月", "10月"]
    assert bars["target"] == {"value": 2000.0, "label": "生活费 2,000"}
    # A no puts it back.
    dinner = next(r for r in finance.ledger.rows() if r["source_id"] == "dinner")
    await finance.finance_reimburse({"ids": [dinner["id"]], "decision": "no"})
    assert (await finance.finance_summary({}))["spent_cents"] == 21200


async def test_the_family_pays_for_ai_and_red_packets_pay_it_back(finance):
    finance.config |= {"sources": ["wechat"], "family": ["妈妈"]}
    clock = finance.now().isoformat()

    def red(source_id, at, amount, sender="妈妈"):
        return raw(
            source_id,
            at=at,
            amount=amount,
            direction="in",
            kind="extra_income",
            extra_type="red_packet",
            counterpart=sender,
            item="/",
        )

    ingest(
        finance,
        [
            raw("lunch", at="2026-10-20", amount=1800),
            raw(
                "game", at="2026-10-06", amount=32800, counterpart="Apple", item="apple.com/bill/A"
            ),
            raw("api", at="2026-10-23", amount=5000, counterpart="深度求索", item="DeepSeek-API"),
            red("back", "2026-10-23", 5000),
            raw("app", at="2026-10-24", amount=4500, counterpart="Apple", item="apple.com/bill/B"),
            raw(
                "topup",
                at="2026-10-24",
                amount=20000,
                direction="in",
                kind="income",
                counterpart="妈妈",
            ),
            red("friend", "2026-10-24", 4500, sender="同学甲"),
            red("rounded", "2026-10-25", 5000),
            red("gift", "2026-10-26", 8200),
        ],
    )
    assert (await finance.finance_summary({}))["spent_cents"] == 44100
    # Named in the settings, they are the family's from then on, whatever day they were. A red
    # packet from family right after one pays it back, even rounded up to ten yuan; one from a
    # friend, a gift, a top-up and one with nothing left to pay back are not.
    finance.config["family_pays"] = ["deepseek", "apple.com/bill"]
    finance.ledger.refresh_reimburse(clock)
    ingest(finance, [red("later", "2026-10-25", 5000)], key="later")
    rows = {r["source_id"]: r for r in finance.ledger.rows()}
    assert {k: rows[k]["reimburse"] for k in ("lunch", "game", "api", "app")} == {
        "lunch": "no",
        "game": "yes",
        "api": "reimbursed",
        "app": "reimbursed",
    }
    assert {
        k: rows[k]["kind"] for k in ("back", "rounded", "later", "friend", "gift", "topup")
    } == {
        "back": "reimbursement",
        "rounded": "reimbursement",
        "later": "extra_income",
        "friend": "extra_income",
        "gift": "extra_income",
        "topup": "income",
    }
    summary = await finance.finance_summary({})
    assert summary["spent_cents"] == 1800 and summary["pending_reimbursement_cents"] == 32800
    assert summary["red_packet_cents"] == 5000 + 4500 + 8200
    # A game top-up through the same store is the user's own, and stays so.
    await finance.finance_reimburse({"ids": [rows["game"]["id"]], "decision": "no"})
    await finance.finance_categorize(
        {"id": rows["game"]["id"], "category": "娱乐", "remember": False}
    )
    finance.ledger.refresh_reimburse(clock)
    summary = await finance.finance_summary({})
    assert summary["spent_cents"] == 1800 + 32800 and summary["categories"]["娱乐"] == 32800
    assert summary["reimbursed_cents"] == 9500 and summary["pending_reimbursement_cents"] == 0
    bills = next(c for c in await finance.cards() if c["title"] == "账单")["data"]["items"]
    assert {"title": "家里报销", "subtitle": "95.00 元，不占生活费"} in bills


async def test_part_of_a_charge_can_be_the_users_own(finance):
    finance.config |= {"sources": ["wechat"], "family_pays": ["apple.com/bill"]}
    finance.ledger.refresh_reimburse(finance.now().isoformat())
    ingest(
        finance,
        [
            raw("lunch", at="2026-10-20", amount=1800),
            raw(
                "apple", at="2026-10-06", amount=16400, counterpart="Apple", item="apple.com/bill/C"
            ),
        ],
    )
    apple = next(r for r in finance.ledger.rows() if r["source_id"] == "apple")
    assert (await finance.finance_summary({}))["spent_cents"] == 1800
    # 128 of it was a game top-up: the user's, in 娱乐; the family keeps paying for the rest.
    result = await finance.finance_split({"id": apple["id"], "amount": 128, "category": "娱乐"})
    assert result["part"]["amount_cents"] == 12800 and result["part"]["reimburse"] == "no"
    summary = await finance.finance_summary({})
    assert summary["spent_cents"] == 1800 + 12800 and summary["categories"]["娱乐"] == 12800
    assert summary["pending_reimbursement_cents"] == 3600
    # Rules, new bills and a remembered category leave the part as it is; again replaces it.
    finance.ledger.refresh_reimburse(finance.now().isoformat())
    ingest(
        finance,
        [raw("apple", at="2026-10-06", amount=16400, counterpart="Apple", item="apple.com/bill/C")],
        key="again",
    )
    await finance.finance_categorize({"id": apple["id"], "category": "会员订阅"})
    assert (await finance.finance_summary({}))["categories"]["娱乐"] == 12800
    await finance.finance_split({"id": apple["id"], "amount": "100", "category": "娱乐"})
    summary = await finance.finance_summary({})
    assert summary["categories"]["娱乐"] == 10000 and summary["pending_reimbursement_cents"] == 6400
    await finance.finance_split({"id": apple["id"], "amount": 0, "category": "娱乐"})
    summary = await finance.finance_summary({})
    assert summary["spent_cents"] == 1800 and summary["pending_reimbursement_cents"] == 16400
    assert [r["source"] for r in finance.ledger.rows()].count("split") == 0
    for amount in (164, 200, -5):
        with pytest.raises(FinanceError):
            await finance.finance_split({"id": apple["id"], "amount": amount, "category": "娱乐"})
    lunch = next(r for r in finance.ledger.rows() if r["source_id"] == "lunch")
    with pytest.raises(FinanceError):
        await finance.finance_split({"id": lunch["id"], "amount": 5, "category": "零食"})


async def test_a_month_without_bills_says_so_and_looks_back(finance):
    finance.config["sources"] = ["wechat"]
    ingest(finance, [raw("sep", at="2026-09-10", amount=150000)])
    finance.now = lambda: datetime(2026, 10, 2, 9, tzinfo=ZONE)
    cards = await finance.cards()
    assert "10月的账单还没导入" in cards[0]["data"]["label"]
    assert cards[1]["data"]["headline"] == "9月剩了 500 元"
    assert cards[2]["title"] == "9月钱花在哪儿"
