from __future__ import annotations

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
    combine_date_and_time,
    compute_legacy_compatible_row_hash,
    compute_semantic_fingerprint,
    format_parse_summary,
    normalize_header_token,
    parse_currency_code,
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

# Priority-tiered column aliases prevent generic columns (like `交易类型` or `备注`)
# from preempting authoritative columns (like `收/支` or `交易对方`).
_ROLE_PRIORITY_TIERS: dict[str, tuple[set[str], ...]] = {
    "datetime": (
        {
            "datetime",
            "transactiontime",
            "交易时间",
            "发生时间",
            "创建时间",
            "记账时间",
        },
    ),
    "date": (
        {
            "date",
            "transactiondate",
            "交易日期",
            "记账日期",
            "入账日期",
            "发生日期",
        },
    ),
    "time": (
        {
            "time",
            "具体时间",
            "时间",
        },
    ),
    "payee": (
        {
            "payee",
            "merchant",
            "counterparty",
            "交易对方",
            "对方户名",
            "对方名称",
            "商户",
            "商家",
            "商户名称",
            "收付款人",
            "收款方",
            "付款方",
        },
        {
            "description",
            "summary",
            "商品说明",
            "商品",
            "交易摘要",
            "摘要",
            "项目",
        },
        {
            "remark",
            "memo",
            "note",
            "备注",
            "附言",
        },
    ),
    "memo": (
        {
            "description",
            "summary",
            "memo",
            "remark",
            "note",
            "商品说明",
            "商品",
            "交易摘要",
            "摘要",
            "备注",
            "附言",
        },
    ),
    "direction": (
        {
            "direction",
            "inout",
            "收支",
            "收支类型",
            "收/支",
            "资金方向",
            "借贷标志",
            "收付标志",
            "出入账",
        },
        {
            "type",
            "transactiontype",
            "交易类型",
            "类型",
            "业务类型",
        },
    ),
    "amount": (
        {
            "amount",
            "transactionamount",
            "金额",
            "交易金额",
            "发生额",
            "金额元",
            "实付金额",
            "记账金额",
        },
    ),
    "expense_amount": (
        {
            "debit",
            "debitamount",
            "expense",
            "expenseamount",
            "outflow",
            "支出",
            "支出金额",
            "借方发生额",
            "借方金额",
            "付款金额",
            "转出金额",
        },
    ),
    "income_amount": (
        {
            "credit",
            "creditamount",
            "income",
            "incomeamount",
            "inflow",
            "收入",
            "收入金额",
            "贷方发生额",
            "贷方金额",
            "收款金额",
            "转入金额",
        },
    ),
    "category": (
        {
            "category",
            "classification",
            "交易分类",
            "分类",
            "账单分类",
            "商品类型",
            "消费类别",
        },
    ),
    "currency": (
        {
            "currency",
            "ccy",
            "币种",
            "货币",
            "交易币种",
            "结算币种",
        },
    ),
    "external_id": (
        {
            "id",
            "transactionid",
            "reference",
            "ref",
            "fitid",
            "交易流水号",
            "流水号",
            "交易单号",
            "订单号",
            "凭证号",
        },
    ),
}

_EXPENSE_WORDS = ("支出", "付款", "消费", "转出", "扣款", "借", "out", "debit", "expense", "dr")
_INCOME_WORDS = ("收入", "收款", "入账", "转入", "存入", "贷", "in", "credit", "income", "cr")
_TRANSFER_WORDS = ("转账", "还款", "提现", "充值", "transfer", "repayment", "中性交易", "不计收支")


