from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from ..money import MAX_SAFE_MINOR

ParseStatus = Literal["parsed", "needs_ocr", "rejected"]

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HEADER_CLEAN_RE = re.compile(r"[\s\-_./\\:：()（）\[\]【】{}]+")

_ALIASES: dict[str, set[str]] = {
    "date": {
        "date",
        "time",
        "datetime",
        "transactiondate",
        "transactiontime",
        "交易日期",
        "交易时间",
        "记账日期",
        "入账日期",
        "发生日期",
        "创建时间",
    },
    "payee": {
        "payee",
        "merchant",
        "counterparty",
        "description",
        "summary",
        "remark",
        "交易对方",
        "对方户名",
        "商户",
        "商家",
        "收款方",
        "付款方",
        "商品说明",
        "交易摘要",
        "摘要",
        "备注",
        "项目",
    },
    "direction": {
        "direction",
        "inout",
        "type",
        "transactiontype",
        "收支",
        "收支类型",
        "交易类型",
        "资金方向",
        "类型",
    },
    "amount": {
        "amount",
        "transactionamount",
        "金额",
        "交易金额",
        "发生额",
        "金额元",
    },
    "expense_amount": {
        "debit",
        "debitamount",
        "expense",
        "expenseamount",
        "outflow",
        "支出",
        "支出金额",
        "借方发生额",
        "付款金额",
    },
    "income_amount": {
        "credit",
        "creditamount",
        "income",
        "incomeamount",
        "inflow",
        "收入",
        "收入金额",
        "贷方发生额",
        "收款金额",
    },
    "category": {
        "category",
        "classification",
        "交易分类",
        "分类",
        "账单分类",
        "商品类型",
    },
    "currency": {"currency", "ccy", "币种", "货币", "交易币种"},
}


class ParseLimitError(ValueError):
    pass


class MinorParseError(ValueError):
    def __init__(self, issue: str):
        super().__init__(issue)
        self.issue = issue


@dataclass(slots=True)
class ParsedRow:
    source_ref: str
    raw_payload: dict[str, str | None]
    source_hash: str
    occurred_at: datetime | None
    payee: str | None
    direction: Literal["expense", "income"] | None
    amount_minor: int | None
    currency: str
    original_category: str | None
    confidence: Decimal | None
    issues: list[str] = field(default_factory=list)

    @property
    def can_create_candidate(self) -> bool:
        return (
            not self.issues
            and self.occurred_at is not None
            and bool(self.payee)
            and self.direction in {"expense", "income"}
            and self.amount_minor is not None
            and self.amount_minor > 0
        )


@dataclass(slots=True)
class ParseOutcome:
    status: ParseStatus
    message: str
    rows: list[ParsedRow] = field(default_factory=list)
    warning_count: int = 0

    @property
    def candidate_count(self) -> int:
        return sum(row.can_create_candidate for row in self.rows)

    @property
    def rejected_count(self) -> int:
        return sum(not row.can_create_candidate for row in self.rows)


def _parsed_rows_message(rows: list[ParsedRow]) -> str:
    valid = sum(row.can_create_candidate for row in rows)
    invalid = len(rows) - valid
    message = f"解析 {len(rows)} 行，生成 {valid} 条候选"
    if not invalid:
        return message
    issue_counts = Counter(issue for row in rows for issue in row.issues)
    safety_labels = []
    if issue_counts["amount_exceeds_safe_range"]:
        safety_labels.append(f"{issue_counts['amount_exceeds_safe_range']} 行金额超出安全范围")
    if issue_counts["ambiguous_xlsx_float_amount"]:
        safety_labels.append(
            f"{issue_counts['ambiguous_xlsx_float_amount']} 行 XLSX 大数精度不明确"
        )
    if issue_counts["amount_has_more_than_two_decimal_places"]:
        safety_labels.append(
            f"{issue_counts['amount_has_more_than_two_decimal_places']} 行金额超过两位小数"
        )
    detail = "；".join(safety_labels) or f"{invalid} 行字段不完整"
    return f"{message}，{detail}，已保留原始记录但未生成候选"


