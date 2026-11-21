from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from statistics import median
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..ai.cfo_provider import (
    CfoAccountHint,
    CfoCategoryHint,
    CfoDriverFact,
    CfoPlanningContext,
    PlannedLedgerQuery,
    VerifiedCfoFacts,
    format_minor_yuan,
    get_cfo_provider,
)
from ..config import Settings
from ..models import Account, Member, Split, Transaction
from ..money import require_safe_signed_minor
from ..repositories.accounts import AccountRepository
from ..repositories.ledger import LedgerRepository
from ..schemas import (
    CashflowRadarInput,
    CashflowRadarOut,
    CashflowScenarioOut,
    CashflowWeekBucketOut,
    CfoAskInput,
    CfoAskOut,
    CfoCapabilitiesOut,
    CfoCitationOut,
    CfoDriverOut,
    CfoMetricsOut,
    CfoResolvedQueryOut,
    RecurringCandidateOut,
)
from .permissions import (
    account_permission,
    can_view_transaction,
    require_account_permission,
    transaction_has_hidden_accounts,
    visible_transaction_account_ids,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
_PAYEE_CLEAN_RE = re.compile(r"[\s0-9#\-_:：()（）]+")


def _utc_bounds(starts_on: date, ends_on: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(starts_on, time.min, SHANGHAI).astimezone(UTC),
        datetime.combine(ends_on, time.max, SHANGHAI).astimezone(UTC),
    )


def _round_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((abs(numerator) + denominator // 2) // denominator)


def get_capabilities(settings: Settings) -> CfoCapabilitiesOut:
    provider = get_cfo_provider(settings)
    external_llm = bool(
        provider.uses_external_model and settings.cfo_api_base_url and settings.cfo_api_key
    )
    if external_llm:
        boundary = (
            "已启用受控外部模型规划与叙述；所有金额、笔数、来源引用仍由本地账本确定性查询生成，"
            "私人未授权账户与脱敏对手方绝不进入检索上下文"
        )
    else:
        boundary = "纯本地确定性模式：零外部 AI 调用，全部意图解析、整数核算与来源追溯均在本机完成"
    return CfoCapabilitiesOut(
        ocrEnabled=settings.ocr_enabled,
        ocrProvider=settings.ocr_provider,
        cfoProvider=provider.provider_id,
        externalLlmEnabled=external_llm,
        supportedIntents=[
            "overview",
            "spending_change",
            "frequency_vs_ticket",
            "financial_memory",
            "large_expenses",
        ],
        privacyBoundary=boundary,
    )


def _visible_accounts_map(db: Session, member: Member) -> dict[str, Account]:
    repo = AccountRepository(db)
    result: dict[str, Account] = {}
    for account in repo.list_active_accounts():
        if account_permission(db, member, account) is not None:
            result[account.id] = account
    return result


def _filter_window_transactions(
    db: Session,
    member: Member,
    transactions: list[Transaction],
    *,
    plan: PlannedLedgerQuery,
    visible_accounts: dict[str, Account],
    category_names: dict[str, str],
) -> list[Transaction]:
    allowed_accounts = set(plan.account_ids)
    allowed_categories = set(plan.category_ids)
    needle = plan.keyword.casefold() if plan.keyword else None
    matched: list[Transaction] = []

    for txn in transactions:
        if not can_view_transaction(db, member, txn):
            continue
        visible_ids = visible_transaction_account_ids(db, member, txn)
        if allowed_accounts and not (visible_ids & allowed_accounts):
            continue
        if plan.scope != "all":
            if not any(
                aid in visible_accounts and visible_accounts[aid].scope == plan.scope
                for aid in visible_ids
            ):
                continue
        if allowed_categories and txn.category_id not in allowed_categories:
            continue
        if needle:
            partial = transaction_has_hidden_accounts(db, member, txn)
            visible_payee = "私人账户往来（已脱敏）" if partial else txn.payee
            visible_note = "" if partial else txn.note
            cat_name = category_names.get(txn.category_id or "", "")
            if (
                needle not in visible_payee.casefold()
                and needle not in visible_note.casefold()
                and needle not in cat_name.casefold()
            ):
                continue
        if plan.intent == "large_expenses":
            if txn.type != "expense" or txn.base_amount_minor < plan.large_expense_threshold_cents:
                continue
        matched.append(txn)
    return matched


def _expense_burden_breakdown(
    expense: Transaction,
    *,
    member: Member,
    linked_refunds: list[Transaction],
    split: Split | None,
) -> tuple[int, int, int, int, Literal["none", "open", "settled", "returned"]]:
    gross = expense.base_amount_minor
    refunded = min(gross, sum(item.base_amount_minor for item in linked_refunds))
    net = max(0, gross - refunded)
    if split is None:
        return gross, refunded, 0, net, "none"
    if split.status == "returned":
        return gross, refunded, 0, net, "returned"
    member_raw_share = sum(
        share.share_minor for share in split.shares if share.member_id == member.id
    )
    own_burden = _round_div(net * member_raw_share, expense.amount_minor)
    own_burden = max(0, min(net, own_burden))
    reimbursed = net - own_burden
    status: Literal["open", "settled"] = "settled" if split.status == "settled" else "open"
    return gross, refunded, reimbursed, own_burden, status


def _compute_growth_driver(
    curr_net: int,
    curr_count: int,
    curr_avg: int,
    prev_net: int | None,
    prev_count: int | None,
    prev_avg: int | None,
) -> Literal["frequency", "ticket_size", "mixed", "decreased", "flat", "none"]:
    if prev_net is None or prev_count is None or prev_avg is None or prev_count == 0:
        return "none"
    delta = curr_net - prev_net
    if delta < 0:
        return "decreased"
    if delta == 0:
        return "flat"
    if curr_count > prev_count and curr_avg <= prev_avg:
        return "frequency"
    if curr_avg > prev_avg and curr_count <= prev_count:
        return "ticket_size"
    freq_effect = abs((curr_count - prev_count) * prev_avg)
    ticket_effect = abs(curr_count * (curr_avg - prev_avg))
    if freq_effect >= 2 * ticket_effect:
        return "frequency"
    if ticket_effect >= 2 * freq_effect:
        return "ticket_size"
    return "mixed"


def ask_cfo(
    db: Session,
    member: Member,
    input_data: CfoAskInput,
    *,
    settings: Settings,
) -> CfoAskOut:
    if input_data.account_id:
        require_account_permission(db, member, input_data.account_id)

    visible_accounts = _visible_accounts_map(db, member)
    ledger_repo = LedgerRepository(db)
    active_categories = ledger_repo.list_categories(include_inactive=False)
    all_categories = ledger_repo.list_categories(include_inactive=True)
    category_names = {cat.id: cat.name for cat in all_categories}

    as_of = input_data.as_of_date or datetime.now(SHANGHAI).date()
    context = CfoPlanningContext(
        as_of_date=as_of,
        categories=[
            CfoCategoryHint(id=c.id, name=c.name, kind=c.kind, parent_id=c.parent_id)
            for c in active_categories
        ],
        accounts=[
            CfoAccountHint(id=a.id, name=a.name, scope=a.scope, currency=a.currency)
            for a in visible_accounts.values()
        ],
        explicit_account_id=input_data.account_id,
        explicit_scope=input_data.scope,
        large_expense_threshold_cents=input_data.large_expense_threshold_cents,
    )

    provider = get_cfo_provider(settings)
    plan = provider.plan_query(input_data.question, context=context)
    plan.account_ids = [aid for aid in plan.account_ids if aid in visible_accounts]

    curr_start_utc, curr_end_utc = _utc_bounds(plan.starts_on, plan.ends_on)
    raw_current = ledger_repo.list_transactions_filtered(
        starts_at=curr_start_utc,
        ends_at=curr_end_utc,
        limit=None,
    )
    matched_current = _filter_window_transactions(
        db,
        member,
        raw_current,
        plan=plan,
        visible_accounts=visible_accounts,
        category_names=category_names,
    )

    matched_prev: list[Transaction] | None = None
    if plan.comparison_starts_on and plan.comparison_ends_on:
        prev_start_utc, prev_end_utc = _utc_bounds(
            plan.comparison_starts_on, plan.comparison_ends_on
        )
        raw_prev = ledger_repo.list_transactions_filtered(
            starts_at=prev_start_utc,
            ends_at=prev_end_utc,
            limit=None,
        )
        matched_prev = _filter_window_transactions(
            db,
            member,
            raw_prev,
            plan=plan,
            visible_accounts=visible_accounts,
            category_names=category_names,
        )

    current_expenses = [t for t in matched_current if t.type == "expense"]
    prev_expenses = [t for t in (matched_prev or []) if t.type == "expense"]
    all_expense_ids = [t.id for t in current_expenses] + [t.id for t in prev_expenses]

    raw_refunds = ledger_repo.list_active_refunds_by_original_ids(all_expense_ids)
    visible_refunds_by_exp: dict[str, list[Transaction]] = {
        exp_id: [r for r in items if can_view_transaction(db, member, r)]
        for exp_id, items in raw_refunds.items()
    }
    splits_by_txn = ledger_repo.list_splits_by_transaction_ids([t.id for t in current_expenses])
    provenance = ledger_repo.list_source_provenance_by_transaction_ids(
        [t.id for t in matched_current]
    )

    gross_expense = 0
    linked_refund = 0
    split_reimbursed = 0
    own_burden = 0
    cat_current_net: dict[str, int] = defaultdict(int)
    cat_current_txns: dict[str, list[str]] = defaultdict(list)

    per_txn_breakdown: dict[
        str, tuple[int, int, int, int, Literal["none", "open", "settled", "returned"]]
    ] = {}
    for exp in current_expenses:
        g, r, s, o, sp_status = _expense_burden_breakdown(
            exp,
            member=member,
            linked_refunds=visible_refunds_by_exp.get(exp.id, []),
            split=splits_by_txn.get(exp.id),
        )
        per_txn_breakdown[exp.id] = (g, r, s, o, sp_status)
        gross_expense += g
        linked_refund += r
        split_reimbursed += s
        own_burden += o
        label = category_names.get(exp.category_id or "", "待分类")
        cat_current_net[label] += g - r
        cat_current_txns[label].append(exp.id)

    net_expense = max(0, gross_expense - linked_refund)
    income_cents = sum(t.base_amount_minor for t in matched_current if t.type == "income")
    net_cashflow = require_safe_signed_minor(income_cents - net_expense)
    expense_count = len(current_expenses)
    avg_expense = _round_div(net_expense, expense_count)

    prev_net: int | None = None
    net_delta: int | None = None
    prev_count: int | None = None
    prev_avg: int | None = None
    cat_prev_net: dict[str, int] = defaultdict(int)
    if matched_prev is not None:
        prev_gross = 0
        prev_refund = 0
        for exp in prev_expenses:
            refs = visible_refunds_by_exp.get(exp.id, [])
            r = min(exp.base_amount_minor, sum(x.base_amount_minor for x in refs))
            prev_gross += exp.base_amount_minor
            prev_refund += r
            label = category_names.get(exp.category_id or "", "待分类")
            cat_prev_net[label] += exp.base_amount_minor - r
        prev_net = max(0, prev_gross - prev_refund)
        net_delta = require_safe_signed_minor(net_expense - prev_net)
        prev_count = len(prev_expenses)
        prev_avg = _round_div(prev_net, prev_count)

    all_driver_labels = set(cat_current_net) | set(cat_prev_net)
    drivers_sorted = sorted(
        all_driver_labels,
        key=lambda lbl: (
            abs(cat_current_net[lbl] - cat_prev_net[lbl])
            if matched_prev is not None
            else cat_current_net[lbl],
            cat_current_net[lbl],
        ),
        reverse=True,
    )
    top_drivers_out: list[CfoDriverOut] = []
    driver_facts: list[CfoDriverFact] = []
    for label in drivers_sorted[:5]:
        curr_val = cat_current_net[label]
        prev_val = cat_prev_net[label]
        if curr_val == 0 and prev_val == 0:
            continue
        delta_val = require_safe_signed_minor(curr_val - prev_val)
        txn_ids = cat_current_txns[label]
        top_drivers_out.append(
            CfoDriverOut(
                label=label,
                kind="category",
                currentCents=curr_val,
                previousCents=prev_val,
                deltaCents=delta_val,
                transactionCount=len(txn_ids),
                transactionIds=txn_ids[:20],
            )
        )
        driver_facts.append(
            CfoDriverFact(
                label=label,
                kind="category",
                current_cents=curr_val,
                previous_cents=prev_val,
                delta_cents=delta_val,
                transaction_count=len(txn_ids),
            )
        )

    citation_txns = (
        sorted(current_expenses, key=lambda t: t.base_amount_minor, reverse=True)
        if plan.intent == "large_expenses"
        else matched_current
    )
    citations: list[CfoCitationOut] = []
    for txn in citation_txns[:100]:
        if txn.type == "transfer" and plan.intent != "overview":
            continue
        partial = transaction_has_hidden_accounts(db, member, txn)
        src_filename, src_ref = (None, None) if partial else provenance.get(txn.id, (None, None))
        if txn.type == "expense" and txn.id in per_txn_breakdown:
            _g, r_cents, _s, own_cents, sp_status = per_txn_breakdown[txn.id]
        else:
            r_cents, own_cents, sp_status = 0, 0, "none"
        citations.append(
            CfoCitationOut(
                transactionId=txn.id,
                date=txn.occurred_at,
                type=txn.type,
                payee="私人账户往来（已脱敏）" if partial else txn.payee,
                categoryId=txn.category_id,
                categoryName=category_names.get(txn.category_id or ""),
                currency=txn.currency,
                amountCents=txn.amount_minor,
                baseAmountCents=txn.base_amount_minor,
                linkedRefundCents=r_cents,
                ownBurdenCents=own_cents,
                splitStatus=sp_status,
                source=txn.source,
                sourceFilename=src_filename,
                sourceRef=src_ref,
            )
        )

    growth_driver = _compute_growth_driver(
        net_expense, expense_count, avg_expense, prev_net, prev_count, prev_avg
    )
    facts = VerifiedCfoFacts(
        question=input_data.question,
        intent=plan.intent,
        starts_on=plan.starts_on,
        ends_on=plan.ends_on,
        comparison_starts_on=plan.comparison_starts_on,
        comparison_ends_on=plan.comparison_ends_on,
        gross_expense_cents=gross_expense,
        linked_refund_cents=linked_refund,
        net_expense_cents=net_expense,
        split_reimbursed_cents=split_reimbursed,
        own_burden_cents=own_burden,
        income_cents=income_cents,
        net_cashflow_cents=net_cashflow,
        expense_count=expense_count,
        average_expense_cents=avg_expense,
        previous_net_expense_cents=prev_net,
        net_expense_delta_cents=net_delta,
        previous_expense_count=prev_count,
        previous_average_expense_cents=prev_avg,
        primary_growth_driver=growth_driver,
        top_changes=driver_facts,
        citation_count=len(citations),
    )
    answer = provider.narrate(facts)

    return CfoAskOut(
        question=input_data.question,
        intent=plan.intent,
        provider=provider.provider_id,
        answer=answer,
        assumptions=plan.assumptions,
        resolvedQuery=CfoResolvedQueryOut(
            startsOn=plan.starts_on,
            endsOn=plan.ends_on,
            comparisonStartsOn=plan.comparison_starts_on,
            comparisonEndsOn=plan.comparison_ends_on,
            scope=plan.scope,
            accountIds=plan.account_ids,
            categoryIds=plan.category_ids,
            keyword=plan.keyword,
        ),
        metrics=CfoMetricsOut(
            grossExpenseCents=gross_expense,
            linkedRefundCents=linked_refund,
            netExpenseCents=net_expense,
            splitReimbursedCents=split_reimbursed,
            ownBurdenCents=own_burden,
            incomeCents=income_cents,
            netCashflowCents=net_cashflow,
            expenseCount=expense_count,
            averageExpenseCents=avg_expense,
            previousNetExpenseCents=prev_net,
            netExpenseDeltaCents=net_delta,
            previousExpenseCount=prev_count,
            previousAverageExpenseCents=prev_avg,
            primaryGrowthDriver=growth_driver,
        ),
        topChanges=top_drivers_out,
        citations=citations,
    )


def _normalise_payee_key(payee: str) -> str:
    cleaned = _PAYEE_CLEAN_RE.sub("", payee.strip().casefold())
    return cleaned or payee.strip().casefold()


def _advance_recurring_date(current: date, cadence: Literal["weekly", "monthly"]) -> date:
    if cadence == "weekly":
        return current + timedelta(days=7)
    total = current.year * 12 + (current.month - 1) + 1
    next_year, zero_month = divmod(total, 12)
    next_month = zero_month + 1
    last_day = calendar.monthrange(next_year, next_month)[1]
    return date(next_year, next_month, min(current.day, last_day))


def _detect_recurring(
    transactions: list[Transaction],
    *,
    as_of: date,
    horizon_end: date,
) -> tuple[list[RecurringCandidateOut], set[str], dict[date, list[tuple[str, int]]]]:
    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.type not in {"expense", "income"}:
            continue
        groups[(txn.type, _normalise_payee_key(txn.payee))].append(txn)

    candidates: list[RecurringCandidateOut] = []
    recurring_txn_ids: set[str] = set()
    projected_events: dict[date, list[tuple[str, int]]] = defaultdict(list)

    for (direction, _key), items in groups.items():
        ordered = sorted(items, key=lambda item: item.occurred_at)
        by_day: list[tuple[date, Transaction]] = []
        seen_days: set[date] = set()
        for item in ordered:
            local_day = item.occurred_at.astimezone(SHANGHAI).date()
            if local_day not in seen_days:
                seen_days.add(local_day)
                by_day.append((local_day, item))
        if len(by_day) < 2:
            continue

        gaps = [(by_day[idx][0] - by_day[idx - 1][0]).days for idx in range(1, len(by_day))]
        med_gap = float(median(gaps))
        cadence: Literal["weekly", "monthly"] | None = None
        if 5 <= med_gap <= 9:
            cadence = "weekly"
        elif 25 <= med_gap <= 35:
            cadence = "monthly"
        if cadence is None:
            continue

        amounts = [txn.base_amount_minor for _, txn in by_day]
        typical_amount = max(1, int(round(float(median(amounts)))))
        max_deviation = max(abs(amount - typical_amount) for amount in amounts)
        if max_deviation > _round_div(typical_amount * 35, 100):
            continue

        for txn in ordered:
            recurring_txn_ids.add(txn.id)

        cursor = by_day[-1][0]
        while cursor <= as_of:
            cursor = _advance_recurring_date(cursor, cadence)
        next_expected = cursor if cursor <= horizon_end else None

        future = cursor
        while future <= horizon_end:
            projected_events[future].append((direction, typical_amount))
            future = _advance_recurring_date(future, cadence)

        confidence = Decimal("0.88") if len(by_day) >= 3 else Decimal("0.75")
        candidates.append(
            RecurringCandidateOut(
                payee=ordered[-1].payee,
                direction=direction,  # type: ignore[arg-type]
                cadence=cadence,
                typicalAmountCents=typical_amount,
                nextExpectedDate=next_expected,
                confidence=confidence,
                evidenceTransactionIds=[txn.id for _, txn in by_day],
            )
        )

    candidates.sort(key=lambda item: item.typical_amount_cents, reverse=True)
    return candidates, recurring_txn_ids, projected_events


def build_cashflow_radar(
    db: Session,
    member: Member,
    input_data: CashflowRadarInput,
    *,
    settings: Settings,
) -> CashflowRadarOut:
    as_of = input_data.as_of_date or datetime.now(SHANGHAI).date()
    horizon_end = as_of + timedelta(days=input_data.horizon_days)
    lookback_start = as_of - timedelta(days=input_data.lookback_days - 1)

    acct_repo = AccountRepository(db)
    visible_accounts = [
        acct
        for acct in acct_repo.list_active_accounts()
        if account_permission(db, member, acct) is not None
        and (input_data.scope == "all" or acct.scope == input_data.scope)
    ]
    balances = acct_repo.compute_balances_for_accounts(visible_accounts)

    starting_liquid = 0
    excluded_foreign: list[str] = []
    for acct in visible_accounts:
        if acct.kind != "asset":
            continue
        if acct.currency != settings.base_currency:
            excluded_foreign.append(acct.id)
            continue
        starting_liquid = require_safe_signed_minor(starting_liquid + balances[acct.id])

    ledger_repo = LedgerRepository(db)
    lb_start_utc, lb_end_utc = _utc_bounds(lookback_start, as_of)
    raw_history = ledger_repo.list_transactions_filtered(
        starts_at=lb_start_utc,
        ends_at=lb_end_utc,
        ascending=True,
        limit=None,
    )
    visible_ids_set = {acct.id for acct in visible_accounts}
    history: list[Transaction] = []
    for txn in raw_history:
        if not can_view_transaction(db, member, txn):
            continue
        if transaction_has_hidden_accounts(db, member, txn):
            continue
        touched = visible_transaction_account_ids(db, member, txn)
        if not (touched & visible_ids_set):
            continue
        history.append(txn)

    recurring_candidates, recurring_ids, recurring_schedule = _detect_recurring(
        history,
        as_of=as_of,
        horizon_end=horizon_end,
    )

    expense_ids = [t.id for t in history if t.type == "expense" and t.id not in recurring_ids]
    refunds_map = ledger_repo.list_active_refunds_by_original_ids(expense_ids)
    discretionary_expense_total = 0
    for exp in history:
        if exp.type != "expense" or exp.id in recurring_ids:
            continue
        refs = [r for r in refunds_map.get(exp.id, []) if can_view_transaction(db, member, r)]
        refunded = min(exp.base_amount_minor, sum(r.base_amount_minor for r in refs))
        discretionary_expense_total += max(0, exp.base_amount_minor - refunded)

    discretionary_income_total = sum(
        t.base_amount_minor for t in history if t.type == "income" and t.id not in recurring_ids
    )
    daily_disc_expense = _round_div(discretionary_expense_total, input_data.lookback_days)
    daily_disc_income = _round_div(discretionary_income_total, input_data.lookback_days)

    planned_by_day: dict[date, list[tuple[str, int]]] = defaultdict(list)
    for event in input_data.planned_events:
        if as_of < event.occurs_on <= horizon_end:
            planned_by_day[event.occurs_on].append((event.direction, event.amount_cents))

    scenario_specs: list[tuple[Literal["baseline", "conservative", "stress"], int, int]] = [
        ("baseline", 100, 100),
        ("conservative", 115, 90),
        ("stress", 130, 75),
    ]
    scenarios: list[CashflowScenarioOut] = []
    for scenario_name, exp_pct, inc_pct in scenario_specs:
        scaled_daily_exp = _round_div(daily_disc_expense * exp_pct, 100)
        scaled_daily_inc = _round_div(daily_disc_income * inc_pct, 100)

        running_balance = starting_liquid
        buckets: list[CashflowWeekBucketOut] = []
        lowest_balance = starting_liquid
        tightest_week = as_of + timedelta(days=1)

        offset = 1
        week_index = 1
        while offset <= input_data.horizon_days:
            w_start = as_of + timedelta(days=offset)
            w_end = min(horizon_end, w_start + timedelta(days=6))
            days_in_bucket = (w_end - w_start).days + 1
            inflow = scaled_daily_inc * days_in_bucket
            outflow = scaled_daily_exp * days_in_bucket

            day_cursor = w_start
            while day_cursor <= w_end:
                for direction, amount in recurring_schedule.get(day_cursor, []):
                    if direction == "income":
                        inflow += amount
                    else:
                        outflow += amount
                for direction, amount in planned_by_day.get(day_cursor, []):
                    if direction == "income":
                        inflow += amount
                    else:
                        outflow += amount
                day_cursor += timedelta(days=1)

            net = require_safe_signed_minor(inflow - outflow)
            running_balance = require_safe_signed_minor(running_balance + net)
            if week_index == 1 or running_balance < lowest_balance:
                lowest_balance = running_balance
                tightest_week = w_start

            buckets.append(
                CashflowWeekBucketOut(
                    weekIndex=week_index,
                    startsOn=w_start,
                    endsOn=w_end,
                    inflowCents=inflow,
                    outflowCents=outflow,
                    netCents=net,
                    closingBalanceCents=running_balance,
                )
            )
            offset += 7
            week_index += 1

        scenarios.append(
            CashflowScenarioOut(
                scenario=scenario_name,
                endingBalanceCents=running_balance,
                lowestBalanceCents=lowest_balance,
                tightestWeekStart=tightest_week,
                breachesSafeBalance=lowest_balance < input_data.minimum_safe_balance_cents,
                weeklyBuckets=buckets,
            )
        )

    risk_alerts: list[str] = []
    baseline_out = scenarios[0]
    stress_out = scenarios[2]
    if baseline_out.lowest_balance_cents < 0:
        risk_alerts.append(
            f"基准情景下预计在 {baseline_out.tightest_week_start.isoformat()} 当周出现资金缺口"
            f"（最低余额 {format_minor_yuan(baseline_out.lowest_balance_cents)} 元）"
        )
    elif baseline_out.breaches_safe_balance:
        risk_alerts.append(
            f"基准情景下预计在 {baseline_out.tightest_week_start.isoformat()} 当周跌破安全余额线"
            f"（最低余额 {format_minor_yuan(baseline_out.lowest_balance_cents)} 元）"
        )
    elif stress_out.breaches_safe_balance or stress_out.lowest_balance_cents < 0:
        risk_alerts.append(
            f"基准情景安全，但压力情景下 {stress_out.tightest_week_start.isoformat()} 当周"
            f"现金较紧（最低余额 {format_minor_yuan(stress_out.lowest_balance_cents)} 元）"
        )

    assumptions = [
        (
            f"历史回溯窗口：{lookback_start.isoformat()} 至 {as_of.isoformat()}"
            f"（{input_data.lookback_days} 天）"
        ),
        "起始流动现金仅汇总可见的人民币资产账户（asset），负债账户与外币账户不直接混加",
        (
            "基准/保守/压力三情景分别按历史日均弹性支出的 100% / 115% / 130% "
            "与弹性收入的 100% / 90% / 75% 推演"
        ),
    ]
    if excluded_foreign:
        assumptions.append(
            f"已排除 {len(excluded_foreign)} 个非人民币资产账户，避免未经锁定汇率混算余额"
        )

    return CashflowRadarOut(
        asOfDate=as_of,
        horizonDays=input_data.horizon_days,
        startingLiquidBalanceCents=starting_liquid,
        excludedForeignAccountIds=excluded_foreign,
        dailyDiscretionaryExpenseCents=daily_disc_expense,
        recurringCandidates=recurring_candidates,
        scenarios=scenarios,
        riskAlerts=risk_alerts,
        assumptions=assumptions,
    )
