from __future__ import annotations

import calendar
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

from ..config import Settings

CfoIntent = Literal[
    "overview",
    "spending_change",
    "frequency_vs_ticket",
    "financial_memory",
    "large_expenses",
]
GrowthDriver = Literal["frequency", "ticket_size", "mixed", "decreased", "flat", "none"]

_EXPLICIT_MONTH_RE = re.compile(r"(20\d{2})\s*[年\-/]\s*(0?[1-9]|1[0-2])\s*月?")
_EXPLICIT_YEAR_RE = re.compile(r"(20\d{2})\s*年")
_QUOTED_TOPIC_RE = re.compile(r"[“\"「『]([^”\"」』]{1,30})[”\"」』]")
_RELATED_TOPIC_RE = re.compile(
    r"(?:与|关于|有关)\s*([\u4e00-\u9fffA-Za-z0-9]{1,20}?)\s*(?:有关|相关|的开销|的支出|的账单|花费)"
)
_KNOWN_TOPIC_KEYWORDS = (
    "外卖",
    "装修",
    "房租",
    "订阅",
    "超市",
    "咖啡",
    "奶茶",
    "打车",
    "地铁",
    "公交",
    "水电",
    "燃气",
    "物业",
    "旅行",
    "旅游",
    "医院",
    "体检",
    "学费",
    "保险",
)


