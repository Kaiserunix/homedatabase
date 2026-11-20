"""Deterministic read-only Ask Your Money & Household Digest application service."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from ..money import require_safe_signed_minor
from .adapters import DeterministicOfflineCFOAdapter
from .parser import month_bounds, previous_equal_window
from .protocols import (
    AskNarrativeContext,
    CFOAdapter,
    DigestNarrativeContext,
    LedgerReadRepository,
    VisibleTransactionRecord,
)
from .schemas import (
    AskQuestionInput,
    AskYourMoneyResponse,
    BreakdownItem,
    CfoCapabilitiesResponse,
    DigestQueryInput,
    DriverKind,
    GroupByDimension,
    HouseholdDigestResponse,
    LedgerTotals,
    OpenSplitsSummary,
    PeriodComparison,
    RefundDigestSummary,
    TransactionCitation,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _today_shanghai() -> date:
    return datetime.now(UTC).astimezone(SHANGHAI).date()


def _integer_avg_minor(total_minor: int, count: int) -> int:
    if count <= 0 or total_minor <= 0:
        return 0
    return (total_minor + count // 2) // count


def _pct_change(delta: int, baseline: int) -> float | None:
    if baseline == 0:
        return None
    return round((delta / abs(baseline)) * 100.0, 2)


def _compute_totals(
    records: list[VisibleTransactionRecord],
    *,
    base_currency: str = "CNY",
) -> LedgerTotals:
    gross_expense = sum(r.base_amount_minor for r in records if r.type == "expense")
    refunds = sum(r.base_amount_minor for r in records if r.type == "refund")
    income = sum(r.base_amount_minor for r in records if r.type == "income")
    transfers = sum(r.base_amount_minor for r in records if r.type == "transfer")
    expense_count = sum(1 for r in records if r.type == "expense")

    net_expense = require_safe_signed_minor(gross_expense - refunds)
    net_cashflow = require_safe_signed_minor(income - net_expense)
    avg_expense = _integer_avg_minor(gross_expense, expense_count)

    return LedgerTotals(
        baseCurrency=base_currency,
        grossExpenseCents=gross_expense,
        refundCents=refunds,
        netExpenseCents=net_expense,
        incomeCents=income,
        transferCents=transfers,
        netCashflowCents=net_cashflow,
        transactionCount=len(records),
        expenseCount=expense_count,
        averageExpenseCents=avg_expense,
    )


def _classify_driver(current: LedgerTotals, previous: LedgerTotals) -> DriverKind:
    if previous.expense_count == 0 or previous.gross_expense_cents == 0:
        return "insufficient_baseline"
    net_delta = current.net_expense_cents - previous.net_expense_cents
    if net_delta == 0:
        return "flat"

    count_ratio = abs(current.expense_count - previous.expense_count) / previous.expense_count
    ticket_ratio = (
        abs(current.average_expense_cents - previous.average_expense_cents)
        / previous.average_expense_cents
        if previous.average_expense_cents > 0
        else 0.0
    )
    if count_ratio == 0 and ticket_ratio == 0:
        return "flat"
    if count_ratio > ticket_ratio * 1.5:
        return "frequency"
    if ticket_ratio > count_ratio * 1.5:
        return "ticket_size"
    return "both"


def _compute_comparison(
    *,
    current: LedgerTotals,
    previous: LedgerTotals,
    previous_from: date,
    previous_to: date,
) -> PeriodComparison:
    net_delta = require_safe_signed_minor(current.net_expense_cents - previous.net_expense_cents)
    avg_delta = require_safe_signed_minor(
        current.average_expense_cents - previous.average_expense_cents
    )
    return PeriodComparison(
        previousDateFrom=previous_from,
        previousDateTo=previous_to,
        previousTotals=previous,
        netExpenseDeltaCents=net_delta,
        netExpenseChangePct=_pct_change(net_delta, previous.net_expense_cents),
        expenseCountDelta=current.expense_count - previous.expense_count,
        averageExpenseDeltaCents=avg_delta,
        primaryDriver=_classify_driver(current, previous),
    )


def _to_citation(rec: VisibleTransactionRecord) -> TransactionCitation:
    return TransactionCitation(
        transactionId=rec.id,
        occurredAt=rec.occurred_at,
        type=rec.type,  # type: ignore[arg-type]
        payee=rec.payee,
        note=rec.note,
        categoryId=rec.category_id,
        categoryName=rec.category_name,
        accountIds=list(rec.account_ids),
        accountNames=list(rec.account_names),
        amountCents=rec.amount_minor,
        currency=rec.currency,
        baseAmountCents=rec.base_amount_minor,
        baseCurrency=rec.base_currency,
        refundOfId=rec.refund_of_id,
        splitId=rec.split_id,
        splitStatus=rec.split_status,
        memberShareCents=rec.member_share_minor,
        redacted=rec.redacted,
    )


def _compute_breakdowns(
    records: list[VisibleTransactionRecord],
    *,
    group_by: GroupByDimension,
    is_income_view: bool = False,
) -> list[BreakdownItem]:
    buckets: dict[str, list[VisibleTransactionRecord]] = defaultdict(list)
    labels: dict[str, str] = {}

    for rec in records:
        if group_by == "category":
            key = rec.category_id or "uncategorized"
            label = rec.category_name
        elif group_by == "month":
            occ = rec.occurred_at if rec.occurred_at.tzinfo else rec.occurred_at.replace(tzinfo=UTC)
            key = occ.astimezone(SHANGHAI).strftime("%Y-%m")
            label = key
        elif group_by == "account":
            key = rec.account_ids[0] if rec.account_ids else "unknown"
            label = rec.account_names[0] if rec.account_names else "未知账户"
        else:
            key = rec.payee
            label = rec.payee
        buckets[key].append(rec)
        labels[key] = label

    items: list[BreakdownItem] = []
    for key, rows in buckets.items():
        gross = sum(r.base_amount_minor for r in rows if r.type == "expense")
        refund = sum(r.base_amount_minor for r in rows if r.type == "refund")
        net = require_safe_signed_minor(gross - refund)
        inc = sum(r.base_amount_minor for r in rows if r.type == "income")
        exp_count = sum(1 for r in rows if r.type == "expense")
        items.append(
            BreakdownItem(
                key=key,
                label=labels[key],
                grossExpenseCents=gross,
                refundCents=refund,
                netExpenseCents=net,
                incomeCents=inc,
                transactionCount=len(rows),
                averageExpenseCents=_integer_avg_minor(gross, exp_count),
                sharePct=None,
                citedTransactionIds=[r.id for r in rows],
            )
        )

    if group_by == "month":
        items.sort(key=lambda item: item.key)
    elif is_income_view:
        items.sort(key=lambda item: (-item.income_cents, item.label))
    else:
        items.sort(key=lambda item: (-item.net_expense_cents, item.label))

    denominator = (
        sum(max(item.income_cents, 0) for item in items)
        if is_income_view
        else sum(max(item.net_expense_cents, 0) for item in items)
    )
    if denominator > 0:
        for item in items:
            numerator = item.income_cents if is_income_view else max(item.net_expense_cents, 0)
            item.share_pct = round((numerator / denominator) * 100.0, 2)
    return items


class NLQCFOService:
    """Orchestrates strictly read-only Ask Your Money queries and household digests."""

    def __init__(
        self,
        repository: LedgerReadRepository,
        adapter: CFOAdapter | None = None,
    ):
        self._repo = repository
        self._adapter: CFOAdapter = adapter or DeterministicOfflineCFOAdapter()

    def capabilities(self, member_id: str) -> CfoCapabilitiesResponse:
        context = self._repo.load_catalog_context(member_id)
        return CfoCapabilitiesResponse(
            readOnly=True,
            adapterName=self._adapter.adapter_name,
            baseCurrency=context.base_currency,
            timezone="Asia/Shanghai",
            privacyGuarantees=[
                "仅执行只读账本查询，绝不写入或修改交易、账户或分摊",
                "严格复用成员账户授权边界，个人私有账户不向其他成员或管理员泄露",
                "跨私人账户转账自动脱敏为“私人账户往来（已脱敏）”，关键词不匹配隐藏字段",
                "所有金额与同比/环比均由后端整数分确定性计算，回答必须引用可追溯的交易 ID",
            ],
        )

    def ask(self, member_id: str, request: AskQuestionInput) -> AskYourMoneyResponse:
        context = self._repo.load_catalog_context(member_id)
        as_of = request.as_of_date or _today_shanghai()

        plan, planner_warnings = self._adapter.plan_query(
            question=request.question,
            context=context,
            as_of_date=as_of,
            scope_override=request.scope,
            include_comparison=request.include_comparison,
        )
        records = self._repo.query_visible_transactions(
            member_id,
            date_from=plan.date_from,
            date_to=plan.date_to,
            types=tuple(plan.types),
            category_ids=tuple(plan.category_ids),
            account_ids=tuple(plan.account_ids),
            scope=plan.scope,
            keyword=plan.keyword,
            min_amount_minor=plan.min_amount_cents,
            max_amount_minor=plan.max_amount_cents,
            only_with_splits=plan.only_with_splits,
            only_refunded=plan.only_refunded,
            limit=plan.limit,
        )

        totals = _compute_totals(records, base_currency=context.base_currency)
        comparison: PeriodComparison | None = None
        if plan.comparison_date_from is not None and plan.comparison_date_to is not None:
            prev_records = self._repo.query_visible_transactions(
                member_id,
                date_from=plan.comparison_date_from,
                date_to=plan.comparison_date_to,
                types=tuple(plan.types),
                category_ids=tuple(plan.category_ids),
                account_ids=tuple(plan.account_ids),
                scope=plan.scope,
                keyword=plan.keyword,
                min_amount_minor=plan.min_amount_cents,
                max_amount_minor=plan.max_amount_cents,
                only_with_splits=plan.only_with_splits,
                only_refunded=plan.only_refunded,
                limit=plan.limit,
            )
            prev_totals = _compute_totals(prev_records, base_currency=context.base_currency)
            comparison = _compute_comparison(
                current=totals,
                previous=prev_totals,
                previous_from=plan.comparison_date_from,
                previous_to=plan.comparison_date_to,
            )

        breakdowns = _compute_breakdowns(
            records,
            group_by=plan.group_by,
            is_income_view=plan.intent == "income_summary",
        )

        truncated = len(records) > request.max_citations
        visible_slice = records[: request.max_citations]
        citations = [_to_citation(rec) for rec in visible_slice]
        valid_citation_ids = [c.transaction_id for c in citations]

        narrative = self._adapter.narrate_ask(
            AskNarrativeContext(
                question=request.question,
                query_plan=plan,
                totals=totals,
                comparison=comparison,
                breakdowns=tuple(breakdowns),
                citations=tuple(citations),
                total_matched_count=len(records),
                assumptions=tuple(plan.assumptions),
            )
        )
        warnings = [*planner_warnings, *narrative.warnings]
        # Verify final citedTransactionIds strictly exist in returned citations.
        valid_id_set = set(valid_citation_ids)
        cited_ids = (
            [tid for tid in narrative.cited_transaction_ids if tid in valid_id_set]
            if narrative.cited_transaction_ids
            else valid_citation_ids
        )

        return AskYourMoneyResponse(
            question=request.question,
            answer=narrative.text,
            adapterName=self._adapter.adapter_name,
            readOnly=True,
            timezone="Asia/Shanghai",
            queryPlan=plan,
            assumptions=list(plan.assumptions),
            totals=totals,
            comparison=comparison,
            breakdowns=breakdowns,
            citations=citations,
            citedTransactionIds=cited_ids,
            totalMatchedCount=len(records),
            truncatedCitations=truncated,
            warnings=warnings,
        )

    def digest(self, member_id: str, request: DigestQueryInput) -> HouseholdDigestResponse:
        context = self._repo.load_catalog_context(member_id)
        as_of = request.as_of_date or _today_shanghai()

        if request.month:
            year_str, month_str = request.month.split("-", 1)
            date_from, date_to = month_bounds(int(year_str), int(month_str))
            period_label = request.month
        elif request.starts_on is not None and request.ends_on is not None:
            date_from, date_to = request.starts_on, request.ends_on
            period_label = f"{date_from.isoformat()} ~ {date_to.isoformat()}"
        else:
            date_from, date_to = month_bounds(as_of.year, as_of.month)
            period_label = f"{as_of.year:04d}-{as_of.month:02d}"

        records = self._repo.query_visible_transactions(
            member_id,
            date_from=date_from,
            date_to=date_to,
            types=("expense", "refund", "income", "transfer"),
            scope=request.scope,
            limit=2000,
        )
        totals = _compute_totals(records, base_currency=context.base_currency)

        prev_from, prev_to = previous_equal_window(date_from, date_to)
        prev_records = self._repo.query_visible_transactions(
            member_id,
            date_from=prev_from,
            date_to=prev_to,
            types=("expense", "refund", "income", "transfer"),
            scope=request.scope,
            limit=2000,
        )
        prev_totals = _compute_totals(prev_records, base_currency=context.base_currency)
        comparison = _compute_comparison(
            current=totals,
            previous=prev_totals,
            previous_from=prev_from,
            previous_to=prev_to,
        )

        expense_and_refund = [r for r in records if r.type in ("expense", "refund")]
        top_categories = _compute_breakdowns(
            expense_and_refund, group_by="category", is_income_view=False
        )[: request.max_highlights]

        largest_records = sorted(
            (r for r in records if r.type == "expense"),
            key=lambda r: (-r.base_amount_minor, r.id),
        )[: request.max_highlights]
        largest_expenses = [_to_citation(r) for r in largest_records]

        refund_records = [r for r in records if r.type == "refund"]
        refund_summary = RefundDigestSummary(
            refundCount=len(refund_records),
            refundTotalCents=sum(r.base_amount_minor for r in refund_records),
            citedTransactionIds=[r.id for r in refund_records],
        )

        open_split_records = [r for r in records if r.split_status == "open"]
        pending_share_total = sum(
            r.member_share_minor
            for r in open_split_records
            if r.member_share_minor is not None and r.member_share_status == "pending"
        )
        open_splits_summary = OpenSplitsSummary(
            openSplitCount=len(open_split_records),
            pendingMemberShareCents=pending_share_total,
            citedTransactionIds=[r.id for r in open_split_records],
        )

        citations = [_to_citation(r) for r in records[: request.max_citations]]
        cited_ids = [c.transaction_id for c in citations]

        narrative = self._adapter.narrate_digest(
            DigestNarrativeContext(
                period_label=period_label,
                date_from=date_from,
                date_to=date_to,
                scope=request.scope,
                totals=totals,
                comparison=comparison,
                top_categories=tuple(top_categories),
                largest_expenses=tuple(largest_expenses),
                refund_summary=refund_summary,
                open_splits_summary=open_splits_summary,
            )
        )

        return HouseholdDigestResponse(
            periodLabel=period_label,
            dateFrom=date_from,
            dateTo=date_to,
            scope=request.scope,
            summary=narrative.text,
            adapterName=self._adapter.adapter_name,
            readOnly=True,
            timezone="Asia/Shanghai",
            totals=totals,
            comparison=comparison,
            topCategories=top_categories,
            largestExpenses=largest_expenses,
            refundSummary=refund_summary,
            openSplitsSummary=open_splits_summary,
            citations=citations,
            citedTransactionIds=cited_ids,
            warnings=list(narrative.warnings),
        )
