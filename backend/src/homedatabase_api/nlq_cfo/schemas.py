"""Strict Pydantic schemas for read-only Layer-3 'Ask Your Money' NLQ and digests."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..money import NonNegativeMinor, SignedMinor

ScopeFilter = Literal["all_visible", "family_only", "personal_only"]
TransactionTypeFilter = Literal["expense", "income", "transfer", "refund"]
GroupByDimension = Literal["category", "month", "account", "payee"]
QueryIntent = Literal[
    "spending_summary",
    "income_summary",
    "cashflow_overview",
    "category_breakdown",
    "trend_comparison",
    "refund_analysis",
    "split_analysis",
    "large_expenses",
    "transaction_search",
]
DriverKind = Literal[
    "frequency",
    "ticket_size",
    "both",
    "flat",
    "insufficient_baseline",
]


class CfoModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class AskQuestionInput(CfoModel):
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]
    as_of_date: date | None = Field(default=None, alias="asOfDate")
    scope: ScopeFilter | None = None
    include_comparison: bool | None = Field(default=None, alias="includeComparison")
    max_citations: int = Field(default=25, alias="maxCitations", ge=1, le=200)


class DigestQueryInput(CfoModel):
    month: Annotated[str | None, StringConstraints(pattern=r"^\d{4}-\d{2}$")] = None
    starts_on: date | None = Field(default=None, alias="startsOn")
    ends_on: date | None = Field(default=None, alias="endsOn")
    as_of_date: date | None = Field(default=None, alias="asOfDate")
    scope: ScopeFilter = "all_visible"
    max_highlights: int = Field(default=5, alias="maxHighlights", ge=1, le=20)
    max_citations: int = Field(default=30, alias="maxCitations", ge=1, le=200)

    @model_validator(mode="after")
    def validate_date_window(self) -> DigestQueryInput:
        if self.month and (self.starts_on is not None or self.ends_on is not None):
            raise ValueError("month 与 startsOn/endsOn 不能同时指定")
        if (self.starts_on is None) != (self.ends_on is None):
            raise ValueError("startsOn 与 endsOn 必须成对提供")
        if self.starts_on is not None and self.ends_on is not None:
            if self.starts_on > self.ends_on:
                raise ValueError("开始日期不能晚于结束日期")
            if (self.ends_on - self.starts_on).days > 366:
                raise ValueError("单次简报跨度不能超过 366 天")
        return self


class LedgerQueryPlan(CfoModel):
    """Bounded, read-only structured query plan produced by a planner adapter."""

    intent: QueryIntent = "spending_summary"
    date_from: date = Field(alias="dateFrom")
    date_to: date = Field(alias="dateTo")
    comparison_date_from: date | None = Field(default=None, alias="comparisonDateFrom")
    comparison_date_to: date | None = Field(default=None, alias="comparisonDateTo")
    types: list[TransactionTypeFilter] = Field(default_factory=lambda: ["expense", "refund"])
    category_ids: list[str] = Field(default_factory=list, alias="categoryIds", max_length=32)
    account_ids: list[str] = Field(default_factory=list, alias="accountIds", max_length=32)
    scope: ScopeFilter = "all_visible"
    keyword: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=100)] = None
    min_amount_cents: NonNegativeMinor | None = Field(default=None, alias="minAmountCents")
    max_amount_cents: NonNegativeMinor | None = Field(default=None, alias="maxAmountCents")
    only_with_splits: bool = Field(default=False, alias="onlyWithSplits")
    only_refunded: bool = Field(default=False, alias="onlyRefunded")
    group_by: GroupByDimension = Field(default="category", alias="groupBy")
    limit: int = Field(default=200, ge=1, le=500)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bounds(self) -> LedgerQueryPlan:
        if self.date_from > self.date_to:
            raise ValueError("query plan dateFrom cannot be after dateTo")
        if (self.date_to - self.date_from).days > 1096:
            raise ValueError("query plan range cannot exceed 3 years")
        if (self.comparison_date_from is None) != (self.comparison_date_to is None):
            raise ValueError("comparisonDateFrom and comparisonDateTo must be paired")
        if (
            self.comparison_date_from is not None
            and self.comparison_date_to is not None
            and self.comparison_date_from > self.comparison_date_to
        ):
            raise ValueError("comparisonDateFrom cannot be after comparisonDateTo")
        if (
            self.min_amount_cents is not None
            and self.max_amount_cents is not None
            and self.min_amount_cents > self.max_amount_cents
        ):
            raise ValueError("minAmountCents cannot exceed maxAmountCents")
        return self


class LedgerTotals(CfoModel):
    base_currency: str = Field(default="CNY", alias="baseCurrency")
    gross_expense_cents: NonNegativeMinor = Field(default=0, alias="grossExpenseCents")
    refund_cents: NonNegativeMinor = Field(default=0, alias="refundCents")
    net_expense_cents: SignedMinor = Field(default=0, alias="netExpenseCents")
    income_cents: NonNegativeMinor = Field(default=0, alias="incomeCents")
    transfer_cents: NonNegativeMinor = Field(default=0, alias="transferCents")
    net_cashflow_cents: SignedMinor = Field(default=0, alias="netCashflowCents")
    transaction_count: int = Field(default=0, alias="transactionCount", ge=0)
    expense_count: int = Field(default=0, alias="expenseCount", ge=0)
    average_expense_cents: NonNegativeMinor = Field(default=0, alias="averageExpenseCents")


class PeriodComparison(CfoModel):
    previous_date_from: date = Field(alias="previousDateFrom")
    previous_date_to: date = Field(alias="previousDateTo")
    previous_totals: LedgerTotals = Field(alias="previousTotals")
    net_expense_delta_cents: SignedMinor = Field(alias="netExpenseDeltaCents")
    net_expense_change_pct: float | None = Field(default=None, alias="netExpenseChangePct")
    expense_count_delta: int = Field(alias="expenseCountDelta")
    average_expense_delta_cents: SignedMinor = Field(alias="averageExpenseDeltaCents")
    primary_driver: DriverKind = Field(alias="primaryDriver")


class BreakdownItem(CfoModel):
    key: str
    label: str
    gross_expense_cents: NonNegativeMinor = Field(default=0, alias="grossExpenseCents")
    refund_cents: NonNegativeMinor = Field(default=0, alias="refundCents")
    net_expense_cents: SignedMinor = Field(default=0, alias="netExpenseCents")
    income_cents: NonNegativeMinor = Field(default=0, alias="incomeCents")
    transaction_count: int = Field(default=0, alias="transactionCount", ge=0)
    average_expense_cents: NonNegativeMinor = Field(default=0, alias="averageExpenseCents")
    share_pct: float | None = Field(default=None, alias="sharePct")
    cited_transaction_ids: list[str] = Field(default_factory=list, alias="citedTransactionIds")


class TransactionCitation(CfoModel):
    transaction_id: str = Field(alias="transactionId")
    occurred_at: datetime = Field(alias="occurredAt")
    type: TransactionTypeFilter
    payee: str
    note: str = ""
    category_id: str | None = Field(default=None, alias="categoryId")
    category_name: str = Field(alias="categoryName")
    account_ids: list[str] = Field(default_factory=list, alias="accountIds")
    account_names: list[str] = Field(default_factory=list, alias="accountNames")
    amount_cents: NonNegativeMinor = Field(alias="amountCents")
    currency: str
    base_amount_cents: NonNegativeMinor = Field(alias="baseAmountCents")
    base_currency: str = Field(default="CNY", alias="baseCurrency")
    refund_of_id: str | None = Field(default=None, alias="refundOfId")
    split_id: str | None = Field(default=None, alias="splitId")
    split_status: str | None = Field(default=None, alias="splitStatus")
    member_share_cents: NonNegativeMinor | None = Field(default=None, alias="memberShareCents")
    redacted: bool = False


class RefundDigestSummary(CfoModel):
    refund_count: int = Field(default=0, alias="refundCount", ge=0)
    refund_total_cents: NonNegativeMinor = Field(default=0, alias="refundTotalCents")
    cited_transaction_ids: list[str] = Field(default_factory=list, alias="citedTransactionIds")


class OpenSplitsSummary(CfoModel):
    open_split_count: int = Field(default=0, alias="openSplitCount", ge=0)
    pending_member_share_cents: NonNegativeMinor = Field(default=0, alias="pendingMemberShareCents")
    cited_transaction_ids: list[str] = Field(default_factory=list, alias="citedTransactionIds")


class AskYourMoneyResponse(CfoModel):
    question: str
    answer: str
    adapter_name: str = Field(alias="adapterName")
    read_only: Literal[True] = Field(default=True, alias="readOnly")
    timezone: str = "Asia/Shanghai"
    query_plan: LedgerQueryPlan = Field(alias="queryPlan")
    assumptions: list[str] = Field(default_factory=list)
    totals: LedgerTotals
    comparison: PeriodComparison | None = None
    breakdowns: list[BreakdownItem] = Field(default_factory=list)
    citations: list[TransactionCitation] = Field(default_factory=list)
    cited_transaction_ids: list[str] = Field(default_factory=list, alias="citedTransactionIds")
    total_matched_count: int = Field(default=0, alias="totalMatchedCount", ge=0)
    truncated_citations: bool = Field(default=False, alias="truncatedCitations")
    warnings: list[str] = Field(default_factory=list)


class HouseholdDigestResponse(CfoModel):
    period_label: str = Field(alias="periodLabel")
    date_from: date = Field(alias="dateFrom")
    date_to: date = Field(alias="dateTo")
    scope: ScopeFilter
    summary: str
    adapter_name: str = Field(alias="adapterName")
    read_only: Literal[True] = Field(default=True, alias="readOnly")
    timezone: str = "Asia/Shanghai"
    totals: LedgerTotals
    comparison: PeriodComparison | None = None
    top_categories: list[BreakdownItem] = Field(default_factory=list, alias="topCategories")
    largest_expenses: list[TransactionCitation] = Field(
        default_factory=list, alias="largestExpenses"
    )
    refund_summary: RefundDigestSummary = Field(alias="refundSummary")
    open_splits_summary: OpenSplitsSummary = Field(alias="openSplitsSummary")
    citations: list[TransactionCitation] = Field(default_factory=list)
    cited_transaction_ids: list[str] = Field(default_factory=list, alias="citedTransactionIds")
    warnings: list[str] = Field(default_factory=list)


class CfoCapabilitiesResponse(CfoModel):
    read_only: Literal[True] = Field(default=True, alias="readOnly")
    adapter_name: str = Field(alias="adapterName")
    base_currency: str = Field(default="CNY", alias="baseCurrency")
    timezone: str = "Asia/Shanghai"
    supported_scopes: list[ScopeFilter] = Field(
        default_factory=lambda: ["all_visible", "family_only", "personal_only"],
        alias="supportedScopes",
    )
    supported_group_by: list[GroupByDimension] = Field(
        default_factory=lambda: ["category", "month", "account", "payee"],
        alias="supportedGroupBy",
    )
    privacy_guarantees: list[str] = Field(default_factory=list, alias="privacyGuarantees")
