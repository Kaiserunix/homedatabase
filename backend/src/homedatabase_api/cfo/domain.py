"""Pure domain models, money invariants, and calendar primitives for the AI CFO engine.

This module has zero FastAPI, SQLAlchemy, or network dependencies so that all
financial calculations can be verified deterministically in isolation.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal
from zoneinfo import ZoneInfo

MAX_SAFE_MINOR = 2**53 - 1
BPS_SCALE = 10_000
SHANGHAI = ZoneInfo("Asia/Shanghai")

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

AccountKind = Literal["asset", "liability"]
AccountScope = Literal["personal", "family"]
CategoryKind = Literal["expense", "income"]
TransactionKind = Literal["expense", "income", "transfer", "refund"]
CashflowDirection = Literal["expense", "income"]
ObligationPriority = Literal["essential", "discretionary"]
SeverityLevel = Literal["info", "warning", "high"]


class CFODomainError(ValueError):
    """Base error for invalid CFO domain inputs or broken ledger invariants."""


class UnsafeMoneyError(CFODomainError):
    """Raised when a minor-unit amount violates integer or safe-range bounds."""


class UnbalancedLedgerError(CFODomainError):
    """Raised when double-entry journal lines do not sum to zero."""


def require_int_minor(value: int, *, field_name: str = "amount_minor") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnsafeMoneyError(f"{field_name} must be an exact integer minor unit")
    if value < -MAX_SAFE_MINOR or value > MAX_SAFE_MINOR:
        raise UnsafeMoneyError(
            f"{field_name}={value} exceeds safe minor-unit bounds (+/-{MAX_SAFE_MINOR})"
        )
    return value


def require_non_negative_minor(value: int, *, field_name: str = "amount_minor") -> int:
    checked = require_int_minor(value, field_name=field_name)
    if checked < 0:
        raise UnsafeMoneyError(f"{field_name} must be >= 0, got {checked}")
    return checked


def require_positive_minor(value: int, *, field_name: str = "amount_minor") -> int:
    checked = require_int_minor(value, field_name=field_name)
    if checked <= 0:
        raise UnsafeMoneyError(f"{field_name} must be > 0, got {checked}")
    return checked


def require_currency_code(code: str, *, field_name: str = "currency") -> str:
    if not isinstance(code, str) or not CURRENCY_RE.match(code):
        raise CFODomainError(f"{field_name} must be a 3-letter uppercase ISO code, got {code!r}")
    return code


def format_minor_units(amount_minor: int, *, minor_digits: int = 2) -> str:
    """Format exact integer minor units into human-readable decimal text."""
    require_int_minor(amount_minor, field_name="amount_minor")
    if minor_digits <= 0:
        return str(amount_minor)
    scale = 10**minor_digits
    sign = "-" if amount_minor < 0 else ""
    major, minor = divmod(abs(amount_minor), scale)
    return f"{sign}{major}.{minor:0{minor_digits}d}"


def ratio_to_bps(numerator: int, denominator: int) -> int | None:
    """Compute exact basis points (10000 = 100.00%) using half-up decimal rounding."""
    if denominator == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        raw = (Decimal(numerator) * Decimal(BPS_SCALE)) / Decimal(denominator)
        return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def scale_minor_by_bps(amount_minor: int, bps: int) -> int:
    """Multiply an integer minor-unit amount by basis points with half-up rounding."""
    require_int_minor(amount_minor, field_name="amount_minor")
    with localcontext() as ctx:
        ctx.prec = 50
        scaled = (Decimal(amount_minor) * Decimal(bps)) / Decimal(BPS_SCALE)
        result = int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return require_int_minor(result, field_name="scaled_amount_minor")


def safe_divide_minor(numerator: int, denominator: int) -> int:
    """Divide integer minor units with deterministic half-up rounding."""
    if denominator <= 0:
        raise CFODomainError("denominator must be positive")
    with localcontext() as ctx:
        ctx.prec = 50
        quotient = Decimal(numerator) / Decimal(denominator)
        result = int(quotient.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return require_int_minor(result, field_name="quotient_minor")


def to_shanghai_datetime(value: datetime) -> datetime:
    """Normalize any naive (assumed UTC per ORM contract) or aware datetime to Asia/Shanghai."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(SHANGHAI)


def to_shanghai_date(value: datetime) -> date:
    return to_shanghai_datetime(value).date()


@dataclass(frozen=True, order=True, slots=True)
class YearMonth:
    """Calendar month value object in household local time (Asia/Shanghai)."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if self.year < 1970 or self.year > 9999:
            raise CFODomainError(f"year out of supported range: {self.year}")
        if self.month < 1 or self.month > 12:
            raise CFODomainError(f"month must be 1..12, got {self.month}")

    @classmethod
    def parse(cls, value: str) -> YearMonth:
        parts = value.strip().split("-")
        if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
            raise CFODomainError(f"expected YYYY-MM month format, got {value!r}")
        try:
            return cls(int(parts[0]), int(parts[1]))
        except ValueError as exc:
            raise CFODomainError(f"expected YYYY-MM month format, got {value!r}") from exc

    @classmethod
    def from_date(cls, value: date) -> YearMonth:
        return cls(value.year, value.month)

    @classmethod
    def from_datetime(cls, value: datetime) -> YearMonth:
        local = to_shanghai_date(value)
        return cls(local.year, local.month)

    def add_months(self, delta: int) -> YearMonth:
        zero_based = (self.year * 12 + (self.month - 1)) + delta
        new_year, new_month_zero = divmod(zero_based, 12)
        return YearMonth(new_year, new_month_zero + 1)

    def days_in_month(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def starts_on(self) -> date:
        return date(self.year, self.month, 1)

    def ends_on(self) -> date:
        return date(self.year, self.month, self.days_in_month())

    def window_utc(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self.starts_on(), time.min, SHANGHAI).astimezone(UTC)
        end = datetime.combine(self.ends_on(), time.max, SHANGHAI).astimezone(UTC)
        return start, end

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Immutable point-in-time balance view of a visible ledger account.

    Note on HomeDatabase sign convention:
    - ``kind == "asset"``: positive ``balance_minor`` means positive cash/asset;
      negative means overdrawn asset.
    - ``kind == "liability"``: positive ``balance_minor`` means debt owed
      (credit balance increases liability in ``services/ledger.py``);
      negative means credit-card overpayment.
    """

    id: str
    name: str
    kind: AccountKind
    scope: AccountScope
    currency: str
    balance_minor: int
    included_in_liquid_cash: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise CFODomainError("AccountSnapshot.id must be non-empty")
        if self.kind not in ("asset", "liability"):
            raise CFODomainError(f"invalid account kind: {self.kind!r}")
        if self.scope not in ("personal", "family"):
            raise CFODomainError(f"invalid account scope: {self.scope!r}")
        require_currency_code(self.currency, field_name="account.currency")
        require_int_minor(self.balance_minor, field_name="account.balance_minor")


