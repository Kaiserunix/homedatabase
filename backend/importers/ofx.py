from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation

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
    parse_currency_code,
    parse_minor_amount,
    parse_ofx_datetime,
)

_XXE_GUARD_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_STMTRS_BLOCK_RE = re.compile(
    r"<(?P<kind>STMTRS|CCSTMTRS)\b[^>]*>(?P<body>.*?)</(?P=kind)>",
    re.IGNORECASE | re.DOTALL,
)
_STMTTRN_CLOSED_RE = re.compile(
    r"<STMTTRN\b[^>]*>(?P<body>.*?)</STMTTRN>",
    re.IGNORECASE | re.DOTALL,
)
# In legacy OFX 1.02 SGML exports from older banks, <STMTTRN> blocks are almost always
# closed by </STMTTRN>, while leaf tags (<TRNTYPE>DEBIT\n<DTPOSTED>...) are unclosed.
# We also support unclosed <STMTTRN> delimited by the next <STMTTRN> or </BANKTRANLIST>.
_BANKTRANLIST_RE = re.compile(
    r"<BANKTRANLIST\b[^>]*>(?P<body>.*?)</BANKTRANLIST>",
    re.IGNORECASE | re.DOTALL,
)
_LEAF_TAG_RE = re.compile(
    r"<(?P<tag>[A-Z0-9_]+)>\s*(?P<val>[^<\r\n]+?)(?:\s*</(?P=tag)>|\s*(?=<|\r|\n|$))",
    re.IGNORECASE,
)
_CURRENCY_BLOCK_RE = re.compile(
    r"<(?P<tag>CURRENCY|ORIGCURRENCY)\b[^>]*>(?P<body>.*?)(?:</(?P=tag)>|(?=<STMTTRN|</STMTTRN))",
    re.IGNORECASE | re.DOTALL,
)

_EXPENSE_TRNTYPES = frozenset(
    {
        "DEBIT",
        "POS",
        "ATM",
        "PAYMENT",
        "CHECK",
        "FEE",
        "SRVCHG",
        "DIRECTDEBIT",
        "REPEATPMT",
        "CASH",
    }
)
_INCOME_TRNTYPES = frozenset(
    {
        "CREDIT",
        "DEP",
        "DIRECTDEP",
        "INT",
        "DIV",
    }
)


