from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from .models import ImportParseResult, NormalizedBillRecord, ParserLimits, ParseStatus
from .registry import ParserRegistry, create_default_registry


@dataclass(slots=True)
class LegacyParsedRow:
    """Standalone fallback matching homedatabase_api.services.import_parsers.ParsedRow."""

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
class LegacyParseOutcome:
    """Standalone fallback matching homedatabase_api.services.import_parsers.ParseOutcome."""

    status: ParseStatus
    message: str
    rows: list[LegacyParsedRow] = field(default_factory=list)
    warning_count: int = 0

    @property
    def candidate_count(self) -> int:
        return sum(row.can_create_candidate for row in self.rows)

    @property
    def rejected_count(self) -> int:
        return sum(not row.can_create_candidate for row in self.rows)


def _resolve_legacy_types() -> tuple[type, type]:
    try:
        from homedatabase_api.services.import_parsers import ParsedRow, ParseOutcome

        return ParsedRow, ParseOutcome
    except ImportError:
        return LegacyParsedRow, LegacyParseOutcome


def to_legacy_parsed_row(record: NormalizedBillRecord):
    row_cls, _ = _resolve_legacy_types()
    legacy_direction: Literal["expense", "income"] | None = (
        record.direction if record.direction in {"expense", "income"} else None
    )
    effective_category = record.suggested_category_name or record.original_category
    return row_cls(
        source_ref=record.source_ref,
        raw_payload=dict(record.raw_payload),
        source_hash=record.source_hash,
        occurred_at=record.occurred_at,
        payee=record.payee,
        direction=legacy_direction,
        amount_minor=record.amount_minor,
        currency=record.currency,
        original_category=effective_category,
        confidence=record.confidence,
        issues=list(record.issues),
    )


def to_legacy_parse_outcome(result: ImportParseResult):
    _, outcome_cls = _resolve_legacy_types()
    rows = [to_legacy_parsed_row(rec) for rec in result.records]
    warning_count = len(result.warnings) + sum(len(rec.warnings) for rec in result.records)
    return outcome_cls(
        status=result.status,
        message=result.message,
        rows=rows,
        warning_count=warning_count,
    )


def parse_import_file_pluggable(
    path: Path,
    filename: str,
    *,
    max_rows: int,
    max_uncompressed_bytes: int,
    registry: ParserRegistry | None = None,
    source_hint: str | None = None,
):
    """Drop-in replacement for homedatabase_api.services.import_parsers.parse_import_file."""
    active_registry = registry or create_default_registry()
    limits = ParserLimits(
        max_rows=max_rows,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    result = active_registry.parse_path(
        path,
        filename=filename,
        limits=limits,
        source_hint=source_hint,
    )
    return to_legacy_parse_outcome(result)