@dataclass(frozen=True, slots=True)
class CategorySnapshot:
    id: str
    name: str
    kind: CategoryKind
    parent_id: str | None = None
    essential: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise CFODomainError("CategorySnapshot.id must be non-empty")
        if self.kind not in ("expense", "income"):
            raise CFODomainError(f"invalid category kind: {self.kind!r}")


@dataclass(frozen=True, slots=True)
class TransactionSnapshot:
    """Immutable, permission-filtered ledger transaction for CFO analysis."""

    id: str
    occurred_at: datetime
    type: TransactionKind
    payee: str
    note: str = ""
    category_id: str | None = None
    currency: str = "CNY"
    amount_minor: int = 0
    base_currency: str = "CNY"
    base_amount_minor: int = 0
    account_ids: tuple[str, ...] = ()
    refund_of_id: str | None = None
    has_hidden_counterparty: bool = False
    split_reimbursed_minor: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise CFODomainError("TransactionSnapshot.id must be non-empty")
        if self.type not in ("expense", "income", "transfer", "refund"):
            raise CFODomainError(f"invalid transaction type: {self.type!r}")
        require_currency_code(self.currency, field_name="transaction.currency")
        require_currency_code(self.base_currency, field_name="transaction.base_currency")
        require_positive_minor(self.amount_minor, field_name="transaction.amount_minor")
        require_positive_minor(self.base_amount_minor, field_name="transaction.base_amount_minor")
        require_non_negative_minor(
            self.split_reimbursed_minor, field_name="transaction.split_reimbursed_minor"
        )
        if self.split_reimbursed_minor > self.base_amount_minor:
            raise CFODomainError(
                "split_reimbursed_minor cannot exceed transaction base_amount_minor"
            )

    @property
    def local_date(self) -> date:
        return to_shanghai_date(self.occurred_at)

    @property
    def year_month(self) -> YearMonth:
        return YearMonth.from_datetime(self.occurred_at)


@dataclass(frozen=True, slots=True)
class CategoryBudgetTarget:
    category_id: str
    monthly_limit_minor: int
    warning_threshold_bps: int = 8_000

    def __post_init__(self) -> None:
        if not self.category_id:
            raise CFODomainError("CategoryBudgetTarget.category_id must be non-empty")
        require_positive_minor(self.monthly_limit_minor, field_name="monthly_limit_minor")
        if not 1 <= self.warning_threshold_bps <= BPS_SCALE:
            raise CFODomainError("warning_threshold_bps must be between 1 and 10000")


@dataclass(frozen=True, slots=True)
class ScheduledCashflowItem:
    """Explicit user-confirmed upcoming inflow or outflow (e.g. tuition, bonus, insurance)."""

    id: str
    label: str
    direction: CashflowDirection
    amount_minor: int
    due_date: date
    category_id: str | None = None
    priority: ObligationPriority = "essential"

    def __post_init__(self) -> None:
        if not self.id or not self.label.strip():
            raise CFODomainError("ScheduledCashflowItem id and label must be non-empty")
        if self.direction not in ("expense", "income"):
            raise CFODomainError(f"invalid direction: {self.direction!r}")
        if self.priority not in ("essential", "discretionary"):
            raise CFODomainError(f"invalid priority: {self.priority!r}")
        require_positive_minor(self.amount_minor, field_name="scheduled.amount_minor")


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """Complete read-only analytical input snapshot for one authorized scope."""

    as_of: date
    base_currency: str = "CNY"
    accounts: tuple[AccountSnapshot, ...] = ()
    categories: tuple[CategorySnapshot, ...] = ()
    transactions: tuple[TransactionSnapshot, ...] = ()
    budgets: tuple[CategoryBudgetTarget, ...] = ()
    scheduled_items: tuple[ScheduledCashflowItem, ...] = ()
    viewer_member_id: str | None = None
    scope_label: str = "household"
    category_by_id: dict[str, CategorySnapshot] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        require_currency_code(self.base_currency, field_name="base_currency")
        for txn in self.transactions:
            if txn.base_currency != self.base_currency:
                raise CFODomainError(
                    f"transaction {txn.id} base_currency={txn.base_currency} "
                    f"does not match snapshot base_currency={self.base_currency}"
                )
        object.__setattr__(self, "category_by_id", {cat.id: cat for cat in self.categories})

    def category_name(self, category_id: str | None) -> str:
        if category_id is None:
            return "待分类"
        category = self.category_by_id.get(category_id)
        return category.name if category is not None else category_id
