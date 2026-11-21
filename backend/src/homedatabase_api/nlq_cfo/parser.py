"""Deterministic bilingual (ZH/EN) natural-language query planner for HomeDatabase.

Translates free-form financial questions into a strictly bounded, read-only
LedgerQueryPlan with explicit human-readable assumptions.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from ..money import MAX_SAFE_MINOR
from .protocols import CatalogContext
from .schemas import (
    GroupByDimension,
    LedgerQueryPlan,
    QueryIntent,
    ScopeFilter,
    TransactionTypeFilter,
)

DEFAULT_LARGE_EXPENSE_CENTS = 50_000  # 500.00 CNY default threshold when user asks "大额消费"

_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}

# Helpful keyword aliases mapped to canonical category names when exact category names match.
_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "餐饮": ("餐饮", "吃饭", "外卖", "奶茶", "咖啡", "聚餐", "餐厅", "dining", "food", "takeout"),
    "买菜超市": ("买菜", "超市", "生鲜", "菜市场", "日用品", "grocery", "groceries", "supermarket"),
    "交通": (
        "交通",
        "打车",
        "地铁",
        "公交",
        "加油",
        "停车",
        "高铁",
        "机票",
        "transport",
        "commute",
    ),
    "居家": (
        "居家",
        "房租",
        "水电",
        "物业",
        "装修",
        "家具",
        "宽带",
        "housing",
        "rent",
        "utilities",
    ),
    "医疗": ("医疗", "医院", "看病", "买药", "体检", "medical", "health", "pharmacy"),
    "教育": ("教育", "学费", "培训", "书本", "课程", "education", "tuition"),
    "娱乐": ("娱乐", "电影", "游戏", "旅游", "旅行", "门票", "entertainment", "travel"),
    "工资": ("工资", "薪水", "发薪", "salary", "payroll"),
    "奖金": ("奖金", "年终奖", "bonus"),
    "理财收益": ("理财", "利息", "分红", "收益", "interest", "dividend"),
}

# Merchant/topic keywords that should additionally filter payee/note when narrower than a category.
_SPECIFIC_TOPIC_KEYWORDS = (
    "外卖",
    "奶茶",
    "咖啡",
    "装修",
    "房租",
    "水电",
    "打车",
    "高铁",
    "机票",
    "体检",
    "旅行",
    "旅游",
    "电影",
    "takeout",
    "coffee",
    "renovation",
    "rent",
)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def shift_months(anchor: date, delta_months: int) -> tuple[int, int]:
    total = (anchor.year * 12 + (anchor.month - 1)) + delta_months
    year, month_zero = divmod(total, 12)
    return year, month_zero + 1


def previous_equal_window(date_from: date, date_to: date) -> tuple[date, date]:
    """Compute the immediately preceding comparison window of matching semantics."""
    # Whole calendar month(s) -> compare against the immediately preceding N calendar months.
    if date_from.day == 1 and date_to.day == calendar.monthrange(date_to.year, date_to.month)[1]:
        month_span = (date_to.year - date_from.year) * 12 + (date_to.month - date_from.month) + 1
        prev_start_year, prev_start_month = shift_months(date_from, -month_span)
        prev_end_year, prev_end_month = shift_months(date_from, -1)
        prev_start = date(prev_start_year, prev_start_month, 1)
        _, prev_end = month_bounds(prev_end_year, prev_end_month)
        return prev_start, prev_end
    span_days = (date_to - date_from).days
    prev_end = date_from - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span_days)
    return prev_start, prev_end


def _parse_int_token(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _CHINESE_DIGITS.get(token)


def _extract_date_window(question: str, as_of: date) -> tuple[date, date, str]:
    lower = question.casefold()

    # 1. Explicit ISO date range: 2026-08-01 至/到/-/to 2026-08-31
    range_match = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|—|-|to)\s*(\d{4}-\d{2}-\d{2})",
        question,
        flags=re.IGNORECASE,
    )
    if range_match:
        try:
            start = date.fromisoformat(range_match.group(1))
            end = date.fromisoformat(range_match.group(2))
        except ValueError:
            start = end = None
        if start is not None and end is not None and start <= end:
            return (
                start,
                end,
                f"时间口径：显式区间 {start.isoformat()} 至 {end.isoformat()}（Asia/Shanghai）",
            )

    # 2. Explicit year-month: 2026-08 or 2026年8月
    ym_match = re.search(r"(\d{4})\s*(?:-|年)\s*(\d{1,2})\s*月?", question)
    if ym_match:
        year = int(ym_match.group(1))
        month = int(ym_match.group(2))
        if 1 <= month <= 12:
            start, end = month_bounds(year, month)
            return (
                start,
                end,
                f"时间口径：{year}年{month:02d}月整月（{start} 至 {end}，Asia/Shanghai）",
            )

    # 3. Past N months: 过去三个月 / 最近3个月 / last 3 months
    n_months_match = re.search(
        r"(?:过去|最近|近)\s*([0-9一二两三四五六七八九十]{1,2})\s*个?月"
        r"|(?:last|past)\s+(\d{1,2})\s+months?",
        lower,
    )
    if n_months_match:
        raw_n = n_months_match.group(1) or n_months_match.group(2)
        months = _parse_int_token(raw_n)
        if months and 1 <= months <= 24:
            start_year, start_month = shift_months(as_of, -(months - 1))
            start = date(start_year, start_month, 1)
            return (
                start,
                as_of,
                (
                    f"时间口径：过去 {months} 个月（{start.isoformat()} 至 "
                    f"{as_of.isoformat()}，Asia/Shanghai）"
                ),
            )

    # 4. Past N days: 最近30天 / 过去7天 / last 14 days
    n_days_match = re.search(
        r"(?:过去|最近|近)\s*(\d{1,3})\s*(?:天|日)|(?:last|past)\s+(\d{1,3})\s+days?",
        lower,
    )
    if n_days_match:
        days = int(n_days_match.group(1) or n_days_match.group(2))
        days = max(1, min(days, 366))
        start = as_of - timedelta(days=days - 1)
        return (
            start,
            as_of,
            (
                f"时间口径：最近 {days} 天（{start.isoformat()} 至 "
                f"{as_of.isoformat()}，Asia/Shanghai）"
            ),
        )

    # 5. Last month / 上个月 / 上月
    if any(tok in lower for tok in ("上个月", "上月", "last month")):
        prev_year, prev_month = shift_months(as_of, -1)
        start, end = month_bounds(prev_year, prev_month)
        return (
            start,
            end,
            f"时间口径：上个月（{start.isoformat()} 至 {end.isoformat()}，Asia/Shanghai）",
        )

    # 6. Last year / 去年
    if any(tok in lower for tok in ("去年", "last year")):
        start, end = date(as_of.year - 1, 1, 1), date(as_of.year - 1, 12, 31)
        return (
            start,
            end,
            f"时间口径：去年全年（{start.isoformat()} 至 {end.isoformat()}，Asia/Shanghai）",
        )

    # 7. This year / 今年
    if any(tok in lower for tok in ("今年", "this year", "ytd")):
        start = date(as_of.year, 1, 1)
        return (
            start,
            as_of,
            f"时间口径：今年至今（{start.isoformat()} 至 {as_of.isoformat()}，Asia/Shanghai）",
        )

    # 8. Default: current month up to as_of_date
    start = date(as_of.year, as_of.month, 1)
    if any(tok in lower for tok in ("本月", "这个月", "当月", "this month")):
        return (
            start,
            as_of,
            f"时间口径：本月至今（{start.isoformat()} 至 {as_of.isoformat()}，Asia/Shanghai）",
        )
    return (
        start,
        as_of,
        (
            f"时间口径：未指定时间，默认采用本月至今（{start.isoformat()} 至 "
            f"{as_of.isoformat()}，Asia/Shanghai）"
        ),
    )


def _extract_amount_threshold(question: str) -> tuple[int | None, str | None]:
    lower = question.casefold()
    # Explicit threshold: 超过500元 / 大于 1000 / >= 300 / above 500
    m = re.search(
        r"(?:超过|大于|高于|满|>=|above|over)\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|cny|rmb)?",
        lower,
    )
    if m:
        try:
            cents = int((Decimal(m.group(1)) * 100).to_integral_value())
        except (InvalidOperation, ValueError):
            cents = 0
        if 0 < cents <= MAX_SAFE_MINOR:
            return cents, f"金额门槛：筛选本币金额 >= {cents / 100:.2f} 元"
    if any(tok in lower for tok in ("大额", "大笔", "large expense", "big spend")):
        threshold_text = f"{DEFAULT_LARGE_EXPENSE_CENTS / 100:.2f}"
        return (
            DEFAULT_LARGE_EXPENSE_CENTS,
            f"大额消费口径：未指定阈值，默认筛选本币金额 >= {threshold_text} 元",
        )
    return None, None


def _extract_scope(question: str, override: ScopeFilter | None) -> tuple[ScopeFilter, str | None]:
    if override is not None:
        labels = {
            "all_visible": "全部已授权可见账户",
            "family_only": "仅家庭共享账户",
            "personal_only": "仅本人个人账户",
        }
        return override, f"账户范围：按请求参数限定为{labels[override]}"
    lower = question.casefold()
    if any(
        tok in lower
        for tok in ("共享账户", "家庭公共", "公共账户", "仅共享", "family only", "shared accounts")
    ):
        return "family_only", "账户范围：按提问限定为仅家庭共享账户"
    if any(tok in lower for tok in ("个人账户", "私人账户", "我自己的", "personal only")):
        return "personal_only", "账户范围：按提问限定为仅本人个人账户"
    return (
        "all_visible",
        "账户范围：全部已授权可见账户（个人私有账户仅本人可见，跨边界记录自动脱敏）",
    )


def _extract_accounts(question: str, context: CatalogContext) -> list[str]:
    lower = question.casefold()
    matched: list[str] = []
    for account in context.accounts:
        if account.name.casefold() in lower or account.id.casefold() in lower:
            matched.append(account.id)
    return matched


def _extract_categories_and_keyword(
    question: str, context: CatalogContext
) -> tuple[list[str], str | None]:
    lower = question.casefold()
    matched_categories: list[str] = []

    # 1. Match quoted keyword explicitly: 「外卖」 / "超市" / '咖啡'
    quoted = re.search(r"[「『\"'“]([^「」『』\"'“”]{1,40})[」』\"'”]", question)
    explicit_keyword = quoted.group(1).strip() if quoted else None

    # 2. Match real household category names & aliases
    for cat in context.categories:
        if cat.name.casefold() in lower:
            matched_categories.append(cat.id)
            continue
        aliases = _CATEGORY_ALIASES.get(cat.name, ())
        if any(alias in lower for alias in aliases):
            matched_categories.append(cat.id)

    # 3. Extract specific narrower topic keyword (e.g., "外卖" or "装修")
    topic_keyword = explicit_keyword
    if topic_keyword is None:
        for topic in _SPECIFIC_TOPIC_KEYWORDS:
            if topic in lower:
                topic_keyword = topic
                break

    # If user asks specifically about a narrower topic like "外卖" or "装修" that may be tagged
    # in payee/note even under uncategorized or other categories, prefer keyword filtering rather
    # than over-restricting by category when the category name itself wasn't explicitly named.
    if topic_keyword is not None:
        explicit_cat_ids = [cat.id for cat in context.categories if cat.name.casefold() in lower]
        matched_categories = explicit_cat_ids

    return list(dict.fromkeys(matched_categories)), topic_keyword


def _extract_types_intent_and_groupby(
    question: str,
    *,
    has_min_amount: bool,
    has_keyword: bool,
    has_categories: bool,
    multi_month_window: bool,
) -> tuple[list[TransactionTypeFilter], QueryIntent, GroupByDimension, bool, bool, bool]:
    lower = question.casefold()

    only_refunded = any(tok in lower for tok in ("退款", "退货", "refund"))
    only_with_splits = any(tok in lower for tok in ("分摊", "AA", "split", "aa"))
    wants_comparison = any(
        tok in lower
        for tok in (
            "上涨",
            "下降",
            "变化",
            "趋势",
            "对比",
            "环比",
            "客单价",
            "次数",
            "compare",
            "trend",
            "increase",
            "why",
        )
    )

    if any(tok in lower for tok in ("转账", "调拨", "transfer")):
        types: list[TransactionTypeFilter] = ["transfer"]
        intent: QueryIntent = "transaction_search"
    elif only_refunded and not any(tok in lower for tok in ("净支出", "总支出", "分摊", "结余")):
        types = ["refund"]
        intent = "refund_analysis"
    elif any(tok in lower for tok in ("收支", "结余", "现金流", "总览", "cashflow", "net")):
        types = ["expense", "refund", "income"]
        intent = "cashflow_overview"
    elif any(
        tok in lower for tok in ("收入", "工资", "奖金", "赚了", "进账", "income", "salary")
    ) and not any(tok in lower for tok in ("支出", "花了", "消费", "spend", "expense")):
        types = ["income"]
        intent = "income_summary"
    else:
        types = ["expense", "refund"]
        if only_with_splits:
            intent = "split_analysis"
        elif has_min_amount:
            intent = "large_expenses"
        elif wants_comparison:
            intent = "trend_comparison"
        elif any(tok in lower for tok in ("分类", "哪类", "构成", "占比", "category", "breakdown")):
            intent = "category_breakdown"
        elif has_keyword and not has_categories:
            intent = "transaction_search"
        else:
            intent = "spending_summary"

    if any(tok in lower for tok in ("按月", "每月", "月度", "趋势", "by month", "monthly")):
        group_by: GroupByDimension = "month"
    elif any(tok in lower for tok in ("按账户", "哪个账户", "各账户", "by account")):
        group_by = "account"
    elif any(tok in lower for tok in ("按商户", "哪家", "商户", "收款方", "by payee", "merchant")):
        group_by = "payee"
    elif multi_month_window and wants_comparison:
        group_by = "month"
    else:
        group_by = "category"

    return types, intent, group_by, wants_comparison, only_with_splits, only_refunded


def build_rule_based_plan(
    *,
    question: str,
    context: CatalogContext,
    as_of_date: date,
    scope_override: ScopeFilter | None = None,
    include_comparison: bool | None = None,
) -> LedgerQueryPlan:
    """Deterministically compile a natural-language question into a LedgerQueryPlan."""
    date_from, date_to, date_assumption = _extract_date_window(question, as_of_date)
    scope, scope_assumption = _extract_scope(question, scope_override)
    min_amount_cents, amount_assumption = _extract_amount_threshold(question)
    account_ids = _extract_accounts(question, context)
    category_ids, keyword = _extract_categories_and_keyword(question, context)
    multi_month = (date_from.year, date_from.month) != (date_to.year, date_to.month)

    types, intent, group_by, inferred_compare, only_with_splits, only_refunded = (
        _extract_types_intent_and_groupby(
            question,
            has_min_amount=min_amount_cents is not None,
            has_keyword=keyword is not None,
            has_categories=bool(category_ids),
            multi_month_window=multi_month,
        )
    )

    do_compare = include_comparison if include_comparison is not None else inferred_compare
    comp_from: date | None = None
    comp_to: date | None = None
    if do_compare:
        comp_from, comp_to = previous_equal_window(date_from, date_to)

    assumptions = [date_assumption]
    if scope_assumption:
        assumptions.append(scope_assumption)
    if amount_assumption:
        assumptions.append(amount_assumption)
    if keyword:
        assumptions.append(f"关键词检索：匹配已脱敏后可见的账面名称或备注包含“{keyword}”")
    if "expense" in types and "refund" in types:
        assumptions.append("净支出按本币整数分计算：净支出 = 支出合计 - 退款合计（不含账户间转账）")
    if comp_from is not None and comp_to is not None:
        assumptions.append(f"对比基线：{comp_from.isoformat()} 至 {comp_to.isoformat()}")

    return LedgerQueryPlan(
        intent=intent,
        date_from=date_from,
        date_to=date_to,
        comparison_date_from=comp_from,
        comparison_date_to=comp_to,
        types=types,
        category_ids=category_ids,
        account_ids=account_ids,
        scope=scope,
        keyword=keyword,
        min_amount_cents=min_amount_cents,
        max_amount_cents=None,
        only_with_splits=only_with_splits,
        only_refunded=only_refunded and types != ["refund"],
        group_by=group_by,
        limit=200,
        assumptions=assumptions,
    )
