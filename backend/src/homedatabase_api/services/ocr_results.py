from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..money import MAX_SAFE_MINOR
from ..ocr_contract import OCR_CONTRACT_VERSION
from .import_parsers import ParsedRow, ParseOutcome

_AMOUNT_RE = re.compile(r"^[0-9]{1,14}(?:\.[0-9]{1,2})?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MAX_RESULT_BYTES = 2 * 1024 * 1024


class OcrResultError(ValueError):
    pass


class _OcrTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    occurred_at: datetime = Field(alias="occurredAt")
    payee: str = Field(min_length=1, max_length=300)
    direction: Literal["expense", "income"]
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)
    category: str | None = Field(default=None, max_length=160)
    source_ref: str = Field(alias="sourceRef", min_length=1, max_length=300)
    confidence: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4)

    @field_validator("payee", "source_ref")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("amount")
    @classmethod
    def exact_amount_text(cls, value: str) -> str:
        if not _AMOUNT_RE.fullmatch(value):
            raise ValueError("amount must be a positive decimal string with at most two decimals")
        return value

    @field_validator("currency")
    @classmethod
    def iso_currency(cls, value: str) -> str:
        if not _CURRENCY_RE.fullmatch(value):
            raise ValueError("currency must be a three-letter uppercase code")
        return value

    @field_validator("category")
    @classmethod
    def clean_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class _OcrOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactions: list[_OcrTransaction] = Field(max_length=500)
    warnings: list[str] = Field(max_length=100)

    @field_validator("warnings")
    @classmethod
    def bounded_warnings(cls, values: list[str]) -> list[str]:
        if any(len(value) > 300 for value in values):
            raise ValueError("warning is too long")
        return values


def parse_ocr_result(path: Path, *, max_transactions: int) -> ParseOutcome:
    try:
        if path.stat().st_size > _MAX_RESULT_BYTES:
            raise OcrResultError("OCR 结果超过安全大小限制")
        payload = json.loads(path.read_text(encoding="utf-8"))
        output = _OcrOutput.model_validate(payload)
    except OcrResultError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise OcrResultError("OCR 返回的数据格式无效") from exc
    if len(output.transactions) > max_transactions:
        raise OcrResultError("OCR 返回的交易条数超过批次限制")

    rows: list[ParsedRow] = []
    for transaction in output.transactions:
        raw_payload = transaction.model_dump(mode="json", by_alias=True)
        issues: list[str] = []
        try:
            amount_minor_decimal = Decimal(transaction.amount) * 100
            amount_minor = int(amount_minor_decimal)
        except (InvalidOperation, ValueError, OverflowError):
            amount_minor = 0
            issues.append("invalid_amount")
        if amount_minor < 1:
            issues.append("invalid_amount")
        elif amount_minor > MAX_SAFE_MINOR:
            issues.append("amount_exceeds_safe_range")
        canonical = json.dumps(
            {"contractVersion": OCR_CONTRACT_VERSION, "fields": raw_payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        rows.append(
            ParsedRow(
                source_ref=transaction.source_ref,
                raw_payload=raw_payload,
                source_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                occurred_at=transaction.occurred_at,
                payee=transaction.payee,
                direction=transaction.direction,
                amount_minor=amount_minor,
                currency=transaction.currency,
                original_category=transaction.category,
                confidence=transaction.confidence,
                issues=list(dict.fromkeys(issues)),
            )
        )

    valid = sum(row.can_create_candidate for row in rows)
    rejected = len(rows) - valid
    message = f"Codex OCR 识别 {len(rows)} 条，生成 {valid} 条候选"
    if rejected:
        message += f"；{rejected} 条未通过后端校验"
    if output.warnings:
        message += f"；模型报告 {len(output.warnings)} 项识别警告"
    return ParseOutcome(
        status="parsed",
        message=message,
        rows=rows,
        warning_count=len(output.warnings),
    )
