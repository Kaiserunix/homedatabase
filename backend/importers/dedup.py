from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from .models import DedupStatus, NormalizedBillRecord, ProviderId
from .normalizer import canonicalize_payee, normalize_to_utc


@dataclass(slots=True, frozen=True)
class ExistingFileSnapshot:
    file_id: str
    sha256: str
    filename: str | None = None


@dataclass(slots=True, frozen=True)
class ExistingSourceSnapshot:
    source_record_id: str
    source_hash: str
    provider: ProviderId | str | None = None
    external_id: str | None = None
    linked_transaction_id: str | None = None


@dataclass(slots=True, frozen=True)
class ExistingLedgerTransaction:
    transaction_id: str
    occurred_at: datetime
    direction: Literal["expense", "income"]
    amount_minor: int
    currency: str
    payee: str
    merchant_order_id: str | None = None

    @property
    def occurred_at_utc(self) -> datetime:
        return normalize_to_utc(self.occurred_at)

    @property
    def canonical_payee(self) -> str:
        return canonicalize_payee(self.payee)


@dataclass(slots=True, frozen=True)
class DeduplicationDecision:
    record: NormalizedBillRecord
    status: DedupStatus
    duplicate_of_txn_id: str | None = None
    duplicate_of_source_id: str | None = None
    duplicate_of_batch_ref: str | None = None
    similarity: Decimal = Decimal("0.00")
    reason: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.status != "unique"

    @property
    def legacy_validation_status(self) -> str:
        """Map rich dedup status onto HomeDatabase ImportCandidate.validation_status."""
        if self.status in {"exact_ledger_duplicate"}:
            return "exact_duplicate"
        if self.status in {"external_id_duplicate", "duplicate_source_row"}:
            return "exact_duplicate" if self.duplicate_of_txn_id else "duplicate_source_record"
        if self.status == "batch_internal_duplicate":
            return "duplicate_source_record"
        if self.status == "near_ledger_duplicate":
            return "near_duplicate"
        return "valid"


@dataclass(slots=True, frozen=True)
class RefundLinkSuggestion:
    refund_source_ref: str
    refund_amount_minor: int
    matched_transaction_id: str | None
    matched_batch_source_ref: str | None
    match_basis: Literal["merchant_order_id", "payee_and_window"]
    confidence: Decimal


@dataclass(slots=True, frozen=True)
class TransferPairSuggestion:
    outflow_file_index: int
    outflow_source_ref: str
    inflow_file_index: int
    inflow_source_ref: str
    amount_minor: int
    currency: str
    time_delta_seconds: int
    confidence: Decimal


@dataclass(slots=True)
class DeduplicationContext:
    known_files: Sequence[ExistingFileSnapshot] = field(default_factory=tuple)
    known_sources: Sequence[ExistingSourceSnapshot] = field(default_factory=tuple)
    ledger_transactions: Sequence[ExistingLedgerTransaction] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class DeduplicationConfig:
    near_duplicate_window: timedelta = timedelta(minutes=5)
    refund_lookback_window: timedelta = timedelta(days=90)
    transfer_pair_window: timedelta = timedelta(hours=24)
    enable_near_duplicate: bool = True


_WALLET_GATEWAY_HINTS = ("财付通", "微信", "支付宝", "alipay", "wechat", "tenpay")


