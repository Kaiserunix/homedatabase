from __future__ import annotations

import re
from decimal import Decimal

from .models import (
    FormatSecurityError,
    ImportDocument,
    ImportLimitError,
    ImportParseResult,
    MinorAmountError,
    NormalizedBillRecord,
    NormalizedDirection,
    ParserLimits,
)
from .normalizer import (
    canonicalize_payee,
    clean_cell_text,
    clean_external_id,
    compute_semantic_fingerprint,
    compute_source_hash,
    decode_text_bytes,
    format_parse_summary,
    normalize_header_token,
    parse_minor_amount,
    parse_statement_datetime,
)
from .tabular_io import (
    RawTableSheet,
    build_raw_payload,
    cell_at,
    read_document_sheets,
    unique_headers,
)

# Covers both modern Alipay App exports (`电子客户回单`) and classic Alipay Web exports
_ALIPAY_ROLE_TOKENS: dict[str, set[str]] = {
    "date": {"交易时间", "交易创建时间"},
    "paid_at": {"付款时间"},
    "category": {"交易分类", "类型"},
    "payee": {"交易对方"},
    "counterparty_account": {"对方账号"},
    "item": {"商品说明", "商品名称"},
    "inout": {"收支", "收/支", "资金状态"},
    "amount": {"金额", "金额元"},
    "payment_method": {"收付款方式", "收/付款方式"},
    "status": {"交易状态"},
    "external_id": {"交易订单号", "交易号"},
    "merchant_order_id": {"商家订单号", "商户订单号"},
    "refund_amount": {"成功退款元", "成功退款"},
    "remark": {"备注"},
}

_DIVIDER_RE = re.compile(r"^-{5,}")
_FOOTER_PREFIXES = (
    "共",
    "已收入",
    "已支出",
    "待收入",
    "待支出",
    "导出时间",
    "用户：",
    "用户:",
    "姓名：",
    "支付宝账户",
    "特别提示",
    "注意：",
)
_ACCOUNT_RE = re.compile(r"(?:支付宝账户|账号)[：:]\s*([^\s,，\r\n]+)")
_NAME_RE = re.compile(r"姓名[：:]\s*([^\s,，\r\n]+)")
_RANGE_RE = re.compile(
    r"起始(?:日期|时间)[：:]\s*\[?(?P<start>20\d{2}[-/]\d{2}[-/]\d{2}[^\]]*?)\]?"
    r"\s+终止(?:日期|时间)[：:]\s*\[?(?P<end>20\d{2}[-/]\d{2}[-/]\d{2}[^\],\r\n]*)\]?"
)

_CLOSED_ALIPAY_STATUSES = frozenset(
    {
        "交易关闭",
        "未付款",
        "关闭",
        "等待买家付款",
        "交易失败",
    }
)
_TRANSFER_STATUSES = frozenset(
    {
        "冻结成功",
        "解冻成功",
    }
)
_NEUTRAL_INOUT_VALUES = frozenset(
    {
        "不计收支",
        "资金转移",
        "/",
        "中性交易",
    }
)


