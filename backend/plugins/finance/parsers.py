"""Strict header-based offline readers. An uncertain row rejects the whole statement."""

import csv
import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree as ET

from plugins.finance.config import FinanceError, money, timestamp

MAX_BYTES = 50 * 1024 * 1024
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
DATE = r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"


@dataclass
class Statement:
    source: str
    rows: list
    start: date
    end: date
    coverage: str = "transactions"
    # What each account held at `end` (a bank statement's last balance), in cents.
    balances: dict = field(default_factory=dict)


def clean(value):
    return str(value or "").strip().strip("\ufeff").strip().strip('="').strip()


def header(value):
    return re.sub(r"[\s()（）/元]", "", clean(value))


ALIASES = {
    "交易时间": "time",
    "付款时间": "time",
    "交易日期": "time",
    "交易类型": "type",
    "交易分类": "type",
    "交易对方": "counterpart",
    "商品": "item",
    "商品说明": "item",
    "收支": "direction",
    "金额": "amount",
    "支付方式": "method",
    "收付款方式": "method",
    "当前状态": "status",
    "交易状态": "status",
    "交易单号": "id",
    "交易订单号": "id",
    "商户单号": "merchant_id",
    "商家订单号": "merchant_id",
    "备注": "note",
}


def identify(values):
    names = [header(v) for v in values]
    source = None
    if "交易单号" in names and "商品" in names:
        source = "wechat"
    if "交易订单号" in names and "商品说明" in names:
        source = "alipay"
    keys = [ALIASES.get(n) for n in names]
    required = {
        "time",
        "type",
        "counterpart",
        "item",
        "direction",
        "amount",
        "method",
        "status",
        "id",
    }
    if not source or not required <= set(keys):
        return None
    present = [k for k in keys if k]
    if len(set(present)) != len(present):
        raise FinanceError("账单表头有重复列")
    return source, keys


def csv_rows(raw):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            return list(csv.reader(io.StringIO(text)))
        except UnicodeError:
            continue
    raise FinanceError("账单文字编码无法识别")


def xlsx_rows(path):
    try:
        with zipfile.ZipFile(path) as book:
            if sum(i.file_size for i in book.infolist()) > MAX_BYTES:
                raise FinanceError("账单解压后过大")
            workbook = ET.fromstring(book.read("xl/workbook.xml"))
            props = workbook.find("s:workbookPr", NS)
            if props is not None and props.get("date1904") in {"1", "true"}:
                raise FinanceError("暂不支持这种电子表格日期系统")
            strings = []
            if "xl/sharedStrings.xml" in book.namelist():
                strings = [
                    "".join(s.itertext())
                    for s in ET.fromstring(book.read("xl/sharedStrings.xml")).findall("s:si", NS)
                ]
            first = workbook.find("s:sheets/s:sheet", NS)
            rid = first.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            relations = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
            target = next(r.get("Target") for r in relations if r.get("Id") == rid)
            name = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else "xl/" + target
            )
            if not name.startswith("xl/"):
                raise FinanceError("电子表格路径无效")
            sheet = ET.fromstring(book.read(name))
            rows = []
            for row in sheet.findall("s:sheetData/s:row", NS):
                values = []
                for cell in row.findall("s:c", NS):
                    ref = re.match(r"([A-Z]+)", cell.get("r", ""))
                    if not ref:
                        raise FinanceError("电子表格单元格无效")
                    column = 0
                    for letter in ref[1]:
                        column = column * 26 + ord(letter) - 64
                    if column > 100:
                        raise FinanceError("电子表格列数过多")
                    values.extend([""] * (column - len(values)))
                    value = cell.findtext("s:v", "", NS)
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(cell.find("s:is", NS).itertext())
                    values[column - 1] = value
                rows.append(values)
            return rows
    except FinanceError:
        raise
    except Exception:
        raise FinanceError("电子表格无法读取") from None


def period(rows, zone):
    text = "\n".join(" ".join(r) for r in rows)
    begin = re.search(r"(?:起始时间|开始时间|起始日期|开始日期)[:：\s\[\]]*(" + DATE + r")", text)
    end = re.search(r"(?:终止时间|结束时间|终止日期|结束日期)[:：\s\[\]]*(" + DATE + r")", text)
    if begin and end:
        return timestamp(begin[1], zone).date(), timestamp(end[1], zone).date()
    dashed = re.search(r"^\s*(" + DATE + r")\s*--\s*(" + DATE + r")\s*$", text, re.MULTILINE)
    if dashed:
        return timestamp(dashed[1], zone).date(), timestamp(dashed[2], zone).date()
    both = re.search(
        r"(?:起止日期|账单期间|交易期间|查询期间|查询日期)[:：\s]*("
        + DATE
        + r").{0,30}?("
        + DATE
        + r")",
        text,
    )
    if both:
        return timestamp(both[1], zone).date(), timestamp(both[2], zone).date()
    return None