def format_minor_yuan(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    major, cents = divmod(abs(minor), 100)
    return f"{sign}{major}.{cents:02d}"


@dataclass(frozen=True, slots=True)
class CfoCategoryHint:
    id: str
    name: str
    kind: Literal["expense", "income"]
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class CfoAccountHint:
    id: str
    name: str
    scope: Literal["personal", "family"]
    currency: str


@dataclass(frozen=True, slots=True)
class CfoPlanningContext:
    as_of_date: date
    categories: list[CfoCategoryHint]
    accounts: list[CfoAccountHint]
    explicit_account_id: str | None = None
    explicit_scope: Literal["all", "personal", "family"] | None = None
    large_expense_threshold_cents: int = 50_000


@dataclass(slots=True)
class PlannedLedgerQuery:
    intent: CfoIntent
    starts_on: date
    ends_on: date
    comparison_starts_on: date | None
    comparison_ends_on: date | None
    scope: Literal["all", "personal", "family"]
    account_ids: list[str] = field(default_factory=list)
    category_ids: list[str] = field(default_factory=list)
    keyword: str | None = None
    large_expense_threshold_cents: int = 50_000
    assumptions: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CfoDriverFact:
    label: str
    kind: Literal["category", "payee"]
    current_cents: int
    previous_cents: int
    delta_cents: int
    transaction_count: int


@dataclass(frozen=True, slots=True)
class VerifiedCfoFacts:
    question: str
    intent: CfoIntent
    starts_on: date
    ends_on: date
    comparison_starts_on: date | None
    comparison_ends_on: date | None
    gross_expense_cents: int
    linked_refund_cents: int
    net_expense_cents: int
    split_reimbursed_cents: int
    own_burden_cents: int
    income_cents: int
    net_cashflow_cents: int
    expense_count: int
    average_expense_cents: int
    previous_net_expense_cents: int | None
    net_expense_delta_cents: int | None
    previous_expense_count: int | None
    previous_average_expense_cents: int | None
    primary_growth_driver: GrowthDriver
    top_changes: list[CfoDriverFact]
    citation_count: int


class CfoProvider(Protocol):
    """Pluggable household AI CFO query planner and verified-numbers narrator."""

    provider_id: str
    uses_external_model: bool

    def plan_query(
        self,
        question: str,
        *,
        context: CfoPlanningContext,
    ) -> PlannedLedgerQuery: ...

    def narrate(self, facts: VerifiedCfoFacts) -> str: ...


def _shift_months(year: int, month: int, delta: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + delta
    new_year, zero_month = divmod(total, 12)
    return new_year, zero_month + 1


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _expand_category_tree(
    matched_ids: set[str],
    categories: list[CfoCategoryHint],
) -> list[str]:
    expanded = set(matched_ids)
    for item in categories:
        if item.parent_id in matched_ids:
            expanded.add(item.id)
    return sorted(expanded)


class DeterministicRuleCfoProvider:
    """Zero-dependency local CFO planner and narrator with deterministic integer grounding."""

    provider_id = "deterministic-local"
    uses_external_model = False

    def plan_query(
        self,
        question: str,
        *,
        context: CfoPlanningContext,
    ) -> PlannedLedgerQuery:
        text = question.strip()
        lowered = text.casefold()
        assumptions: list[str] = []

        intent = self._detect_intent(text, lowered)
        starts_on, ends_on, comp_start, comp_end, time_note = self._resolve_window(
            text, lowered, context.as_of_date, intent
        )
        if time_note:
            assumptions.append(time_note)

        scope: Literal["all", "personal", "family"] = context.explicit_scope or "all"
        if context.explicit_scope is None:
            if any(word in text for word in ("共享", "公共", "家庭公共", "全家")):
                scope = "family"
                assumptions.append("仅统计家庭共享账户（scope=family）")
            elif any(word in text for word in ("个人账户", "私人账户", "我个人")):
                scope = "personal"
                assumptions.append("仅统计当前成员可见的个人账户（scope=personal）")

        account_ids: list[str] = []
        if context.explicit_account_id:
            account_ids = [context.explicit_account_id]
        else:
            matched_accounts = [
                acct.id for acct in context.accounts if acct.name and acct.name in text
            ]
            if matched_accounts:
                account_ids = sorted(set(matched_accounts))

        matched_cat_ids = {cat.id for cat in context.categories if cat.name and cat.name in text}
        category_ids = _expand_category_tree(matched_cat_ids, context.categories)

        keyword = self._extract_keyword(text, context.categories, matched_cat_ids)

        if intent == "large_expenses":
            threshold = format_minor_yuan(context.large_expense_threshold_cents)
            assumptions.append(f"“大额消费”按单笔折合基础币 ≥ {threshold} 元筛选")
        assumptions.append(
            "金额按家庭基础币 CNY 整数分确定性汇总；净支出已扣除原支出关联的有效退款，"
            "个人实担额扣除他人分摊份额"
        )

        return PlannedLedgerQuery(
            intent=intent,
            starts_on=starts_on,
            ends_on=ends_on,
            comparison_starts_on=comp_start,
            comparison_ends_on=comp_end,
            scope=scope,
            account_ids=account_ids,
            category_ids=category_ids,
            keyword=keyword,
            large_expense_threshold_cents=context.large_expense_threshold_cents,
            assumptions=assumptions,
        )

    def _detect_intent(self, text: str, lowered: str) -> CfoIntent:
        if any(
            token in text
            for token in ("次数还是客单价", "客单价", "频次", "单均", "平均每笔", "笔数")
        ) or ("frequency" in lowered or "ticket" in lowered):
            return "frequency_vs_ticket"
        if any(
            token in text for token in ("退款", "分摊", "自己承担", "实付", "实担", "有关", "相关")
        ):
            return "financial_memory"
        if any(token in text for token in ("大额", "最贵", "最大单笔", "排名前")):
            return "large_expenses"
        if any(
            token in text
            for token in ("为什么多花", "多花了", "变化最大", "上涨", "增长", "环比", "对比")
        ):
            return "spending_change"
        return "overview"

    def _resolve_window(
        self,
        text: str,
        lowered: str,
        as_of: date,
        intent: CfoIntent,
    ) -> tuple[date, date, date | None, date | None, str]:
        month_match = _EXPLICIT_MONTH_RE.search(text)
        if month_match:
            year = int(month_match.group(1))
            month = int(month_match.group(2))
            starts_on, ends_on = _month_bounds(year, month)
            prev_y, prev_m = _shift_months(year, month, -1)
            comp_start, comp_end = _month_bounds(prev_y, prev_m)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                (
                    f"查询期采用 {starts_on.isoformat()} 至 {ends_on.isoformat()}，"
                    f"对比基线为上一自然月 {comp_start.isoformat()} 至 {comp_end.isoformat()}"
                ),
            )

        if "去年" in text or "last year" in lowered:
            year = as_of.year - 1
            starts_on, ends_on = date(year, 1, 1), date(year, 12, 31)
            comp_start, comp_end = date(year - 1, 1, 1), date(year - 1, 12, 31)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                f"“去年”按上海时区自然年 {starts_on.isoformat()} 至 {ends_on.isoformat()} 统计",
            )

        if any(word in text for word in ("今年", "本年")) or "this year" in lowered:
            starts_on, ends_on = date(as_of.year, 1, 1), as_of
            span = (ends_on - starts_on).days + 1
            comp_end = starts_on - timedelta(days=1)
            comp_start = comp_end - timedelta(days=span - 1)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                f"“今年”按 {starts_on.isoformat()} 至基准日 {ends_on.isoformat()} 统计",
            )

        year_match = _EXPLICIT_YEAR_RE.search(text)
        if year_match:
            year = int(year_match.group(1))
            starts_on, ends_on = date(year, 1, 1), date(year, 12, 31)
            comp_start, comp_end = date(year - 1, 1, 1), date(year - 1, 12, 31)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                f"按自然年 {starts_on.isoformat()} 至 {ends_on.isoformat()} 统计",
            )

        if any(word in text for word in ("上个月", "上月")) or "last month" in lowered:
            prev_y, prev_m = _shift_months(as_of.year, as_of.month, -1)
            starts_on, ends_on = _month_bounds(prev_y, prev_m)
            comp_y, comp_m = _shift_months(prev_y, prev_m, -1)
            comp_start, comp_end = _month_bounds(comp_y, comp_m)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                (
                    f"“上个月”按上海时区上一完整自然月 "
                    f"{starts_on.isoformat()} 至 {ends_on.isoformat()} 统计"
                ),
            )

        if (
            any(
                word in text
                for word in ("过去三个月", "近三个月", "最近三个月", "过去3个月", "近3个月")
            )
            or "last 3 months" in lowered
        ):
            start_y, start_m = _shift_months(as_of.year, as_of.month, -2)
            starts_on = date(start_y, start_m, 1)
            ends_on = as_of
            comp_end = starts_on - timedelta(days=1)
            comp_start_y, comp_start_m = _shift_months(start_y, start_m, -3)
            comp_start = date(comp_start_y, comp_start_m, 1)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                (
                    f"“过去三个月”采用 {starts_on.isoformat()} 至 {ends_on.isoformat()}，"
                    f"对比前三个月 {comp_start.isoformat()} 至 {comp_end.isoformat()}"
                ),
            )

        if any(word in text for word in ("过去半年", "近半年", "近六个月", "过去6个月")):
            start_y, start_m = _shift_months(as_of.year, as_of.month, -5)
            starts_on = date(start_y, start_m, 1)
            ends_on = as_of
            comp_end = starts_on - timedelta(days=1)
            comp_start_y, comp_start_m = _shift_months(start_y, start_m, -6)
            comp_start = date(comp_start_y, comp_start_m, 1)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                f"“过去半年”采用 {starts_on.isoformat()} 至 {ends_on.isoformat()}",
            )

        if intent == "financial_memory" and not any(
            word in text for word in ("本月", "这个月", "当月")
        ):
            starts_on = as_of - timedelta(days=364)
            ends_on = as_of
            comp_end = starts_on - timedelta(days=1)
            comp_start = comp_end - timedelta(days=364)
            return (
                starts_on,
                ends_on,
                comp_start,
                comp_end,
                (
                    f"未指定具体月份，财务记忆检索最近 365 天"
                    f"（{starts_on.isoformat()} 至 {ends_on.isoformat()}）"
                ),
            )

        starts_on, month_end = _month_bounds(as_of.year, as_of.month)
        ends_on = min(as_of, month_end)
        prev_y, prev_m = _shift_months(as_of.year, as_of.month, -1)
        comp_start, comp_end = _month_bounds(prev_y, prev_m)
        return (
            starts_on,
            ends_on,
            comp_start,
            comp_end,
            (
                f"默认按当前自然月 {starts_on.isoformat()} 至 {ends_on.isoformat()} 统计，"
                f"对比上一自然月 {comp_start.isoformat()} 至 {comp_end.isoformat()}"
            ),
        )

    def _extract_keyword(
        self,
        text: str,
        categories: list[CfoCategoryHint],
        matched_category_ids: set[str],
    ) -> str | None:
        quoted = _QUOTED_TOPIC_RE.search(text)
        if quoted:
            candidate = quoted.group(1).strip()
            if candidate:
                return candidate

        related = _RELATED_TOPIC_RE.search(text)
        if related:
            candidate = related.group(1).strip()
            category_names = {cat.name for cat in categories if cat.id in matched_category_ids}
            if candidate and candidate not in category_names:
                return candidate

        if not matched_category_ids:
            for token in _KNOWN_TOPIC_KEYWORDS:
                if token in text:
                    return token
        return None

    def narrate(self, facts: VerifiedCfoFacts) -> str:
        period = f"{facts.starts_on.isoformat()} 至 {facts.ends_on.isoformat()}"
        if facts.citation_count == 0 and facts.income_cents == 0 and facts.gross_expense_cents == 0:
            return f"在 {period} 的可见账本范围内未检索到符合条件的交易记录（0 笔来源）。"

        if facts.intent == "financial_memory":
            return (
                f"{period} 共检索到 {facts.expense_count} 笔相关支出"
                f"（附 {facts.citation_count} 条可核对记录）："
                f"账面毛支出 {format_minor_yuan(facts.gross_expense_cents)} 元，"
                f"关联退款 {format_minor_yuan(facts.linked_refund_cents)} 元，"
                f"家庭净支出 {format_minor_yuan(facts.net_expense_cents)} 元，"
                f"由其他家庭成员分摊承担 {format_minor_yuan(facts.split_reimbursed_cents)} 元，"
                f"最终本人实际承担 {format_minor_yuan(facts.own_burden_cents)} 元。"
            )

        if facts.intent == "frequency_vs_ticket":
            driver_map = {
                "frequency": "上涨主要由消费次数增加驱动",
                "ticket_size": "上涨主要由单笔客单价上升驱动",
                "mixed": "消费次数与单笔客单价共同推动上涨",
                "decreased": "本期净支出较对比期回落",
                "flat": "本期净支出与对比期持平",
                "none": "对比期暂无基线记录，仅展示本期水平",
            }
            comp_clause = ""
            if (
                facts.previous_net_expense_cents is not None
                and facts.previous_expense_count is not None
                and facts.previous_average_expense_cents is not None
            ):
                prev_net = format_minor_yuan(facts.previous_net_expense_cents)
                prev_avg = format_minor_yuan(facts.previous_average_expense_cents)
                comp_clause = (
                    f"对比基线期净支出 {prev_net} 元"
                    f"（{facts.previous_expense_count} 笔，客单价 {prev_avg} 元），"
                )
            net_text = format_minor_yuan(facts.net_expense_cents)
            avg_text = format_minor_yuan(facts.average_expense_cents)
            return (
                f"{period} 共发生 {facts.expense_count} 笔支出，净支出 {net_text} 元，"
                f"平均客单价 {avg_text} 元。"
                f"{comp_clause}{driver_map[facts.primary_growth_driver]}。"
            )

        if facts.intent == "spending_change":
            delta_text = ""
            if facts.net_expense_delta_cents is not None:
                direction = "增加" if facts.net_expense_delta_cents >= 0 else "减少"
                abs_delta = format_minor_yuan(abs(facts.net_expense_delta_cents))
                delta_text = f"，较对比期{direction} {abs_delta} 元"
            drivers_text = ""
            if facts.top_changes:
                parts = [
                    (
                        f"{item.label}（净支出 {format_minor_yuan(item.current_cents)} 元，"
                        f"变动 {format_minor_yuan(item.delta_cents)} 元）"
                    )
                    for item in facts.top_changes[:3]
                ]
                drivers_text = "；变化最大的三项依次为：" + "、".join(parts)
            return (
                f"{period} 净支出 {format_minor_yuan(facts.net_expense_cents)} 元{delta_text}"
                f"{drivers_text}。共附 {facts.citation_count} 条原始记录供展开核对。"
            )

        if facts.intent == "large_expenses":
            return (
                f"{period} 共筛选出 {facts.expense_count} 笔大额支出，"
                f"累计毛支出 {format_minor_yuan(facts.gross_expense_cents)} 元、"
                f"净支出 {format_minor_yuan(facts.net_expense_cents)} 元"
                f"（本人实担 {format_minor_yuan(facts.own_burden_cents)} 元），已附上对应账单来源。"
            )

        gross_text = format_minor_yuan(facts.gross_expense_cents)
        refund_text = format_minor_yuan(facts.linked_refund_cents)
        return (
            f"{period} 可见账本共记录收入 {format_minor_yuan(facts.income_cents)} 元、"
            f"净支出 {format_minor_yuan(facts.net_expense_cents)} 元"
            f"（毛支出 {gross_text} 元，关联退款 {refund_text} 元），"
            f"净结余 {format_minor_yuan(facts.net_cashflow_cents)} 元，"
            f"共引用 {facts.citation_count} 条账目。"
        )


