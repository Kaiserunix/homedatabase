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
    ProviderId,
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

_WECHAT_ROLE_TOKENS: dict[str, set[str]] = {
    "date": {"交易时间", "创建时间"},
    "tx_type": {"交易类型"},
    "payee": {"交易对方"},
    "item": {"商品", "商品说明"},
    "inout": {"收支", "收/支"},
    "amount": {"金额元", "金额"},
    "payment_method": {"支付方式", "收付款方式"},
    "status": {"当前状态", "交易状态"},
    "external_id": {"交易单号", "微信订单号"},
    "merchant_order_id": {"商户单号"},
    "remark": {"备注"},
}

_PERIOD_RE = re.compile(
    r"起始时间[：:]\s*\[?(?P<start>20\d{2}[-/]\d{2}[-/]\d{2}[^\]]*?)\]?"
    r"\s+终止时间[：:]\s*\[?(?P<end>20\d{2}[-/]\d{2}[-/]\d{2}[^\],\r\n]*)\]?"
)
_NICKNAME_RE = re.compile(r"微信昵称[：:]\s*\[?(?P<nick>[^\],\r\n]+)\]?")
_DIVIDER_RE = re.compile(r"^-{5,}")

_CLOSED_STATUSES = frozenset({"交易关闭", "已取消", "支付失败", "未支付"})
_NEUTRAL_TX_KEYWORDS = (
    "零钱提现",
    "零钱充值",
    "零钱通转入",
    "零钱通转出",
    "信用卡还款",
    "购买理财通",
    "理财通赎回",
    "转入零钱通",
)