def parse_import_file(
    path: Path,
    filename: str,
    *,
    max_rows: int,
    max_uncompressed_bytes: int,
) -> ParseOutcome:
    suffix = Path(filename).suffix.casefold()
    if suffix in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic"}:
        if not _has_expected_signature(path, suffix):
            return ParseOutcome("rejected", "文件扩展名与内容不符")
        return ParseOutcome("needs_ocr", "文件已安全保存，等待多模态/OCR 工作器处理")
    if suffix in {".csv", ".tsv"}:
        return _parse_delimited(path, max_rows=max_rows)
    if suffix == ".xlsx":
        return _parse_xlsx(
            path,
            max_rows=max_rows,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
    if suffix == ".xls":
        return ParseOutcome("rejected", "暂不安全解析旧版 XLS，请另存为 XLSX 后重新导入")
    return ParseOutcome("rejected", "不支持该文件格式；当前可解析 CSV/TSV/XLSX")


def _has_expected_signature(path: Path, suffix: str) -> bool:
    with path.open("rb") as source:
        head = source.read(16)
    if suffix == ".pdf":
        return head.startswith(b"%PDF-")
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    if suffix == ".heic":
        return len(head) >= 12 and head[4:12] in {b"ftypheic", b"ftypheix", b"ftypmif1"}
    return False


def _parse_delimited(path: Path, *, max_rows: int) -> ParseOutcome:
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return ParseOutcome("rejected", "CSV/TSV 看起来是二进制文件")
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return ParseOutcome("rejected", "无法识别文本编码，请导出为 UTF-8 CSV")

    sample = text[:16384]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if "\t" in sample else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return _parse_table(rows, source_name="CSV", max_rows=max_rows)


def _parse_xlsx(path: Path, *, max_rows: int, max_uncompressed_bytes: int) -> ParseOutcome:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            expanded = sum(member.file_size for member in members)
            if len(members) > 10_000 or expanded > max_uncompressed_bytes:
                return ParseOutcome("rejected", "XLSX 解压后过大，已拒绝处理")
            for member in members:
                if member.compress_size == 0:
                    continue
                if member.file_size > member.compress_size * 500:
                    return ParseOutcome("rejected", "XLSX 压缩比异常，已拒绝处理")
    except zipfile.BadZipFile:
        return ParseOutcome("rejected", "XLSX 文件已损坏或扩展名与内容不符")

    workbook_source = path.open("rb")
    try:
        workbook = load_workbook(workbook_source, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl exposes several format-specific exception types
        workbook_source.close()
        return ParseOutcome("rejected", f"无法读取 XLSX：{type(exc).__name__}")

    parsed_rows: list[ParsedRow] = []
    sheet_messages: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            table: list[list[object]] = []
            for index, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
                if index > max_rows + 30:
                    raise ParseLimitError(f"单个工作表超过 {max_rows} 行")
                table.append(list(row))
            result = _parse_table(
                table,
                source_name=worksheet.title,
                max_rows=max_rows - len(parsed_rows),
            )
            if result.status == "parsed":
                parsed_rows.extend(result.rows)
            else:
                sheet_messages.append(f"{worksheet.title}: {result.message}")
            if len(parsed_rows) > max_rows:
                raise ParseLimitError(f"工作簿超过 {max_rows} 行")
    except ParseLimitError as exc:
        return ParseOutcome("rejected", str(exc))
    finally:
        workbook.close()
        workbook_source.close()

    if not parsed_rows:
        detail = "；".join(sheet_messages[:3]) or "未找到可识别的账单表头"
        return ParseOutcome("rejected", detail)
    return ParseOutcome("parsed", _parsed_rows_message(parsed_rows), parsed_rows)


def _parse_table(
    rows: list[list[object]],
    *,
    source_name: str,
    max_rows: int,
) -> ParseOutcome:
    header_index, roles = _find_header(rows[:30])
    if header_index is None:
        return ParseOutcome("rejected", "未找到包含日期、金额等字段的表头")
    headers = [
        _cell_text(value) or f"列{index + 1}" for index, value in enumerate(rows[header_index])
    ]
    parsed: list[ParsedRow] = []
    data_rows = rows[header_index + 1 :]
    if len(data_rows) > max_rows:
        raise ParseLimitError(f"账单超过 {max_rows} 行")
    for row_number, values in enumerate(data_rows, start=header_index + 2):
        if not any(_cell_text(value) for value in values):
            continue
        raw_payload = {
            _unique_header(headers, index): _cell_text(value)
            for index, value in enumerate(values)
            if index < len(headers)
        }
        parsed.append(
            _normalise_row(
                values,
                roles,
                raw_payload,
                source_ref=f"{source_name}!{row_number}",
            )
        )
    if not parsed:
        return ParseOutcome("rejected", "表格只有表头，没有账单记录")
    return ParseOutcome("parsed", _parsed_rows_message(parsed), parsed)


def _find_header(rows: list[list[object]]) -> tuple[int | None, dict[str, int]]:
    best: tuple[int, int, dict[str, int]] | None = None
    for row_index, row in enumerate(rows):
        roles: dict[str, int] = {}
        for column_index, value in enumerate(row):
            normalised = _normalise_header(_cell_text(value) or "")
            for role, aliases in _ALIASES.items():
                if normalised in aliases and role not in roles:
                    roles[role] = column_index
                    break
        has_amount = any(role in roles for role in ("amount", "expense_amount", "income_amount"))
        score = len(roles)
        if "date" in roles and has_amount and score >= 2:
            if best is None or score > best[0]:
                best = (score, row_index, roles)
    if best is None:
        return None, {}
    return best[1], best[2]


def _normalise_row(
    values: list[object],
    roles: dict[str, int],
    raw_payload: dict[str, str | None],
    *,
    source_ref: str,
) -> ParsedRow:
    issues: list[str] = []
    occurred_at = _parse_datetime(_value(values, roles.get("date")))
    if occurred_at is None:
        issues.append("missing_or_invalid_date")

    payee = _cell_text(_value(values, roles.get("payee")))
    if not payee:
        issues.append("missing_payee")
    elif len(payee) > 300:
        payee = payee[:300]
        issues.append("payee_too_long")

    direction, amount, direction_inferred, amount_issue = _parse_direction_and_amount(values, roles)
    if amount_issue:
        issues.append(amount_issue)
    if direction is None:
        issues.append("missing_or_ambiguous_direction")

    currency, currency_defaulted = _parse_currency(_value(values, roles.get("currency")))
    if currency is None:
        currency = "CNY"
        issues.append("invalid_currency")

    category = _cell_text(_value(values, roles.get("category")))
    direction_text = _cell_text(_value(values, roles.get("direction"))) or ""
    if "退款" in direction_text or "refund" in direction_text.casefold():
        direction = None
        issues.append("refund_requires_original_transaction")

    confidence = Decimal("0.99")
    if direction_inferred:
        confidence -= Decimal("0.08")
    if currency_defaulted:
        confidence -= Decimal("0.04")
    if issues:
        confidence = max(Decimal("0.10"), confidence - Decimal("0.30"))

    canonical = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ParsedRow(
        source_ref=source_ref,
        raw_payload=raw_payload,
        source_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        occurred_at=occurred_at,
        payee=payee,
        direction=direction,
        amount_minor=amount,
        currency=currency,
        original_category=category,
        confidence=confidence,
        issues=list(dict.fromkeys(issues)),
    )


def _parse_direction_and_amount(
    values: list[object], roles: dict[str, int]
) -> tuple[Literal["expense", "income"] | None, int | None, bool, str | None]:
    try:
        expense = _parse_minor(_value(values, roles.get("expense_amount")))
        income = _parse_minor(_value(values, roles.get("income_amount")))
    except MinorParseError as exc:
        return None, None, False, exc.issue
    if expense is not None and expense != 0 and (income is None or income == 0):
        return "expense", abs(expense), True, None
    if income is not None and income != 0 and (expense is None or expense == 0):
        return "income", abs(income), True, None
    if expense not in {None, 0} and income not in {None, 0}:
        return None, None, False, "both_income_and_expense_present"

    try:
        amount = _parse_minor(_value(values, roles.get("amount")))
    except MinorParseError as exc:
        return None, None, False, exc.issue
    if amount is None or amount == 0:
        return None, None, False, "missing_or_invalid_amount"
    direction_text = (_cell_text(_value(values, roles.get("direction"))) or "").casefold()
    expense_words = ("支出", "付款", "消费", "借", "out", "debit", "expense")
    income_words = ("收入", "收款", "入账", "贷", "in", "credit", "income")
    if any(word in direction_text for word in expense_words):
        return "expense", abs(amount), False, None
    if any(word in direction_text for word in income_words):
        return "income", abs(amount), False, None
    if amount < 0:
        return "expense", abs(amount), True, None
    return None, abs(amount), False, None


def _parse_minor(value: object | None) -> int | None:
    if isinstance(value, float) and math.ulp(value) >= 0.01:
        raise MinorParseError("ambiguous_xlsx_float_amount")
    text = _cell_text(value)
    if text is None:
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")")
    cleaned = (
        text.replace(",", "")
        .replace("，", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace("元", "")
        .replace("CNY", "")
        .replace("RMB", "")
        .strip()
    )
    if negative_parentheses:
        cleaned = f"-{cleaned[1:-1]}"
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        raise MinorParseError("amount_has_more_than_two_decimal_places")
    minor = int(scaled)
    if abs(minor) > MAX_SAFE_MINOR:
        raise MinorParseError("amount_exceeds_safe_range")
    return minor


def _parse_datetime(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_SHANGHAI)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=_SHANGHAI)
    text = _cell_text(value)
    if not text:
        return None
    cleaned = text.strip().replace("年", "-").replace("月", "-").replace("日", " ").strip()
    iso_candidate = cleaned.replace("/", "-")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_SHANGHAI)
    except ValueError:
        pass
    for pattern in (
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m-%d-%Y",
    ):
        try:
            return datetime.strptime(iso_candidate, pattern).replace(tzinfo=_SHANGHAI)
        except ValueError:
            continue
    return None


def _parse_currency(value: object | None) -> tuple[str | None, bool]:
    text = (_cell_text(value) or "").strip().upper()
    if not text:
        return "CNY", True
    aliases = {"人民币": "CNY", "RMB": "CNY", "￥": "CNY", "美元": "USD", "美金": "USD"}
    text = aliases.get(text, text)
    if re.fullmatch(r"[A-Z]{3}", text):
        return text, False
    return None, False


def _normalise_header(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    return _HEADER_CLEAN_RE.sub("", value)


def _cell_text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _value(values: list[object], index: int | None) -> object | None:
    if index is None or index >= len(values):
        return None
    return values[index]


def _unique_header(headers: list[str], index: int) -> str:
    header = headers[index]
    if headers[:index].count(header) == 0:
        return header
    return f"{header}#{headers[:index].count(header) + 1}"


def normalise_to_utc(value: datetime) -> datetime:
    """Normalise parser output before exact database comparisons."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=_SHANGHAI)
    return value.astimezone(UTC)
