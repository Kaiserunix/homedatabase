"""Decoupled, pluggable bill-import parser, normalization & deduplication package.

Supports WeChat Pay (CSV/XLSX), Alipay modern & classic exports (CSV/XLSX, UTF-8/GB18030),
priority-ranked Bank CSV/TSV/XLSX statements, and OFX 1.x (SGML) / OFX 2.x (XML) / QFX files,
with 5-layer deduplication, category alias & payee-memory resolution, refund/transfer matching,
and drop-in compatibility with ``homedatabase_api.services.import_parsers``.
"""

from __future__ import annotations

from .alipay import AlipayBillParser
from .csv_parser import GenericTableBillParser
from .dedup import (
    DeduplicationConfig,
    DeduplicationContext,
    DeduplicationDecision,
    DeduplicationEngine,
    ExistingFileSnapshot,
    ExistingLedgerTransaction,
    ExistingSourceSnapshot,
    RefundLinkSuggestion,
    TransferPairSuggestion,
)
from .legacy_adapter import (
    parse_import_file_pluggable,
    to_legacy_parse_outcome,
    to_legacy_parsed_row,
)
from .models import (
    MAX_SAFE_MINOR,
    SHANGHAI_TZ,
    BillParserPlugin,
    DedupStatus,
    FormatSecurityError,
    ImportDocument,
    ImportLimitError,
    ImportParseResult,
    LedgerDirection,
    MinorAmountError,
    NormalizedBillRecord,
    NormalizedDirection,
    ParserLimits,
    ParseStatus,
    ProviderId,
)
from .normalizer import (
    DEFAULT_CATEGORY_ALIASES,
    CategoryResolver,
    canonicalize_payee,
    clean_cell_text,
    clean_external_id,
    combine_date_and_time,
    compute_semantic_fingerprint,
    compute_source_hash,
    normalize_to_utc,
    parse_currency_code,
    parse_minor_amount,
    parse_ofx_datetime,
    parse_statement_datetime,
)
from .ofx import OfxBillParser
from .pipeline import BatchImportPlan, BillImportPipeline, FileImportOutcome
from .registry import OcrPassthroughParser, ParserRegistry, create_default_registry
from .wechat import WeChatBillParser

__all__ = [
    "AlipayBillParser",
    "BatchImportPlan",
    "BillImportPipeline",
    "BillParserPlugin",
    "CategoryResolver",
    "DEFAULT_CATEGORY_ALIASES",
    "DedupStatus",
    "DeduplicationConfig",
    "DeduplicationContext",
    "DeduplicationDecision",
    "DeduplicationEngine",
    "ExistingFileSnapshot",
    "ExistingLedgerTransaction",
    "ExistingSourceSnapshot",
    "FileImportOutcome",
    "FormatSecurityError",
    "GenericTableBillParser",
    "ImportDocument",
    "ImportLimitError",
    "ImportParseResult",
    "LedgerDirection",
    "MAX_SAFE_MINOR",
    "MinorAmountError",
    "NormalizedBillRecord",
    "NormalizedDirection",
    "OcrPassthroughParser",
    "OfxBillParser",
    "ParseStatus",
    "ParserLimits",
    "ParserRegistry",
    "ProviderId",
    "RefundLinkSuggestion",
    "SHANGHAI_TZ",
    "TransferPairSuggestion",
    "WeChatBillParser",
    "canonicalize_payee",
    "clean_cell_text",
    "clean_external_id",
    "combine_date_and_time",
    "compute_semantic_fingerprint",
    "compute_source_hash",
    "create_default_registry",
    "normalize_to_utc",
    "parse_currency_code",
    "parse_import_file_pluggable",
    "parse_minor_amount",
    "parse_ofx_datetime",
    "parse_statement_datetime",
    "to_legacy_parse_outcome",
    "to_legacy_parsed_row",
]
