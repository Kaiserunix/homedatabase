from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .dedup import (
    DeduplicationContext,
    DeduplicationDecision,
    DeduplicationEngine,
    ExistingFileSnapshot,
    RefundLinkSuggestion,
    TransferPairSuggestion,
)
from .models import ImportDocument, ImportParseResult, NormalizedBillRecord, ParserLimits
from .normalizer import CategoryResolver
from .registry import ParserRegistry, create_default_registry


@dataclass(slots=True)
class FileImportOutcome:
    filename: str
    sha256: str
    duplicate_of_file_id: str | None
    parse_result: ImportParseResult
    decisions: list[DeduplicationDecision] = field(default_factory=list)

    @property
    def effective_status(self) -> str:
        if self.duplicate_of_file_id is not None:
            return "duplicate"
        return self.parse_result.status


@dataclass(slots=True)
class BatchImportPlan:
    files: list[FileImportOutcome]
    refund_suggestions: list[RefundLinkSuggestion] = field(default_factory=list)
    transfer_pairs: list[TransferPairSuggestion] = field(default_factory=list)

    @property
    def all_decisions(self) -> list[DeduplicationDecision]:
        return [decision for item in self.files for decision in item.decisions]

    @property
    def unique_candidates(self) -> list[NormalizedBillRecord]:
        return [
            decision.record
            for decision in self.all_decisions
            if decision.record.is_ledger_candidate and not decision.is_duplicate
        ]

    @property
    def flagged_duplicates(self) -> list[DeduplicationDecision]:
        return [
            decision
            for decision in self.all_decisions
            if decision.record.is_ledger_candidate and decision.is_duplicate
        ]


class BillImportPipeline:
    """End-to-end orchestrator decoupling parsing, category resolution, and deduplication."""

    def __init__(
        self,
        *,
        registry: ParserRegistry | None = None,
        category_resolver: CategoryResolver | None = None,
        dedup_engine: DeduplicationEngine | None = None,
        limits: ParserLimits | None = None,
    ) -> None:
        self._registry = registry or create_default_registry()
        self._categories = category_resolver or CategoryResolver()
        self._dedup = dedup_engine or DeduplicationEngine()
        self._limits = limits or ParserLimits()

    @property
    def registry(self) -> ParserRegistry:
        return self._registry

    def run_batch(
        self,
        documents: Sequence[ImportDocument],
        *,
        context: DeduplicationContext | None = None,
    ) -> BatchImportPlan:
        dedup_ctx = context or DeduplicationContext()
        seen_upload_files: list[ExistingFileSnapshot] = list(dedup_ctx.known_files)

        parsed_files: list[tuple[ImportDocument, str | None, ImportParseResult]] = []
        per_file_records: list[list[NormalizedBillRecord]] = []
        flat_records: list[NormalizedBillRecord] = []
        file_slices: list[tuple[int, int]] = []

        for idx, doc in enumerate(documents):
            prior_file = next(
                (f for f in seen_upload_files if f.sha256 == doc.sha256),
                None,
            )
            if prior_file is not None:
                dup_result = ImportParseResult(
                    provider="unknown",
                    status="rejected",
                    message="与已上传源文件哈希完全相同，已阻止重复解析",
                )
                parsed_files.append((doc, prior_file.file_id, dup_result))
                per_file_records.append([])
                file_slices.append((len(flat_records), len(flat_records)))
                continue

            seen_upload_files.append(
                ExistingFileSnapshot(
                    file_id=f"batch-file-{idx + 1}",
                    sha256=doc.sha256,
                    filename=doc.filename,
                )
            )
            result = self._registry.parse(doc, limits=self._limits)
            if result.status == "parsed" and result.records:
                result.records = self._categories.enrich_records(result.records)
                start = len(flat_records)
                flat_records.extend(result.records)
                end = len(flat_records)
                per_file_records.append(result.records)
                file_slices.append((start, end))
            else:
                per_file_records.append([])
                file_slices.append((len(flat_records), len(flat_records)))
            parsed_files.append((doc, None, result))

        flat_decisions = self._dedup.evaluate_records(flat_records, dedup_ctx)
        outcomes: list[FileImportOutcome] = []
        for (doc, dup_file_id, result), (start, end) in zip(parsed_files, file_slices, strict=True):
            outcomes.append(
                FileImportOutcome(
                    filename=doc.filename,
                    sha256=doc.sha256,
                    duplicate_of_file_id=dup_file_id,
                    parse_result=result,
                    decisions=flat_decisions[start:end],
                )
            )

        refund_links = self._dedup.match_refunds(flat_records, dedup_ctx)
        transfer_pairs = self._dedup.match_transfer_pairs(per_file_records)

        return BatchImportPlan(
            files=outcomes,
            refund_suggestions=refund_links,
            transfer_pairs=transfer_pairs,
        )