class GenericTableBillParser:
    """Priority-ranked CSV/TSV & XLSX parser for bank statements and custom templates."""

    provider_id: ProviderId = "csv"
    priority = 50

    def sniff(self, doc: ImportDocument) -> float:
        if doc.suffix == ".xlsx":
            return 0.65
        if doc.suffix in {".csv", ".tsv"}:
            return 0.60
        return 0.0

    def parse(self, doc: ImportDocument, limits: ParserLimits) -> ImportParseResult:
        provider: ProviderId = "xlsx" if doc.suffix == ".xlsx" else "csv"
        if doc.suffix == ".xls":
            return ImportParseResult(
                provider,
                "rejected",
                "暂不安全解析旧版 XLS，请另存为 XLSX 后重新导入",
            )
        if doc.suffix not in {".csv", ".tsv", ".xlsx"}:
            return ImportParseResult(
                provider,
                "rejected",
                "不支持该文件格式；当前可解析 CSV/TSV/XLSX/OFX/QFX",
            )
        try:
            sheets = read_document_sheets(doc, limits)
        except (FormatSecurityError, ImportLimitError, ValueError) as exc:
            return ImportParseResult(provider, "rejected", str(exc))

        all_records: list[NormalizedBillRecord] = []
        sheet_errors: list[str] = []

        for sheet in sheets:
            header_idx, roles = self._find_header(sheet.rows[:30])
            if header_idx is None:
                sheet_errors.append(f"{sheet.name}: 未找到包含日期、金额等字段的表头")
                continue
            try:
                sheet_records = self._parse_sheet(
                    sheet,
                    provider=provider,
                    header_idx=header_idx,
                    roles=roles,
                    limits=limits,
                    remaining_rows=limits.max_rows - len(all_records),
                )
            except ImportLimitError as exc:
                return ImportParseResult(provider, "rejected", str(exc))

            if sheet_records:
                all_records.extend(sheet_records)
            else:
                sheet_errors.append(f"{sheet.name}: 表格只有表头，没有账单记录")

        if not all_records:
            if len(sheets) == 1 and sheet_errors:
                # Preserve clean single-sheet rejection messages
                msg = sheet_errors[0].split(": ", 1)[-1]
            else:
                msg = "；".join(sheet_errors[:3]) or "未找到可识别的账单表头"
            return ImportParseResult(provider, "rejected", msg)

        return ImportParseResult(
            provider=provider,
            status="parsed",
            message=format_parse_summary(all_records),
            records=all_records,
        )

    @classmethod
    def _find_header(cls, rows: list[list[object]]) -> tuple[int | None, dict[str, int]]:
        best: tuple[int, int, dict[str, int]] | None = None
        for row_idx, row in enumerate(rows):
            normalized_cols = [normalize_header_token(cell) for cell in row]
            raw_cols = [(clean_cell_text(cell) or "").strip() for cell in row]
            roles: dict[str, int] = {}
            used_cols: set[int] = set()

            for role, tiers in _ROLE_PRIORITY_TIERS.items():
                matched_col: int | None = None
                for tier in tiers:
                    for col_idx, (norm_val, raw_val) in enumerate(
                        zip(normalized_cols, raw_cols, strict=False)
                    ):
                        if not norm_val:
                            continue
                        if role != "memo" and col_idx in used_cols:
                            continue
                        if norm_val in tier or raw_val in tier:
                            matched_col = col_idx
                            break
                    if matched_col is not None:
                        break
                if matched_col is not None:
                    roles[role] = matched_col
                    if role != "memo":
                        used_cols.add(matched_col)

            has_date = "datetime" in roles or "date" in roles
            has_amount = any(key in roles for key in ("amount", "expense_amount", "income_amount"))
            score = len([r for r in roles if r != "memo"])
            if has_date and has_amount and score >= 2:
                if best is None or score > best[0]:
                    best = (score, row_idx, roles)

        if best is None:
            return None, {}
        return best[1], best[2]

    def _parse_sheet(
        self,
        sheet: RawTableSheet,
        *,
        provider: ProviderId,
        header_idx: int,
        roles: dict[str, int],
        limits: ParserLimits,
        remaining_rows: int,
    ) -> list[NormalizedBillRecord]:
        headers = unique_headers(sheet.rows[header_idx])
        data_rows = sheet.rows[header_idx + 1 :]
        if len(data_rows) > remaining_rows:
            # Check non-empty count before rejecting padded spreadsheets
            non_empty_rows = [
                (offset, row)
                for offset, row in enumerate(data_rows)
                if any(clean_cell_text(val) for val in row)
            ]
            if len(non_empty_rows) > remaining_rows:
                raise ImportLimitError(f"账单超过 {limits.max_rows} 行")
        else:
            non_empty_rows = [
                (offset, row)
                for offset, row in enumerate(data_rows)
                if any(clean_cell_text(val) for val in row)
            ]

        records: list[NormalizedBillRecord] = []
        for offset, values in non_empty_rows:
            row_number = header_idx + 2 + offset
            raw_payload = build_raw_payload(headers, values)
            records.append(
                self._normalize_row(
                    values,
                    provider=provider,
                    roles=roles,
                    raw_payload=raw_payload,
                    source_ref=f"{sheet.name}!{row_number}",
                    limits=limits,
                )
            )
        return records

    @classmethod
    def _normalize_row(
        cls,
        values: list[object],
        *,
        provider: ProviderId,
        roles: dict[str, int],
        raw_payload: dict[str, str | None],
        source_ref: str,
        limits: ParserLimits,
    ) -> NormalizedBillRecord:
        issues: list[str] = []
        warnings: list[str] = []

        if "datetime" in roles:
            occurred_at = parse_statement_datetime(
                cell_at(values, roles.get("datetime")),
                default_tz=limits.default_timezone,
            )
        else:
            occurred_at = combine_date_and_time(
                cell_at(values, roles.get("date")),
                cell_at(values, roles.get("time")),
                default_tz=limits.default_timezone,
            )
        if occurred_at is None:
            issues.append("missing_or_invalid_date")

        payee = clean_cell_text(cell_at(values, roles.get("payee")))
        memo = clean_cell_text(cell_at(values, roles.get("memo")))
        if not payee and memo:
            payee = memo
        if not payee:
            issues.append("missing_payee")
        elif len(payee) > 300:
            payee = payee[:300]
            issues.append("payee_too_long")

        item_summary = memo if memo and memo != payee else None

        direction, amount_minor, direction_inferred, amount_issue = cls._parse_direction_and_amount(
            values, roles
        )
        if amount_issue:
            issues.append(amount_issue)

        direction_text = clean_cell_text(cell_at(values, roles.get("direction"))) or ""
        lower_dir = direction_text.casefold()
        if "退款" in direction_text or "refund" in lower_dir:
            direction = "refund"
            issues.append("refund_requires_original_transaction")
        elif direction is None and any(w in lower_dir for w in _TRANSFER_WORDS):
            direction = "transfer"
            issues.append("neutral_transfer_requires_counter_account")
        elif direction is None:
            issues.append("missing_or_ambiguous_direction")

        currency, currency_defaulted = parse_currency_code(
            cell_at(values, roles.get("currency")),
            default_currency=limits.default_currency,
        )
        if currency is None:
            currency = limits.default_currency
            issues.append("invalid_currency")

        category = clean_cell_text(cell_at(values, roles.get("category")))
        external_id = clean_external_id(cell_at(values, roles.get("external_id")))

        confidence = Decimal("0.99")
        if direction_inferred:
            confidence -= Decimal("0.08")
        if currency_defaulted:
            confidence -= Decimal("0.04")
        if issues:
            confidence = max(Decimal("0.10"), confidence - Decimal("0.30"))

        return NormalizedBillRecord(
            provider=provider,
            source_ref=source_ref,
            raw_payload=raw_payload,
            source_hash=compute_legacy_compatible_row_hash(raw_payload),
            fingerprint=compute_semantic_fingerprint(
                occurred_at=occurred_at,
                direction=direction,
                amount_minor=amount_minor,
                currency=currency,
                payee=payee,
                default_tz=limits.default_timezone,
            ),
            external_id=external_id,
            occurred_at=occurred_at,
            payee=payee,
            canonical_payee=canonicalize_payee(payee),
            item_summary=item_summary,
            direction=direction,
            amount_minor=amount_minor,
            currency=currency,
            original_category=category,
            confidence=confidence,
            issues=tuple(dict.fromkeys(issues)),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _parse_direction_and_amount(
        values: list[object],
        roles: dict[str, int],
    ) -> tuple[NormalizedDirection | None, int | None, bool, str | None]:
        try:
            expense = parse_minor_amount(cell_at(values, roles.get("expense_amount")))
            income = parse_minor_amount(cell_at(values, roles.get("income_amount")))
        except MinorAmountError as exc:
            return None, None, False, exc.issue

        if expense is not None and expense != 0 and (income is None or income == 0):
            return "expense", abs(expense), True, None
        if income is not None and income != 0 and (expense is None or expense == 0):
            return "income", abs(income), True, None
        if expense not in {None, 0} and income not in {None, 0}:
            return None, None, False, "both_income_and_expense_present"

        try:
            amount = parse_minor_amount(cell_at(values, roles.get("amount")))
        except MinorAmountError as exc:
            return None, None, False, exc.issue

        if amount is None or amount == 0:
            return None, None, False, "missing_or_invalid_amount"

        direction_text = (clean_cell_text(cell_at(values, roles.get("direction"))) or "").casefold()
        if any(word in direction_text for word in _EXPENSE_WORDS):
            return "expense", abs(amount), False, None
        if any(word in direction_text for word in _INCOME_WORDS):
            return "income", abs(amount), False, None
        if amount < 0:
            return "expense", abs(amount), True, None
        return None, abs(amount), False, None