def statement(source, records, rows, zone):
    coverage = period(rows, zone)
    dates = [timestamp(r["occurred_at"], zone).date() for r in records]
    if not coverage and not dates:
        raise FinanceError("账单没有可确认的覆盖日期")
    start, end = coverage or (min(dates), max(dates))
    if start > end or any(d < start or d > end for d in dates):
        raise FinanceError("账单覆盖日期不一致")
    return Statement(source, records, start, end, "statement" if coverage else "transactions")


PARTIAL = re.compile(r"已退款[（(]?\s*[¥￥]\s*([\d,]+(?:\.\d+)?)")


def tables(rows, zone):
    for index, values in enumerate(rows):
        recognized = identify(values)
        if recognized:
            header_index = index
            break
    else:
        raise FinanceError("认不出账单表头")
    source, keys = recognized
    records = []
    for values in rows[header_index + 1 :]:
        if not any(clean(v) for v in values):
            continue
        # Only known explanatory/footer lines are skipped, never a malformed date row.
        first = clean(values[0]) if values else ""
        if first.startswith(
            ("-", "共", "总计", "收入：", "支出：", "中性交易", "温馨提示", "说明")
        ):
            continue
        if identify(values):  # repeated header
            continue
        row = {k: clean(values[i]) if i < len(values) else "" for i, k in enumerate(keys) if k}
        at = timestamp(row["time"], zone)
        if "交易关闭" in row["status"]:
            continue
        if not row["id"] or row["id"] in {"/", "-"}:
            raise FinanceError("账单缺少交易单号")
        sign = row["direction"]
        if sign in {"/", "中性交易", "不计收支", "其他"}:
            direction, kind = "neutral", "transfer"
        elif sign in {"支出", "收入"}:
            direction = "out" if sign == "支出" else "in"
            kind = "spend" if direction == "out" else "income"
        else:
            raise FinanceError("账单收支方向无法识别")
        description = row["type"] + row["item"]
        if any(
            word in description
            for word in (
                "零钱充值",
                "零钱提现",
                "转入零钱通",
                "转出零钱通",
                "余额宝转入",
                "余额宝转出",
                "提现",
            )
        ):
            direction, kind = "neutral", "transfer"
        if direction == "in" and "退款" in description + row["status"]:
            kind = "refund"
        original = re.search(
            r"(?:原交易单号|原订单号)[:：\s]*([A-Za-z0-9_-]+)", row.get("note", "")
        )
        records.append(
            {
                "source_id": row["id"] + (":refund" if kind == "refund" else ""),
                "extra_type": "red_packet" if source == "wechat" and "红包" in row["type"] else "",
                "occurred_at": at.isoformat(),
                "amount_cents": money(row["amount"]),
                "direction": direction,
                "kind": kind,
                "counterpart": row["counterpart"],
                "item": row["item"],
                "method": row["method"],
                "note": row.get("note", ""),
                "merchant_id": row.get("merchant_id", ""),
                "original_id": original[1] if original else row["id"] if kind == "refund" else "",
                # WeChat marks the purchase itself: 「已全额退款」, 「对方已退还」 (a transfer sent
                # back), or 「已退款(¥2.00)」 for part of it.
                "full_refund": int(
                    direction == "out"
                    and any(s in row["status"] for s in ("已全额退款", "对方已退还"))
                ),
                "refunded_cents": money(partial[1])
                if direction == "out" and (partial := PARTIAL.search(row["status"]))
                else 0,
            }
        )
    return statement(source, records, rows[:header_index], zone)


# 招商银行 交易流水 (checked against real statements, 2026-10): a header block with 户名 and 账号,
# then per page the column titles, rows, and a 「1/5」 page number. A row is one dated line:
# 记账日期 货币 交易金额 联机余额 交易摘要 [对手信息]. A long 对手信息 wraps; its lines follow
# the row, and for a cell taller than the row PDF text order puts the first one *above* it.
CMB_ROW = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(?:人民币|CNY|RMB)\s+([+-]?[\d,]+\.\d{2})\s+([+-]?[\d,]+\.\d{2})"
    r"(?:\s+(\S+))?(?:\s+(.+))?$"
)
# Page markers, the column titles repeated on every page (Chinese and English), page numbers.
CMB_SKIP = re.compile(
    r"^(?:\[第 \d+ 页\]|\d+\s*/\s*\d+|第\s*\d+\s*页.*)$|^(?:记账日期|Date\s+Currency|Amount\s+Balance)"
)
# The counterparty of money between the holder's own accounts.
OWN_ACCOUNT = "自己的账户"
# Positive amounts under these are the money coming back from a purchase.
CMB_PAYMENTS = {"快捷支付", "银联快捷支付", "银联线上有卡支付", "消费", "POS消费", "线上境外消费"}