class DeduplicationEngine:
    """Multi-layer deterministic & cross-channel deduplication + refund/transfer matcher."""

    def __init__(self, config: DeduplicationConfig | None = None) -> None:
        self._config = config or DeduplicationConfig()

    def check_file_duplicate(
        self,
        sha256: str,
        context: DeduplicationContext,
    ) -> ExistingFileSnapshot | None:
        for existing in context.known_files:
            if existing.sha256 == sha256:
                return existing
        return None

    def evaluate_records(
        self,
        records: Iterable[NormalizedBillRecord],
        context: DeduplicationContext,
    ) -> list[DeduplicationDecision]:
        # Index prior source records by source_hash and by (provider, external_id)
        hash_index: dict[str, ExistingSourceSnapshot] = {}
        ext_id_index: dict[tuple[str, str], ExistingSourceSnapshot] = {}
        for src in context.known_sources:
            if src.source_hash not in hash_index or src.linked_transaction_id:
                hash_index[src.source_hash] = src
            if src.external_id and src.provider:
                key = (str(src.provider).casefold(), src.external_id)
                if key not in ext_id_index or src.linked_transaction_id:
                    ext_id_index[key] = src

        # Index active ledger transactions by (direction, amount_minor, currency)
        ledger_buckets: dict[tuple[str, int, str], list[ExistingLedgerTransaction]] = {}
        for txn in context.ledger_transactions:
            bucket_key = (txn.direction, txn.amount_minor, txn.currency.upper())
            ledger_buckets.setdefault(bucket_key, []).append(txn)

        seen_batch_ext_ids: dict[tuple[str, str], str] = {}
        seen_batch_hashes: dict[str, str] = {}
        seen_batch_candidates: dict[tuple[str, int, str], list[NormalizedBillRecord]] = {}

        decisions: list[DeduplicationDecision] = []
        for record in records:
            decision = self._evaluate_one(
                record,
                hash_index=hash_index,
                ext_id_index=ext_id_index,
                ledger_buckets=ledger_buckets,
                seen_batch_ext_ids=seen_batch_ext_ids,
                seen_batch_hashes=seen_batch_hashes,
                seen_batch_candidates=seen_batch_candidates,
            )
            decisions.append(decision)

            # Register non-ignored records into batch memory so subsequent rows/files dedup
            if not record.is_ignored:
                if record.external_id:
                    ext_key = (record.provider.casefold(), record.external_id)
                    seen_batch_ext_ids.setdefault(ext_key, record.source_ref)
                seen_batch_hashes.setdefault(record.source_hash, record.source_ref)
                if record.is_ledger_candidate and record.direction in {"expense", "income"}:
                    cand_key = (
                        record.direction,
                        record.amount_minor or 0,
                        record.currency.upper(),
                    )
                    seen_batch_candidates.setdefault(cand_key, []).append(record)

        return decisions

    def _evaluate_one(
        self,
        record: NormalizedBillRecord,
        *,
        hash_index: dict[str, ExistingSourceSnapshot],
        ext_id_index: dict[tuple[str, str], ExistingSourceSnapshot],
        ledger_buckets: dict[tuple[str, int, str], list[ExistingLedgerTransaction]],
        seen_batch_ext_ids: dict[tuple[str, str], str],
        seen_batch_hashes: dict[str, str],
        seen_batch_candidates: dict[tuple[str, int, str], list[NormalizedBillRecord]],
    ) -> DeduplicationDecision:
        # Layer 2a: Prior external transaction ID match (WeChat / Alipay / OFX FITID)
        if record.external_id:
            ext_key = (record.provider.casefold(), record.external_id)
            if ext_key in ext_id_index:
                prior_src = ext_id_index[ext_key]
                return DeduplicationDecision(
                    record=record,
                    status="external_id_duplicate",
                    duplicate_of_txn_id=prior_src.linked_transaction_id,
                    duplicate_of_source_id=prior_src.source_record_id,
                    similarity=Decimal("1.00"),
                    reason=f"平台唯一单号 {record.external_id} 已导入过",
                )
            if ext_key in seen_batch_ext_ids:
                return DeduplicationDecision(
                    record=record,
                    status="batch_internal_duplicate",
                    duplicate_of_batch_ref=seen_batch_ext_ids[ext_key],
                    similarity=Decimal("1.00"),
                    reason=f"当前批次内重复出现单号 {record.external_id}",
                )

        # Layer 3: Canonical raw-row hash match
        if record.source_hash in hash_index:
            prior_src = hash_index[record.source_hash]
            return DeduplicationDecision(
                record=record,
                status="duplicate_source_row",
                duplicate_of_txn_id=prior_src.linked_transaction_id,
                duplicate_of_source_id=prior_src.source_record_id,
                similarity=Decimal("1.00"),
                reason="与历史导入的原始账单行完全相同",
            )
        if record.source_hash in seen_batch_hashes:
            return DeduplicationDecision(
                record=record,
                status="batch_internal_duplicate",
                duplicate_of_batch_ref=seen_batch_hashes[record.source_hash],
                similarity=Decimal("1.00"),
                reason="当前批次内包含完全相同的原始账单行",
            )

        if not record.is_ledger_candidate or record.direction not in {"expense", "income"}:
            return DeduplicationDecision(record=record, status="unique")

        rec_utc = record.occurred_at_utc
        assert rec_utc is not None
        assert record.amount_minor is not None
        bucket_key = (record.direction, record.amount_minor, record.currency.upper())
        candidates_in_ledger = ledger_buckets.get(bucket_key, ())

        # Layer 4: Exact ledger transaction match (instant + amount + currency + direction + payee)
        for txn in candidates_in_ledger:
            if txn.occurred_at_utc == rec_utc and (
                txn.payee == record.payee
                or (txn.canonical_payee and txn.canonical_payee == record.canonical_payee)
            ):
                return DeduplicationDecision(
                    record=record,
                    status="exact_ledger_duplicate",
                    duplicate_of_txn_id=txn.transaction_id,
                    similarity=Decimal("1.00"),
                    reason="账本中已存在同一时间、同金额、同商户的账目",
                )

        if not self._config.enable_near_duplicate:
            return DeduplicationDecision(record=record, status="unique")

        # Layer 5a: Cross-channel near-duplicate against active ledger transactions
        window = self._config.near_duplicate_window
        for txn in candidates_in_ledger:
            delta = abs(txn.occurred_at_utc - rec_utc)
            if delta <= window and self._is_cross_channel_payee_match(
                record_payee=record.payee or "",
                record_canon=record.canonical_payee,
                record_payment_method=record.payment_method,
                other_payee=txn.payee,
                other_canon=txn.canonical_payee,
            ):
                return DeduplicationDecision(
                    record=record,
                    status="near_ledger_duplicate",
                    duplicate_of_txn_id=txn.transaction_id,
                    similarity=Decimal("0.92"),
                    reason=(
                        f"疑似跨渠道重复记账：与账目 {txn.transaction_id} 金额相同且时间相差 "
                        f"{int(delta.total_seconds())} 秒"
                    ),
                )

        # Layer 5b: Cross-file near-duplicate within the same multi-file batch
        # (e.g. uploading WeChat CSV + Bank Card CSV together)
        for prior in seen_batch_candidates.get(bucket_key, ()):
            prior_utc = prior.occurred_at_utc
            if prior_utc is None or prior.provider == record.provider:
                continue
            delta = abs(prior_utc - rec_utc)
            if delta <= window and self._is_cross_channel_payee_match(
                record_payee=record.payee or "",
                record_canon=record.canonical_payee,
                record_payment_method=record.payment_method,
                other_payee=prior.payee or "",
                other_canon=prior.canonical_payee,
                other_payment_method=prior.payment_method,
            ):
                return DeduplicationDecision(
                    record=record,
                    status="near_ledger_duplicate",
                    duplicate_of_batch_ref=prior.source_ref,
                    similarity=Decimal("0.90"),
                    reason=(
                        f"疑似与同批文件记录 {prior.source_ref} 跨渠道重复（如微信/支付宝绑卡扣款）"
                    ),
                )

        return DeduplicationDecision(record=record, status="unique")

    @staticmethod
    def _is_cross_channel_payee_match(
        *,
        record_payee: str,
        record_canon: str,
        record_payment_method: str | None,
        other_payee: str,
        other_canon: str,
        other_payment_method: str | None = None,
    ) -> bool:
        if record_canon and other_canon:
            if record_canon == other_canon:
                return True
            if (
                len(record_canon) >= 2
                and len(other_canon) >= 2
                and (record_canon in other_canon or other_canon in record_canon)
            ):
                return True

        # Bank CSV often records only `"财付通"` or `"支付宝"` as counterparty while WeChat/Alipay
        # has the real merchant name + `payment_method="招商银行储蓄卡(1234)"`!
        raw_pair = f"{record_payee} {other_payee}".casefold()
        has_gateway_label = any(hint in raw_pair for hint in _WALLET_GATEWAY_HINTS)
        has_card_method = bool(record_payment_method or other_payment_method)
        if (
            has_gateway_label
            and has_card_method
            and (record_canon in _WALLET_GATEWAY_HINTS or other_canon in _WALLET_GATEWAY_HINTS)
        ):
            return True
        return False

    def match_refunds(
        self,
        records: Sequence[NormalizedBillRecord],
        context: DeduplicationContext,
    ) -> list[RefundLinkSuggestion]:
        """Link refund rows to their original expense in the ledger or current batch."""
        batch_expenses = [
            rec
            for rec in records
            if rec.direction == "expense"
            and rec.amount_minor is not None
            and rec.occurred_at_utc is not None
        ]
        ledger_expenses = [txn for txn in context.ledger_transactions if txn.direction == "expense"]
        lookback = self._config.refund_lookback_window
        suggestions: list[RefundLinkSuggestion] = []

        for rec in records:
            if rec.direction != "refund" or not rec.amount_minor:
                continue
            refund_utc = rec.occurred_at_utc

            # 1. Exact merchant_order_id match
            if rec.merchant_order_id:
                matched_ledger = next(
                    (
                        txn
                        for txn in ledger_expenses
                        if txn.merchant_order_id == rec.merchant_order_id
                        and rec.amount_minor <= txn.amount_minor
                    ),
                    None,
                )
                if matched_ledger is not None:
                    suggestions.append(
                        RefundLinkSuggestion(
                            refund_source_ref=rec.source_ref,
                            refund_amount_minor=rec.amount_minor,
                            matched_transaction_id=matched_ledger.transaction_id,
                            matched_batch_source_ref=None,
                            match_basis="merchant_order_id",
                            confidence=Decimal("0.98"),
                        )
                    )
                    continue

                matched_batch = next(
                    (
                        exp
                        for exp in batch_expenses
                        if exp.merchant_order_id == rec.merchant_order_id
                        and exp.amount_minor is not None
                        and rec.amount_minor <= exp.amount_minor
                    ),
                    None,
                )
                if matched_batch is not None:
                    suggestions.append(
                        RefundLinkSuggestion(
                            refund_source_ref=rec.source_ref,
                            refund_amount_minor=rec.amount_minor,
                            matched_transaction_id=None,
                            matched_batch_source_ref=matched_batch.source_ref,
                            match_basis="merchant_order_id",
                            confidence=Decimal("0.97"),
                        )
                    )
                    continue

            # 2. Canonical payee + currency + amount <= original + within lookback window
            if refund_utc is None or not rec.canonical_payee:
                continue

            best_ledger: ExistingLedgerTransaction | None = None
            for txn in ledger_expenses:
                if (
                    txn.currency.upper() != rec.currency.upper()
                    or rec.amount_minor > txn.amount_minor
                ):
                    continue
                delta = refund_utc - txn.occurred_at_utc
                if timedelta(0) <= delta <= lookback and self._payee_compatible(
                    rec.canonical_payee, txn.canonical_payee
                ):
                    if best_ledger is None or (
                        txn.amount_minor == rec.amount_minor
                        and best_ledger.amount_minor != rec.amount_minor
                    ):
                        best_ledger = txn
            if best_ledger is not None:
                suggestions.append(
                    RefundLinkSuggestion(
                        refund_source_ref=rec.source_ref,
                        refund_amount_minor=rec.amount_minor,
                        matched_transaction_id=best_ledger.transaction_id,
                        matched_batch_source_ref=None,
                        match_basis="payee_and_window",
                        confidence=Decimal("0.85"),
                    )
                )
                continue

            for exp in batch_expenses:
                exp_utc = exp.occurred_at_utc
                if (
                    exp_utc is None
                    or exp.amount_minor is None
                    or exp.currency.upper() != rec.currency.upper()
                    or rec.amount_minor > exp.amount_minor
                ):
                    continue
                delta = refund_utc - exp_utc
                if timedelta(0) <= delta <= lookback and self._payee_compatible(
                    rec.canonical_payee, exp.canonical_payee
                ):
                    suggestions.append(
                        RefundLinkSuggestion(
                            refund_source_ref=rec.source_ref,
                            refund_amount_minor=rec.amount_minor,
                            matched_transaction_id=None,
                            matched_batch_source_ref=exp.source_ref,
                            match_basis="payee_and_window",
                            confidence=Decimal("0.82"),
                        )
                    )
                    break

        return suggestions

    def match_transfer_pairs(
        self,
        per_file_records: Sequence[Sequence[NormalizedBillRecord]],
    ) -> list[TransferPairSuggestion]:
        """Identify complementary transfer legs across distinct statement files."""
        if len(per_file_records) < 2:
            return []
        window = self._config.transfer_pair_window
        used_refs: set[tuple[int, str]] = set()
        pairs: list[TransferPairSuggestion] = []

        outflows: list[tuple[int, NormalizedBillRecord]] = []
        inflows: list[tuple[int, NormalizedBillRecord]] = []
        for file_idx, file_rows in enumerate(per_file_records):
            for rec in file_rows:
                if rec.occurred_at_utc is None or not rec.amount_minor:
                    continue
                desc = f"{rec.payee or ''} {rec.item_summary or ''} {rec.original_category or ''}"
                if rec.direction == "expense" or (
                    rec.direction == "transfer"
                    and any(k in desc for k in ("提现", "转出", "还款", "转入零钱通"))
                ):
                    outflows.append((file_idx, rec))
                if rec.direction == "income" or (
                    rec.direction == "transfer"
                    and any(k in desc for k in ("充值", "存入", "提现", "还款"))
                ):
                    inflows.append((file_idx, rec))

        for out_idx, out_rec in outflows:
            if (out_idx, out_rec.source_ref) in used_refs:
                continue
            out_utc = out_rec.occurred_at_utc
            assert out_utc is not None
            assert out_rec.amount_minor is not None
            for in_idx, in_rec in inflows:
                if in_idx == out_idx or (in_idx, in_rec.source_ref) in used_refs:
                    continue
                if not (out_rec.direction == "transfer" or in_rec.direction == "transfer"):
                    continue
                in_utc = in_rec.occurred_at_utc
                if (
                    in_utc is None
                    or in_rec.amount_minor != out_rec.amount_minor
                    or in_rec.currency.upper() != out_rec.currency.upper()
                ):
                    continue
                delta = abs(in_utc - out_utc)
                if delta <= window:
                    used_refs.add((out_idx, out_rec.source_ref))
                    used_refs.add((in_idx, in_rec.source_ref))
                    pairs.append(
                        TransferPairSuggestion(
                            outflow_file_index=out_idx,
                            outflow_source_ref=out_rec.source_ref,
                            inflow_file_index=in_idx,
                            inflow_source_ref=in_rec.source_ref,
                            amount_minor=out_rec.amount_minor,
                            currency=out_rec.currency.upper(),
                            time_delta_seconds=int(delta.total_seconds()),
                            confidence=Decimal("0.90"),
                        )
                    )
                    break

        return pairs

    @staticmethod
    def _payee_compatible(a: str, b: str) -> bool:
        if not a or not b:
            return False
        return a == b or a in b or b in a
