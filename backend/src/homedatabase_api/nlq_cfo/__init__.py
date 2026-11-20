"""Standalone read-only Layer-3 'Ask Your Money' NLQ and household digest package."""

from __future__ import annotations

from .adapters import (
    DeterministicOfflineCFOAdapter,
    OpenAICompatibleTransport,
    PluggableLLMCFOAdapter,
)
from .parser import build_rule_based_plan
from .protocols import (
    AskNarrativeContext,
    CatalogContext,
    CategoryInfo,
    CFOAdapter,
    DigestNarrativeContext,
    LedgerReadRepository,
    LLMTransport,
    NarrativeDraft,
    VisibleAccountInfo,
    VisibleTransactionRecord,
)
from .repository import InMemoryLedgerReadRepository, SqlAlchemyLedgerReadRepository
from .router import create_nlq_router, get_cfo_adapter, mount_nlq_cfo, router
from .schemas import (
    AskQuestionInput,
    AskYourMoneyResponse,
    BreakdownItem,
    CfoCapabilitiesResponse,
    DigestQueryInput,
    HouseholdDigestResponse,
    LedgerQueryPlan,
    LedgerTotals,
    OpenSplitsSummary,
    PeriodComparison,
    RefundDigestSummary,
    TransactionCitation,
)
from .service import NLQCFOService

__all__ = [
    "AskNarrativeContext",
    "AskQuestionInput",
    "AskYourMoneyResponse",
    "BreakdownItem",
    "CFOAdapter",
    "CatalogContext",
    "CategoryInfo",
    "CfoCapabilitiesResponse",
    "DeterministicOfflineCFOAdapter",
    "DigestNarrativeContext",
    "DigestQueryInput",
    "HouseholdDigestResponse",
    "InMemoryLedgerReadRepository",
    "LLMTransport",
    "LedgerQueryPlan",
    "LedgerReadRepository",
    "LedgerTotals",
    "NLQCFOService",
    "NarrativeDraft",
    "OpenAICompatibleTransport",
    "OpenSplitsSummary",
    "PeriodComparison",
    "PluggableLLMCFOAdapter",
    "RefundDigestSummary",
    "SqlAlchemyLedgerReadRepository",
    "TransactionCitation",
    "VisibleAccountInfo",
    "VisibleTransactionRecord",
    "build_rule_based_plan",
    "create_nlq_router",
    "get_cfo_adapter",
    "mount_nlq_cfo",
    "router",
]