class OpenAiCompatibleCfoProvider:
    """Optional OpenAI-compatible planner/narrator; numbers remain deterministically computed."""

    provider_id = "openai-compatible"
    uses_external_model = True

    def __init__(
        self,
        *,
        settings: Settings,
        fallback: DeterministicRuleCfoProvider | None = None,
        transport_fn: Callable[[str, dict[str, object], str, int], dict[str, object]] | None = None,
    ) -> None:
        self._settings = settings
        self._fallback = fallback or DeterministicRuleCfoProvider()
        self._transport = transport_fn or _post_cfo_json

    def plan_query(
        self,
        question: str,
        *,
        context: CfoPlanningContext,
    ) -> PlannedLedgerQuery:
        baseline = self._fallback.plan_query(question, context=context)
        base_url = (self._settings.cfo_api_base_url or "").strip().rstrip("/")
        api_key = (self._settings.cfo_api_key or "").strip()
        if not base_url or not api_key:
            return baseline

        allowed_categories = {item.id for item in context.categories}
        allowed_accounts = {item.id for item in context.accounts}
        system_prompt = (
            "你是家庭账本查询规划器。将用户问题映射为有边界的只读查询 JSON。"
            "严禁编造金额，只能从给定分类与账户列表中选择 ID。"
        )
        user_payload = {
            "question": question,
            "asOfDate": context.as_of_date.isoformat(),
            "categories": [
                {"id": c.id, "name": c.name, "kind": c.kind} for c in context.categories
            ],
            "accounts": [{"id": a.id, "name": a.name, "scope": a.scope} for a in context.accounts],
        }
        try:
            response = self._transport(
                f"{base_url}/chat/completions",
                {
                    "model": self._settings.cfo_model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                    ],
                },
                api_key,
                self._settings.cfo_timeout_seconds,
            )
            parsed = json.loads(_extract_chat_text(response))
        except Exception:
            baseline.assumptions.append("远端语义规划不可用，已自动回退为本地确定性规则解析")
            return baseline

        if not isinstance(parsed, dict):
            return baseline
        intent = parsed.get("intent")
        if intent in {
            "overview",
            "spending_change",
            "frequency_vs_ticket",
            "financial_memory",
            "large_expenses",
        }:
            baseline.intent = intent
        try:
            if isinstance(parsed.get("startsOn"), str) and isinstance(parsed.get("endsOn"), str):
                s_on = date.fromisoformat(parsed["startsOn"])
                e_on = date.fromisoformat(parsed["endsOn"])
                if s_on <= e_on:
                    baseline.starts_on = s_on
                    baseline.ends_on = e_on
        except ValueError:
            pass
        if isinstance(parsed.get("categoryIds"), list) and not context.explicit_account_id:
            valid_cats = [
                str(cid) for cid in parsed["categoryIds"] if str(cid) in allowed_categories
            ]
            if valid_cats:
                baseline.category_ids = _expand_category_tree(set(valid_cats), context.categories)
        if isinstance(parsed.get("accountIds"), list) and not context.explicit_account_id:
            valid_accts = [str(aid) for aid in parsed["accountIds"] if str(aid) in allowed_accounts]
            if valid_accts:
                baseline.account_ids = sorted(set(valid_accts))
        if isinstance(parsed.get("keyword"), str) and parsed["keyword"].strip():
            baseline.keyword = parsed["keyword"].strip()[:80]
        return baseline

    def narrate(self, facts: VerifiedCfoFacts) -> str:
        deterministic_answer = self._fallback.narrate(facts)
        base_url = (self._settings.cfo_api_base_url or "").strip().rstrip("/")
        api_key = (self._settings.cfo_api_key or "").strip()
        if not base_url or not api_key:
            return deterministic_answer

        facts_payload = {
            "question": facts.question,
            "intent": facts.intent,
            "period": f"{facts.starts_on.isoformat()}..{facts.ends_on.isoformat()}",
            "grossExpenseYuan": format_minor_yuan(facts.gross_expense_cents),
            "linkedRefundYuan": format_minor_yuan(facts.linked_refund_cents),
            "netExpenseYuan": format_minor_yuan(facts.net_expense_cents),
            "splitReimbursedYuan": format_minor_yuan(facts.split_reimbursed_cents),
            "ownBurdenYuan": format_minor_yuan(facts.own_burden_cents),
            "incomeYuan": format_minor_yuan(facts.income_cents),
            "expenseCount": facts.expense_count,
            "averageExpenseYuan": format_minor_yuan(facts.average_expense_cents),
            "primaryGrowthDriver": facts.primary_growth_driver,
            "citationCount": facts.citation_count,
        }
        try:
            response = self._transport(
                f"{base_url}/chat/completions",
                {
                    "model": self._settings.cfo_model,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是家庭 AI CFO。只能复述给定 JSON 中已经核实的金额与笔数，"
                                "严禁自行心算或修改任何数字，用简洁中文在 150 字内回答。"
                            ),
                        },
                        {"role": "user", "content": json.dumps(facts_payload, ensure_ascii=False)},
                    ],
                },
                api_key,
                self._settings.cfo_timeout_seconds,
            )
            text = _extract_chat_text(response).strip()
            if text and format_minor_yuan(facts.net_expense_cents) in text:
                return text[:1000]
        except Exception:
            pass
        return deterministic_answer


def _post_cfo_json(
    url: str,
    body: dict[str, object],
    api_key: str,
    timeout_seconds: int,
) -> dict[str, object]:
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ValueError("CFO 远端模型服务必须使用 HTTPS（本机回环地址除外）")
    req = urllib_request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read(512 * 1024)
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("CFO 模型接口请求失败") from exc
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("invalid response")
    return decoded


def _extract_chat_text(response: dict[str, object]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("invalid choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("invalid message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("invalid content")
    return content


_CUSTOM_CFO_PROVIDERS: dict[str, CfoProvider] = {}


def register_cfo_provider(provider: CfoProvider) -> None:
    """Register a custom CfoProvider plugin for custom LLMs or deterministic test doubles."""
    _CUSTOM_CFO_PROVIDERS[provider.provider_id] = provider


def get_cfo_provider(settings: Settings) -> CfoProvider:
    custom = _CUSTOM_CFO_PROVIDERS.get(settings.cfo_provider)
    if custom is not None:
        return custom
    if settings.cfo_provider == "openai-compatible":
        return OpenAiCompatibleCfoProvider(settings=settings)
    return DeterministicRuleCfoProvider()