class OfxBillParser:
    """Zero-dependency OFX 1.x (SGML) & OFX 2.x (XML) / QFX bank & credit-card parser."""

    provider_id: ProviderId = "ofx"
    priority = 95

    def __init__(self, *, classify_xfer_as_transfer: bool = False) -> None:
        self._classify_xfer_as_transfer = classify_xfer_as_transfer

    def sniff(self, doc: ImportDocument) -> float:
        if doc.suffix in {".ofx", ".qfx"}:
            return 0.98
        text, _ = decode_text_bytes(doc.raw_bytes[:8192])
        if not text:
            return 0.0
        upper = text[:4096].upper()
        if "OFXHEADER:" in upper or ("<OFX>" in upper and "<STMTTRN>" in upper):
            return 0.96
        if "<?OFX" in upper:
            return 0.96
        return 0.0

    def parse(self, doc: ImportDocument, limits: ParserLimits) -> ImportParseResult:
        if doc.size_bytes > limits.max_file_bytes:
            return ImportParseResult(
                "ofx",
                "rejected",
                f"OFX 文件大小超过上限 ({limits.max_file_bytes} bytes)",
            )
        text, reason = decode_text_bytes(
            doc.raw_bytes,
            encodings=("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"),
        )
        if text is None:
            detail = "OFX 包含非法二进制字节" if reason == "binary_payload" else "无法解码 OFX 文本"
            return ImportParseResult("ofx", "rejected", detail)

        if _XXE_GUARD_RE.search(text[:65536]):
            return ImportParseResult("ofx", "rejected", "拒绝包含 DOCTYPE/ENTITY 声明的不安全 OFX")

        ofx_pos = re.search(r"<OFX\b", text, flags=re.IGNORECASE)
        if ofx_pos is None:
            return ImportParseResult("ofx", "rejected", "未找到 <OFX> 根节点")

        preamble = text[: ofx_pos.start()]
        body = text[ofx_pos.start() :]
        metadata = self._parse_ofx_header_metadata(preamble)

        stmt_blocks = list(_STMTRS_BLOCK_RE.finditer(body))
        if not stmt_blocks:
            # Fallback if outer STMTRS wrapper is unclosed SGML
            stmt_blocks_raw = [("STMTRS", body)]
        else:
            stmt_blocks_raw = [(m.group("kind").upper(), m.group("body")) for m in stmt_blocks]

        records: list[NormalizedBillRecord] = []
        warnings: list[str] = []

        try:
            for stmt_idx, (stmt_kind, stmt_body) in enumerate(stmt_blocks_raw, start=1):
                stmt_currency = self._extract_statement_currency(stmt_body, limits.default_currency)
                self._extract_statement_metadata(stmt_kind, stmt_body, metadata, stmt_idx)
                trn_chunks = self._extract_transaction_blocks(stmt_body)
                for trn_idx, trn_body in enumerate(trn_chunks, start=1):
                    if len(records) >= limits.max_rows:
                        raise ImportLimitError(f"OFX 交易条数超过上限 {limits.max_rows}")
                    ref = (
                        f"OFX!STMTTRN#{trn_idx}"
                        if len(stmt_blocks_raw) == 1
                        else f"OFX!S{stmt_idx}:STMTTRN#{trn_idx}"
                    )
                    records.append(
                        self._parse_stmttrn(
                            trn_body,
                            source_ref=ref,
                            statement_currency=stmt_currency,
                            limits=limits,
                        )
                    )
        except (FormatSecurityError, ImportLimitError) as exc:
            return ImportParseResult("ofx", "rejected", str(exc), metadata=metadata)

        if not records:
            return ImportParseResult(
                "ofx",
                "rejected",
                "OFX 文件中未找到 <STMTTRN> 交易明细",
                metadata=metadata,
            )

        return ImportParseResult(
            provider="ofx",
            status="parsed",
            message=format_parse_summary(records, provider_label="OFX"),
            records=records,
            metadata=metadata,
            warnings=warnings,
        )

    @staticmethod
    def _parse_ofx_header_metadata(preamble: str) -> dict[str, str]:
        meta: dict[str, str] = {}
        for line in preamble.splitlines():
            line = line.strip()
            if not line or line.startswith("<?") or ":" not in line:
                continue
            key, val = line.split(":", 1)
            key_clean = key.strip().upper()
            val_clean = val.strip()
            if key_clean in {"OFXHEADER", "VERSION", "CHARSET", "ENCODING"} and val_clean:
                meta[key_clean.lower()] = val_clean
        return meta

    @staticmethod
    def _extract_statement_currency(stmt_body: str, default_currency: str) -> str:
        # Match <CURDEF> before <BANKTRANLIST> so per-transaction tags never shadow it
        tranlist_match = re.search(r"<BANKTRANLIST\b", stmt_body, flags=re.IGNORECASE)
        header_slice = stmt_body[: tranlist_match.start()] if tranlist_match else stmt_body[:2048]
        cur_match = re.search(
            r"<CURDEF>\s*([A-Za-z]{3})\b",
            header_slice,
            flags=re.IGNORECASE,
        )
        if cur_match:
            return cur_match.group(1).upper()
        return default_currency.upper()

    @staticmethod
    def _extract_statement_metadata(
        stmt_kind: str,
        stmt_body: str,
        meta: dict[str, str],
        stmt_idx: int,
    ) -> None:
        prefix = "" if stmt_idx == 1 else f"stmt{stmt_idx}_"
        meta[f"{prefix}statement_type"] = "credit_card" if stmt_kind == "CCSTMTRS" else "bank"
        for tag, key in (
            ("BANKID", "bank_id"),
            ("ACCTID", "account_id"),
            ("ACCTTYPE", "account_type"),
            ("DTSTART", "period_start"),
            ("DTEND", "period_end"),
        ):
            match = re.search(
                rf"<{tag}>\s*([^<\r\n]+?)(?:\s*</{tag}>|\s*(?=<|\r|\n|$))",
                stmt_body[:4096],
                flags=re.IGNORECASE,
            )
            if match:
                value = html.unescape(match.group(1)).strip()
                if key == "account_id" and len(value) > 4:
                    # Mask full account numbers in metadata to avoid accidental PII echo
                    value = f"***{value[-4:]}"
                meta[f"{prefix}{key}"] = value

    @staticmethod
    def _extract_transaction_blocks(stmt_body: str) -> list[str]:
        closed = [m.group("body") for m in _STMTTRN_CLOSED_RE.finditer(stmt_body)]
        if closed:
            return closed
        tranlist = _BANKTRANLIST_RE.search(stmt_body)
        scope = tranlist.group("body") if tranlist else stmt_body
        parts = re.split(r"<STMTTRN\b[^>]*>", scope, flags=re.IGNORECASE)
        if len(parts) <= 1:
            return []
        return [part for part in parts[1:] if part.strip()]

    def _parse_stmttrn(
        self,
        trn_body: str,
        *,
        source_ref: str,
        statement_currency: str,
        limits: ParserLimits,
    ) -> NormalizedBillRecord:
        issues: list[str] = []
        warnings: list[str] = []

        fx_currency: str | None = None
        fx_rate: Decimal | None = None
        stripped_body = trn_body
        for cur_match in _CURRENCY_BLOCK_RE.finditer(trn_body):
            cur_tags = {
                m.group("tag").upper(): html.unescape(m.group("val")).strip()
                for m in _LEAF_TAG_RE.finditer(cur_match.group("body"))
            }
            sym, _ = parse_currency_code(cur_tags.get("CURSYM"), default_currency="")
            if sym and len(sym) == 3:
                fx_currency = sym
            rate_str = cur_tags.get("CURRATE")
            if rate_str:
                try:
                    candidate_rate = Decimal(rate_str.strip())
                    if candidate_rate > 0 and candidate_rate.is_finite():
                        fx_rate = candidate_rate
                except InvalidOperation:
                    warnings.append("invalid_ofx_currate_ignored")
        # Remove nested <CURRENCY>/<ORIGCURRENCY> blocks before extracting top-level tags
        stripped_body = _CURRENCY_BLOCK_RE.sub("", stripped_body)

        fields: dict[str, str | None] = {}
        for match in _LEAF_TAG_RE.finditer(stripped_body):
            tag = match.group("tag").upper()
            val = clean_cell_text(html.unescape(match.group("val")))
            if tag not in fields and val is not None:
                fields[tag] = val

        trntype = (fields.get("TRNTYPE") or "").upper()
        dt_raw = fields.get("DTUSER") or fields.get("DTPOSTED") or fields.get("DTAVAIL")
        occurred_at = parse_ofx_datetime(dt_raw, default_tz=limits.default_timezone)
        if occurred_at is None:
            issues.append("missing_or_invalid_date")

        fitid = clean_external_id(fields.get("FITID"))
        refnum = clean_external_id(fields.get("REFNUM") or fields.get("CHECKNUM"))
        if not fitid:
            warnings.append("missing_ofx_fitid")

        name = fields.get("NAME") or fields.get("PAYEE")
        memo = fields.get("MEMO")
        payee = name or memo
        if not payee:
            issues.append("missing_payee")
        elif len(payee) > 300:
            payee = payee[:300]
            issues.append("payee_too_long")
        item_summary = memo if memo and memo != payee else None

        signed_minor: int | None = None
        try:
            signed_minor = parse_minor_amount(fields.get("TRNAMT"))
        except MinorAmountError as exc:
            issues.append(exc.issue)

        amount_minor = abs(signed_minor) if signed_minor is not None else None
        direction: NormalizedDirection | None = None

        combined_text = f"{payee or ''} {memo or ''}".casefold()
        if signed_minor == 0:
            direction = "ignored"
            issues.append("zero_amount_transaction")
        elif trntype == "XFER" and self._classify_xfer_as_transfer:
            direction = "transfer"
            issues.append("neutral_transfer_requires_counter_account")
        elif "refund" in combined_text or "退款" in combined_text:
            if signed_minor is not None and signed_minor > 0:
                direction = "refund"
                issues.append("refund_requires_original_transaction")
            elif signed_minor is not None and signed_minor < 0:
                direction = "expense"
        elif signed_minor is not None:
            if trntype == "XFER":
                warnings.append("ofx_trntype_xfer")
            if signed_minor < 0:
                direction = "expense"
                if trntype in _INCOME_TRNTYPES:
                    warnings.append("ofx_sign_overrides_trntype")
            elif signed_minor > 0:
                direction = "income"
                if trntype in _EXPENSE_TRNTYPES:
                    warnings.append("ofx_sign_overrides_trntype")
        else:
            if not any(
                err in issues
                for err in (
                    "amount_exceeds_safe_range",
                    "ambiguous_xlsx_float_amount",
                    "amount_has_more_than_two_decimal_places",
                )
            ):
                issues.append("missing_or_invalid_amount")
            if trntype in _EXPENSE_TRNTYPES:
                direction = "expense"
            elif trntype in _INCOME_TRNTYPES:
                direction = "income"
            else:
                issues.append("missing_or_ambiguous_direction")

        # Note on OFX <CURRENCY> vs <ORIGCURRENCY>:
        # In OFX, <TRNAMT> is expressed in <CURDEF> unless <CURRENCY> specifies the
        # transaction currency; keep statement_currency as authoritative for TRNAMT unless
        # statement_currency was missing, and surface foreign_currency metadata in raw_payload.
        currency = statement_currency
        if fx_currency and fx_currency != statement_currency:
            fields["FX_CURSYM"] = fx_currency
            if fx_rate is not None:
                fields["FX_CURRATE"] = str(fx_rate)

        confidence = Decimal("0.99")
        if not fitid:
            confidence -= Decimal("0.04")
        if warnings:
            confidence -= Decimal("0.03")
        if issues:
            confidence = max(Decimal("0.15"), confidence - Decimal("0.25"))

        return NormalizedBillRecord(
            provider="ofx",
            source_ref=source_ref,
            raw_payload=fields,
            source_hash=compute_source_hash("ofx", fields),
            fingerprint=compute_semantic_fingerprint(
                occurred_at=occurred_at,
                direction=direction,
                amount_minor=amount_minor,
                currency=currency,
                payee=payee,
                default_tz=limits.default_timezone,
            ),
            external_id=fitid or refnum,
            merchant_order_id=refnum if fitid and refnum != fitid else None,
            occurred_at=occurred_at,
            payee=payee,
            canonical_payee=canonicalize_payee(payee),
            item_summary=item_summary,
            direction=direction,
            amount_minor=amount_minor,
            currency=currency,
            fx_rate=fx_rate if currency != limits.default_currency else None,
            original_category=fields.get("SIC") or trntype or None,
            confidence=confidence,
            issues=tuple(dict.fromkeys(issues)),
            warnings=tuple(dict.fromkeys(warnings)),
        )