def pdf(text, zone):
    names = ("记账日期", "货币", "交易金额", "联机余额", "交易摘要", "对手信息")
    compact = re.sub(r"\s", "", text)
    if not all(n in compact for n in names):
        raise FinanceError("认不出招商银行账单表头")
    # The holder's own name as counterparty means money between their own accounts (their
    # WeChat or Alipay balance, their other card). Used here only, never stored.
    holder = re.search(r"户\s*名[:：]\s*(\S+)", text)
    holder = holder[1] if holder else None
    account = re.search(r"账\s*号[:：]\s*\S*?(\d{4})(?:\s|$)", text)
    method = f"招商银行({account[1]})" if account else "招商银行"
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip().startswith("记账日期")), None)
    if first is None:
        raise FinanceError("认不出招商银行账单表头")
    parsed, gap = [], []
    for line in lines[first:]:
        line = line.strip()
        # A rule of dashes and 「温馨提示」 close the statement.
        if re.match(r"^[—\-_]{5,}|^温馨提示", line):
            break
        if not line or CMB_SKIP.match(line):
            continue
        match = CMB_ROW.match(line)
        if not match:
            # A dated line that is not a row (another currency, a changed layout) must not
            # vanish into a counterparty.
            if re.match(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", line):
                raise FinanceError("招商银行版式无法确认，请保留原件核对")
            gap.append(line)
            continue
        day, amount, balance, summary, counterpart = match.groups()
        row = {
            "day": day,
            "cents": money(amount, signed=True),
            "balance": money(balance, signed=True),
            "summary": summary or "",
            "counterpart": [counterpart] if counterpart else [],
        }
        if parsed:
            # Lines between two rows continue the earlier one, except the start of a wrapped
            # cell that belongs to a row with no counterparty on its own line: the last of
            # two or more lines, or a single merchant descriptor such as 「GOOGLE*…」.
            prefix = []
            if not row["counterpart"] and (len(gap) >= 2 or (gap and "*" in gap[-1])):
                prefix = [gap.pop()]
            parsed[-1]["counterpart"] += gap
            row["counterpart"] = prefix + row["counterpart"]
        gap = []
        parsed.append(row)
    if parsed:
        parsed[-1]["counterpart"] += gap
    # Each balance must follow from the one before: proof that no row was lost or misread.
    pairs = zip(parsed, parsed[1:], strict=False)
    if any(a["balance"] + b["cents"] != b["balance"] for a, b in pairs):
        raise FinanceError("招商银行余额对不上，版式无法确认，请保留原件核对")
    records, seen = [], {}
    for row in parsed:
        cents, summary = row["cents"], row["summary"]
        counterpart = "".join(row["counterpart"])
        if cents == 0:
            continue
        if holder and counterpart == holder:
            # Never kept: the holder's name would reach 记账's model with the details.
            direction, kind, counterpart = "neutral", "transfer", OWN_ACCOUNT
        elif cents > 0:
            direction = "in"
            refund = "退款" in summary or "退货" in summary or summary in CMB_PAYMENTS
            kind = "refund" if refund else "income"
        else:
            direction, kind = "out", "spend"
        stamp = timestamp(row["day"], zone).isoformat()
        # Without the counterparty, whose wrapping may differ between exports: a later
        # statement overlapping this one gives the same ids. Paying, getting it back and
        # paying again leaves two rows alike in everything, balance too: the second and later
        # ones are numbered (statements cover whole days, so the numbers agree).
        key = "|".join((method, stamp, str(cents), str(row["balance"]), summary))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key += f"|{seen[key]}"
        identity = hashlib.sha256(key.encode()).hexdigest()
        records.append(
            {
                "source_id": identity,
                "occurred_at": stamp,
                "amount_cents": abs(cents),
                "direction": direction,
                "kind": kind,
                "counterpart": counterpart,
                "item": summary,
                "method": method,
                "note": "",
            }
        )
    result = statement("cmb", records, [[line] for line in lines], zone)
    if parsed:
        result.balances = {method: parsed[-1]["balance"]}
    return result
