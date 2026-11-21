from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

MAX_SAFE_MINOR: Final[int] = 2**53 - 1
SHANGHAI_TZ: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

ProviderId = Literal[
    "wechat",
    "alipay",
    "csv",
    "xlsx",
    "ofx",
    "ocr_passthrough",
    "unknown",
]
ParseStatus = Literal["parsed", "needs_ocr", "rejected"]
NormalizedDirection = Literal["expense", "income", "transfer", "refund", "ignored"]
LedgerDirection = Literal["expense", "income"]
DedupStatus = Literal[
    "unique",
    "exact_file_duplicate",
    "external_id_duplicate",
    "duplicate_source_row",
    "batch_internal_duplicate",
    "exact_ledger_duplicate",
    "near_ledger_duplicate",
]


class MinorAmountError(ValueError):
    """Raised when an amount cell violates minor-unit precision or safety invariants."""

    def __init__(self, issue: str) -> None:
        super().__init__(issue)
        self.issue = issue


class ImportLimitError(ValueError):
    """Raised when a statement exceeds row, byte, or decompression safety limits."""


class FormatSecurityError(ValueError):
    """Raised when an input file contains unsafe constructs (e.g. zip bomb or XXE)."""


@dataclass(slots=True, frozen=True)
class ParserLimits:
    max_rows: int = 50_000
    max_file_bytes: int = 25 * 1024 * 1024
    max_uncompressed_bytes: int = 64 * 1024 * 1024
    default_currency: str = "CNY"
    default_timezone: ZoneInfo = SHANGHAI_TZ


@dataclass(slots=True, frozen=True)
class ImportDocument:
    filename: str
    raw_bytes: bytes
    source_hint: str | None = None
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        digest = hashlib.sha256(self.raw_bytes).hexdigest()
        object.__setattr__(self, "sha256", digest)
        safe_name = Path(self.filename.replace("\\", "/")).name or "upload.bin"
        object.__setattr__(self, "filename", safe_name)

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.casefold()

    @property
    def size_bytes(self) -> int:
        return len(self.raw_bytes)

    @classmethod
    def from_path(
        cls,
        path: Path,
        filename: str | None = None,
        *,
        max_file_bytes: int | None = None,
        source_hint: str | None = None,
    ) -> ImportDocument:
        if max_file_bytes is not None and path.stat().st_size > max_file_bytes:
            raise ImportLimitError(f"文件大小超过上限 ({max_file_bytes} bytes)")
        raw = path.read_bytes()
        if max_file_bytes is not None and len(raw) > max_file_bytes:
            raise ImportLimitError(f"文件大小超过上限 ({max_file_bytes} bytes)")
        return cls(
            filename=filename or path.name,
            raw_bytes=raw,
            source_hint=source_hint,
        )

    @classmethod
    def from_text(
        cls,
        text: str,
        filename: str,
        *,
        encoding: str = "utf-8",
        source_hint: str | None = None,
    ) -> ImportDocument:
        return cls(
            filename=filename,
            raw_bytes=text.encode(encoding),
            source_hint=source_hint,
        )


@dataclass(slots=True, frozen=True)
class NormalizedBillRecord:
    provider: ProviderId
    source_ref: str
    raw_payload: dict[str, str | None]
    source_hash: str
    fingerprint: str
    occurred_at: datetime | None
    payee: str | None
    canonical_payee: str
    direction: NormalizedDirection | None
    amount_minor: int | None
    currency: str
    external_id: str | None = None
    merchant_order_id: str | None = None
    item_summary: str | None = None
    fx_rate: Decimal | None = None
    original_category: str | None = None
    suggested_category_name: str | None = None
    suggested_category_id: str | None = None
    payment_method: str | None = None
    tx_status: str | None = None
    confidence: Decimal = Decimal("0.99")
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def occurred_at_utc(self) -> datetime | None:
        if self.occurred_at is None:
            return None
        if self.occurred_at.tzinfo is None:
            return self.occurred_at.replace(tzinfo=SHANGHAI_TZ).astimezone(UTC)
        return self.occurred_at.astimezone(UTC)

    @property
    def is_ledger_candidate(self) -> bool:
        """True iff this row can be posted directly as a V1 income/expense candidate."""
        return (
            not self.issues
            and self.occurred_at is not None
            and bool(self.payee)
            and self.direction in {"expense", "income"}
            and self.amount_minor is not None
            and 0 < self.amount_minor <= MAX_SAFE_MINOR
        )

    @property
    def is_ignored(self) -> bool:
        return self.direction == "ignored"

    @property
    def is_non_posting_financial_event(self) -> bool:
        """True for valid transfers/refunds held out of ordinary income/expense posting."""
        return self.direction in {"transfer", "refund"}

    def with_category(
        self,
        *,
        category_name: str | None,
        category_id: str | None = None,
    ) -> NormalizedBillRecord:
        return replace(
            self,
            suggested_category_name=category_name,
            suggested_category_id=category_id,
        )


@dataclass(slots=True)
class ImportParseResult:
    provider: ProviderId
    status: ParseStatus
    message: str
    records: list[NormalizedBillRecord] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def candidate_count(self) -> int:
        return sum(record.is_ledger_candidate for record in self.records)

    @property
    def transfer_count(self) -> int:
        return sum(record.direction == "transfer" for record in self.records)

    @property
    def refund_count(self) -> int:
        return sum(record.direction == "refund" for record in self.records)

    @property
    def ignored_count(self) -> int:
        return sum(record.direction == "ignored" for record in self.records)

    @property
    def rejected_count(self) -> int:
        return sum(not record.is_ledger_candidate for record in self.records)


@runtime_checkable
class BillParserPlugin(Protocol):
    provider_id: ProviderId
    priority: int

    def sniff(self, doc: ImportDocument) -> float:
        """Return a confidence score in [0.0, 1.0] indicating format match."""
        ...

    def parse(self, doc: ImportDocument, limits: ParserLimits) -> ImportParseResult:
        """Parse and normalize a bill document within explicit safety limits."""
        ...
