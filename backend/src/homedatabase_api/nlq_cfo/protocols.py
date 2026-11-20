"""Narrow protocols and immutable domain records for decoupled NLQ CFO components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from .schemas import (
    BreakdownItem,
    LedgerQueryPlan,
    LedgerTotals,
    OpenSplitsSummary,
    PeriodComparison,
    RefundDigestSummary,
    ScopeFilter,
    TransactionCitation,
)


@dataclass(frozen=True)
class VisibleAccountInfo:
    """Authorized account metadata visible to the requesting member."""

    id: str
    name: str
    kind: str
    scope: str
    currency: str
    permission: str


@dataclass(frozen=True)
class CategoryInfo:
    """Category catalog metadata."""

    id: str
    name: str
    kind: str
    parent_id: str | None = None
    active: bool = True


@dataclass(frozen=True)
class CatalogContext:
    """Minimal non-sensitive metadata given to a query planner adapter."""

    member_id: str
    member_name: str
    base_currency: str
    accounts: tuple[VisibleAccountInfo, ...]
    categories: tuple[CategoryInfo, ...]


@dataclass(frozen=True)
class VisibleTransactionRecord:
    """Permission-filtered, privacy-redacted read-only transaction projection."""

    id: str
    occurred_at: datetime
    type: str
    payee: str
    note: str
    category_id: str | None
    category_name: str
    account_ids: tuple[str, ...]
    account_names: tuple[str, ...]
    account_scopes: tuple[str, ...]
    amount_minor: int
    currency: str
    base_amount_minor: int
    base_currency: str
    refund_of_id: str | None = None
    split_id: str | None = None
    split_status: str | None = None
    member_share_minor: int | None = None
    member_share_status: str | None = None
    redacted: bool = False


@dataclass(frozen=True)
class NarrativeDraft:
    """Textual explanation produced by a narrator adapter, validated by service."""

    text: str
    cited_transaction_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AskNarrativeContext:
    """Deterministic, permission-filtered aggregates passed to narrator adapters."""

    question: str
    query_plan: LedgerQueryPlan
    totals: LedgerTotals
    comparison: PeriodComparison | None
    breakdowns: tuple[BreakdownItem, ...]
    citations: tuple[TransactionCitation, ...]
    total_matched_count: int
    assumptions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DigestNarrativeContext:
    """Deterministic household digest facts passed to narrator adapters."""

    period_label: str
    date_from: date
    date_to: date
    scope: ScopeFilter
    totals: LedgerTotals
    comparison: PeriodComparison | None
    top_categories: tuple[BreakdownItem, ...]
    largest_expenses: tuple[TransactionCitation, ...]
    refund_summary: RefundDigestSummary
    open_splits_summary: OpenSplitsSummary


@runtime_checkable
class LedgerReadRepository(Protocol):
    """Strictly read-only port over the household ledger."""

    def load_catalog_context(self, member_id: str) -> CatalogContext: ...

    def query_visible_transactions(
        self,
        member_id: str,
        *,
        date_from: date,
        date_to: date,
        types: tuple[str, ...] = (),
        category_ids: tuple[str, ...] = (),
        account_ids: tuple[str, ...] = (),
        scope: ScopeFilter = "all_visible",
        keyword: str | None = None,
        min_amount_minor: int | None = None,
        max_amount_minor: int | None = None,
        only_with_splits: bool = False,
        only_refunded: bool = False,
        limit: int = 500,
    ) -> list[VisibleTransactionRecord]: ...


@runtime_checkable
class LLMTransport(Protocol):
    """Provider-agnostic structured JSON completion transport for pluggable LLMs."""

    @property
    def provider_name(self) -> str: ...

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class CFOAdapter(Protocol):
    """Pluggable adapter decoupling NLQ planning & narration from ledger execution."""

    @property
    def adapter_name(self) -> str: ...

    def plan_query(
        self,
        *,
        question: str,
        context: CatalogContext,
        as_of_date: date,
        scope_override: ScopeFilter | None = None,
        include_comparison: bool | None = None,
    ) -> tuple[LedgerQueryPlan, list[str]]: ...

    def narrate_ask(self, context: AskNarrativeContext) -> NarrativeDraft: ...

    def narrate_digest(self, context: DigestNarrativeContext) -> NarrativeDraft: ...