class AlipayBillParser:
    """Dedicated parser for Alipay (支付宝) modern & classic CSV/XLSX statements."""

    provider_id = "alipay"
    priority = 100

    def sniff(self, doc: ImportDocument) -> float:
        if doc.suffix not in {".csv", ".tsv", ".xlsx"}:
            return 0.0
        hint = (doc.source_hint or "").casefold()
        filename = doc.filename.casefold()
        is_named_alipay = "支付宝" in filename or "alipay" in filename or "支付宝" in hint
        name_bonus = 0.15 if is_named_alipay else 0.0

        if doc.suffix == ".xlsx":
            try:
                sheets = read_document_sheets(doc, ParserLimits(max_rows=50))
            except Exception:
                return 0.0
            for sheet in sheets:
                score = self._score_rows(sheet.rows[:35])
                if score > 0:
                    return min(1.0, score + name_bonus)
            return 0.0

        text, _ = decode_text_bytes(doc.raw_bytes[:32768])
        if not text:
            return 0.0
        head = text[:4096]
        if (
            "支付宝交易记录明细查询" in head
            or "支付宝（中国）网络技术有限公司" in head
            or "支付宝账户" in head
        ):
            return 0.99
        lines = [line.split(",") for line in head.splitlines()[:35] if line.strip()]
        score = self._score_rows(lines)
        return min(1.0, score + name_bonus) if score > 0 else 0.0

    def parse(self, doc: ImportDocument, limits: ParserLimits) -> ImportParseResult:
        if doc.suffix not in {".csv", ".tsv", ".xlsx"}:
            return ImportParseResult("alipay", "rejected", "不支持的支付宝账单文件扩展名")
        try:
            sheets = read_document_sheets(doc, limits)
        except (FormatSecurityError, ImportLimitError, ValueError) as exc:
            return ImportParseResult("alipay", "rejected", str(exc))

        records: list[NormalizedBillRecord] = []
        metadata: dict[str, str] = {}
        warnings: list[str] = []

        for sheet in sheets:
            sheet_meta = self._extract_metadata(sheet.rows[:35])
            metadata.update({k: v for k, v in sheet_meta.items() if v})
            header_idx, roles = self._locate_header(sheet.rows[:40])
            if header_idx is None:
                continue
            sheet_records = self._parse_sheet(
                sheet,
                header_idx=header_idx,
                roles=roles,
                limits=limits,
                remaining_rows=limits.max_rows - len(records),
            )
            records.extend(sheet_records)

        if not records:
            return ImportParseResult(
                "alipay",
                "rejected",
                "未找到可识别的支付宝账单记录或表头",
                metadata=metadata,
            )

        message = format_parse_summary(records, provider_label="支付宝")
        return ImportParseResult(
            provider="alipay",
            status="parsed",
            message=message,
            records=records,
            metadata=metadata,
            warnings=warnings,
        )

    def _score_rows(self, rows: list[list[object]]) -> float:
        joined_top = "\n".join(
            " ".join(clean_cell_text(cell) or "" for cell in row) for row in rows[:25]
        )
        if "支付宝" in joined_top and ("明细" in joined_top or "电子客户回单" in joined_top):
            return 0.99
        header_idx, roles = self._locate_header(rows)
        if header_idx is None:
            return 0.0
        raw_headers = {clean_cell_text(c) or "" for c in rows[header_idx]}
        if {"交易订单号", "商家订单号"} <= raw_headers or {"交易号", "商家订单号"} <= raw_headers:
            if "status" in roles and "inout" in roles:
                return 0.95
        return 0.0

    @staticmethod
    def _extract_metadata(rows: list[list[object]]) -> dict[str, str]:
        meta: dict[str, str] = {}
        for row in rows:
            line = " ".join(clean_cell_text(cell) or "" for cell in row if cell is not None).strip()
            if not line:
                continue
            acct_match = _ACCOUNT_RE.search(line)
            if acct_match:
                meta["account"] = acct_match.group(1).strip("[]")
            name_match = _NAME_RE.search(line)
            if name_match:
                meta["member_name"] = name_match.group(1).strip("[]")
            range_match = _RANGE_RE.search(line)
            if range_match:
                meta["period_start"] = range_match.group("start").strip()
                meta["period_end"] = range_match.group("end").strip()
        return meta

    @staticmethod
    def _locate_header(rows: list[list[object]]) -> tuple[int | None, dict[str, int]]:
        for row_idx, row in enumerate(rows):
            roles: dict[str, int] = {}
            for col_idx, cell in enumerate(row):
                raw_text = clean_cell_text(cell) or ""
                token = normalize_header_token(raw_text)
                if raw_text.strip() in {"收/支", "收支"}:
                    roles["inout"] = col_idx
                    continue
                for role, accepted in _ALIPAY_ROLE_TOKENS.items():
                    if (token in accepted or raw_text.strip() in accepted) and role not in roles:
                        roles[role] = col_idx
                        break
            has_alipay_ids = "external_id" in roles and "merchant_order_id" in roles
            if {"date", "payee", "amount", "status"} <= set(roles) and has_alipay_ids:
                return row_idx, roles
        return None, {}

    def _parse_sheet(
        self,
        sheet: RawTableSheet,
        *,
        header_idx: int,
        roles: dict[str, int],
        limits: ParserLimits,
        remaining_rows: int,
    ) -> list[NormalizedBillRecord]:
        headers = unique_headers(sheet.rows[header_idx])
        data_rows = sheet.rows[header_idx + 1 :]
        records: list[NormalizedBillRecord] = []
        source_prefix = "Alipay" if sheet.name == "CSV" else f"Alipay:{sheet.name}"

        for offset, values in enumerate(data_rows):
            row_number = header_idx + 2 + offset
            non_empty = [clean_cell_text(v) for v in values if clean_cell_text(v)]
            if not non_empty:
                continue
            first_cell = non_empty[0] or ""
            # Alipay statements append a dashed divider and summary statistics at the bottom
            if _DIVIDER_RE.match(first_cell) or (
                len(non_empty) <= 2 and first_cell.startswith(_FOOTER_PREFIXES)
            ):
                break
            if len(records) >= remaining_rows:
                raise ImportLimitError(f"账单超过 {limits.max_rows} 行")

            raw_payload = build_raw_payload(headers, values)
            records.append(
                self._normalize_row(
                    values,
                    roles=roles,
                    raw_payload=raw_payload,
                    source_ref=f"{source_prefix}!{row_number}",
                    limits=limits,
                )
            )
        return records

    @staticmethod
    def _normalize_row(
        values: list[object],
        *,
        roles: dict[str, int],
        raw_payload: dict[str, str | None],
        source_ref: str,
        limits: ParserLimits,
    ) -> NormalizedBillRecord:
        issues: list[str] = []
        warnings: list[str] = []

        # Prefer actual payment time if present and valid; fall back to creation/transaction time
        occurred_at = parse_statement_datetime(
            cell_at(values, roles.get("paid_at")),
            default_tz=limits.default_timezone,
        ) or parse_statement_datetime(
            cell_at(values, roles.get("date")),
            default_tz=limits.default_timezone,
        )
        if occurred_at is None:
            issues.append("missing_or_invalid_date")

        category = clean_cell_text(cell_at(values, roles.get("category")))
        if category in {"/", "-"}:
            category = None

        raw_payee = clean_cell_text(cell_at(values, roles.get("payee")))
        raw_item = clean_cell_text(cell_at(values, roles.get("item")))
        item_summary = None if raw_item in {None, "/", "-"} else raw_item
        remark = clean_cell_text(cell_at(values, roles.get("remark")))
        if remark and remark not in {"/", "-"}:
            item_summary = f"{item_summary} ({remark})" if item_summary else remark

        payee = None if raw_payee in {None, "/", "-"} else raw_payee
        if not payee:
            payee = item_summary or category
        if not payee:
            issues.append("missing_payee")
        elif len(payee) > 300:
            payee = payee[:300]
            issues.append("payee_too_long")

        inout_text = (clean_cell_text(cell_at(values, roles.get("inout"))) or "").strip()
        tx_status = clean_cell_text(cell_at(values, roles.get("status")))
        payment_method = clean_cell_text(cell_at(values, roles.get("payment_method")))
        if payment_method in {"/", "-"}:
            payment_method = None

        external_id = clean_external_id(cell_at(values, roles.get("external_id")))
        merchant_order_id = clean_external_id(cell_at(values, roles.get("merchant_order_id")))

        amount_minor: int | None = None
        try:
            parsed_amount = parse_minor_amount(cell_at(values, roles.get("amount")))
            if parsed_amount is not None:
                amount_minor = abs(parsed_amount)
        except MinorAmountError as exc:
            issues.append(exc.issue)

        refund_col_minor: int | None = None
        if "refund_amount" in roles:
            try:
                refund_col_minor = parse_minor_amount(cell_at(values, roles.get("refund_amount")))
            except MinorAmountError:
                refund_col_minor = None

        status_text = tx_status or ""
        combined_desc = f"{category or ''} {item_summary or ''} {status_text}"
        direction: NormalizedDirection | None = None

        if status_text in _CLOSED_ALIPAY_STATUSES:
            # Cancelled/unpaid orders in Alipay often still say `支出` in `收/支`!
            # Filtering `交易关闭` prevents ghost expenses from polluting the ledger.
            direction = "ignored"
            issues.append("closed_or_unsettled_transaction")
        elif status_text == "退款成功" or (
            inout_text in {"不计收支", "收入", ""} and "退款" in combined_desc
        ):
            direction = "refund"
            issues.append("refund_requires_original_transaction")
        elif status_text in _TRANSFER_STATUSES or inout_text in _NEUTRAL_INOUT_VALUES:
            direction = "transfer"
            issues.append("neutral_transfer_requires_counter_account")
        elif inout_text in {"支出", "已支出"}:
            direction = "expense"
            if refund_col_minor and refund_col_minor > 0:
                warnings.append("expense_has_subsequent_refund")
        elif inout_text in {"收入", "已收入"}:
            direction = "income"
        else:
            issues.append("missing_or_ambiguous_direction")

        if direction != "ignored" and (amount_minor is None or amount_minor == 0):
            if not any(
                err in issues
                for err in (
                    "amount_exceeds_safe_range",
                    "ambiguous_xlsx_float_amount",
                    "amount_has_more_than_two_decimal_places",
                )
            ):
                issues.append("missing_or_invalid_amount")

        confidence = Decimal("0.99")
        if warnings:
            confidence -= Decimal("0.04")
        if issues:
            confidence = max(Decimal("0.15"), confidence - Decimal("0.25"))

        currency = limits.default_currency
        return NormalizedBillRecord(
            provider="alipay",
            source_ref=source_ref,
            raw_payload=raw_payload,
            source_hash=compute_source_hash("alipay", raw_payload),
            fingerprint=compute_semantic_fingerprint(
                occurred_at=occurred_at,
                direction=direction,
                amount_minor=amount_minor,
                currency=currency,
                payee=payee,
                default_tz=limits.default_timezone,
            ),
            external_id=external_id,
            merchant_order_id=merchant_order_id,
            occurred_at=occurred_at,
            payee=payee,
            canonical_payee=canonicalize_payee(payee),
            item_summary=item_summary,
            direction=direction,
            amount_minor=amount_minor,
            currency=currency,
            original_category=category,
            payment_method=payment_method,
            tx_status=tx_status,
            confidence=confidence,
            issues=tuple(dict.fromkeys(issues)),
            warnings=tuple(dict.fromkeys(warnings)),
        )