class WeChatBillParser:
    """Dedicated parser for WeChat Pay (微信支付) CSV and XLSX statement exports."""

    provider_id: ProviderId = "wechat"
    priority = 100

    def sniff(self, doc: ImportDocument) -> float:
        if doc.suffix not in {".csv", ".tsv", ".xlsx"}:
            return 0.0
        hint = (doc.source_hint or "").casefold()
        filename = doc.filename.casefold()
        name_bonus = 0.15 if ("微信" in filename or "wechat" in filename or "微信" in hint) else 0.0

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
        if "微信支付账单明细" in head or "微信昵称：" in head or "微信支付账单明细列表" in head:
            return 0.99
        lines = [line.split(",") for line in head.splitlines()[:35] if line.strip()]
        score = self._score_rows(lines)
        return min(1.0, score + name_bonus) if score > 0 else 0.0

    def parse(self, doc: ImportDocument, limits: ParserLimits) -> ImportParseResult:
        if doc.suffix not in {".csv", ".tsv", ".xlsx"}:
            return ImportParseResult("wechat", "rejected", "不支持的微信账单文件扩展名")
        try:
            sheets = read_document_sheets(doc, limits)
        except (FormatSecurityError, ImportLimitError, ValueError) as exc:
            return ImportParseResult("wechat", "rejected", str(exc))

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
                "wechat",
                "rejected",
                "未找到可识别的微信支付账单记录或表头",
                metadata=metadata,
            )

        message = format_parse_summary(records, provider_label="微信支付")
        return ImportParseResult(
            provider="wechat",
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
        if "微信支付账单明细" in joined_top or "微信昵称" in joined_top:
            return 0.99
        header_idx, roles = self._locate_header(rows)
        if header_idx is None:
            return 0.0
        # Require distinctly WeChat-specific column combination (`当前状态` + `交易单号` + `商品`)
        wechat_specific = {"tx_type", "item", "inout", "status", "external_id"}
        matched = len(wechat_specific.intersection(roles))
        if matched >= 4 and "当前状态" in {clean_cell_text(c) for c in rows[header_idx]}:
            return 0.94
        return 0.0

    @staticmethod
    def _extract_metadata(rows: list[list[object]]) -> dict[str, str]:
        meta: dict[str, str] = {}
        for row in rows:
            line = " ".join(clean_cell_text(cell) or "" for cell in row if cell is not None).strip()
            if not line:
                continue
            nick_match = _NICKNAME_RE.search(line)
            if nick_match:
                meta["nickname"] = nick_match.group("nick").strip()
            period_match = _PERIOD_RE.search(line)
            if period_match:
                meta["period_start"] = period_match.group("start").strip()
                meta["period_end"] = period_match.group("end").strip()
        return meta

    @staticmethod
    def _locate_header(rows: list[list[object]]) -> tuple[int | None, dict[str, int]]:
        for row_idx, row in enumerate(rows):
            roles: dict[str, int] = {}
            for col_idx, cell in enumerate(row):
                raw_text = clean_cell_text(cell) or ""
                token = normalize_header_token(raw_text)
                if raw_text.strip() == "收/支":
                    roles.setdefault("inout", col_idx)
                    continue
                for role, accepted in _WECHAT_ROLE_TOKENS.items():
                    if (token in accepted or raw_text.strip() in accepted) and role not in roles:
                        roles[role] = col_idx
                        break
            if {"date", "payee", "inout", "amount"} <= set(roles) and (
                "external_id" in roles or "status" in roles
            ):
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
        source_prefix = "WeChat" if sheet.name == "CSV" else f"WeChat:{sheet.name}"

        for offset, values in enumerate(data_rows):
            row_number = header_idx + 2 + offset
            non_empty = [clean_cell_text(v) for v in values if clean_cell_text(v)]
            if not non_empty:
                continue
            first_cell = non_empty[0] or ""
            if _DIVIDER_RE.match(first_cell) or first_cell.startswith("注："):
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

        occurred_at = parse_statement_datetime(
            cell_at(values, roles.get("date")),
            default_tz=limits.default_timezone,
        )
        if occurred_at is None:
            issues.append("missing_or_invalid_date")

        tx_type = clean_cell_text(cell_at(values, roles.get("tx_type")))
        raw_payee = clean_cell_text(cell_at(values, roles.get("payee")))
        raw_item = clean_cell_text(cell_at(values, roles.get("item")))
        item_summary = None if raw_item in {None, "/", "-"} else raw_item
        remark = clean_cell_text(cell_at(values, roles.get("remark")))
        if remark and remark not in {"/", "-"}:
            item_summary = f"{item_summary} ({remark})" if item_summary else remark

        payee = None if raw_payee in {None, "/", "-"} else raw_payee
        if not payee:
            payee = item_summary or tx_type
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

        direction: NormalizedDirection | None = None
        status_text = tx_status or ""
        type_text = tx_type or ""

        if status_text in _CLOSED_STATUSES:
            direction = "ignored"
            issues.append("closed_or_unsettled_transaction")
        elif "退款" in type_text or (
            inout_text == "收入" and ("退款" in status_text or "退还" in status_text)
        ):
            direction = "refund"
            issues.append("refund_requires_original_transaction")
        elif inout_text in {"/", "中性交易", "不计收支"} or (
            not inout_text and any(k in type_text for k in _NEUTRAL_TX_KEYWORDS)
        ):
            direction = "transfer"
            issues.append("neutral_transfer_requires_counter_account")
        elif inout_text == "支出":
            direction = "expense"
            if "退款" in status_text:
                warnings.append("expense_has_subsequent_refund")
        elif inout_text == "收入":
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
        canon_payee = canonicalize_payee(payee)
        return NormalizedBillRecord(
            provider="wechat",
            source_ref=source_ref,
            raw_payload=raw_payload,
            source_hash=compute_source_hash("wechat", raw_payload),
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
            canonical_payee=canon_payee,
            item_summary=item_summary,
            direction=direction,
            amount_minor=amount_minor,
            currency=currency,
            original_category=tx_type,
            payment_method=payment_method,
            tx_status=tx_status,
            confidence=confidence,
            issues=tuple(dict.fromkeys(issues)),
            warnings=tuple(dict.fromkeys(warnings)),
        )
