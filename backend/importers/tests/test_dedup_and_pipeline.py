from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from homedatabase_api.services.import_parsers import ParsedRow, ParseOutcome
from importers import (
    BillImportPipeline,
    CategoryResolver,
    DeduplicationContext,
    DeduplicationEngine,
    ExistingFileSnapshot,
    ExistingLedgerTransaction,
    ExistingSourceSnapshot,
    ImportDocument,
    ImportParseResult,
    ParserLimits,
    ParserRegistry,
    create_default_registry,
    parse_import_file_pluggable,
)

_SEEDED_CATALOG = {
    ("expense", "餐饮"): "c-food",
    ("expense", "买菜超市"): "c-grocery",
    ("expense", "交通"): "c-transport",
    ("expense", "居家"): "c-home",
    ("expense", "医疗"): "c-medical",
    ("expense", "教育"): "c-education",
    ("expense", "娱乐"): "c-fun",
    ("expense", "其他"): "c-other",
    ("income", "工资"): "c-salary",
    ("income", "奖金"): "c-bonus",
    ("income", "理财收益"): "c-interest",
    ("income", "其他收入"): "c-other-in",
}


def test_category_resolver_combines_exact_match_payee_memory_aliases_and_keywords():
    resolver = CategoryResolver(
        catalog=_SEEDED_CATALOG,
        payee_memory={
            ("expense", "山姆会员商店"): "c-grocery",
            "自定义私教工作室": "教育",
        },
    )
    alipay_csv = """\
支付宝（中国）网络技术有限公司电子客户回单
交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注
2026-08-01 12:00:00,餐饮美食,老乡鸡,a@b,午饭套餐,支出,26.00,余额宝,交易成功,A001,M001,
2026-08-02 15:00:00,其他,财付通-山姆会员商店,b@c,采购,支出,399.00,银行卡,交易成功,A002,M002,
2026-08-03 19:00:00,商户消费,自定义私教工作室,c@d,课程,支出,500.00,银行卡,交易成功,A003,M003,
2026-08-04 08:30:00,商户消费,广州地铁集团,d@e,乘车码,支出,5.00,余额宝,交易成功,A004,M004,
"""
    parsed = create_default_registry().parse(ImportDocument.from_text(alipay_csv, "alipay.csv"))
    enriched = resolver.enrich_records(parsed.records)

    # 1. Alias mapping: "餐饮美食" -> "餐饮" (c-food)
    assert enriched[0].suggested_category_name == "餐饮"
    assert enriched[0].suggested_category_id == "c-food"

    # 2. Scoped historical payee memory beats generic "其他" category, even with "财付通-" wrapper!
    assert enriched[1].suggested_category_name == "买菜超市"
    assert enriched[1].suggested_category_id == "c-grocery"

    # 3. Unscoped payee memory resolves by name -> catalog id
    assert enriched[2].suggested_category_name == "教育"
    assert enriched[2].suggested_category_id == "c-education"

    # 4. Keyword fallback ("地铁" -> "交通" / c-transport) when bill says generic "商户消费"
    assert enriched[3].suggested_category_name == "交通"
    assert enriched[3].suggested_category_id == "c-transport"


def test_five_layer_deduplication_and_refund_and_transfer_matching():
    wechat_csv = "\n".join(
        [
            "微信支付账单明细",
            "----------------------微信支付账单明细列表--------------------",
            "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注",
            (
                "2026-08-05 12:30:02,商户消费,美团外卖,午餐,支出,¥28.50,"
                "招商银行储蓄卡(1234),支付成功,WX001,M-LUNCH-1,/"
            ),
            (
                "2026-08-06 09:00:00,商户消费,瑞幸咖啡,生椰拿铁,支出,¥9.90,"
                "零钱,支付成功,WX002,M-COFFEE-2,/"
            ),
            "2026-08-07 10:00:00,商户消费,京东商城,耳机,支出,¥199.00,零钱,支付成功,WX003,M-JD-3,/",
            (
                "2026-08-08 14:00:00,商户消费-退款,京东商城,耳机退款,收入,¥199.00,"
                "零钱,已全额退款,WX004,M-JD-3,/"
            ),
            "2026-08-10 15:00:00,零钱提现,招商银行(1234),/,/,¥1000.00,零钱,提现已到账,WX005,/,/",
        ]
    )
    # Bank CSV uploaded in the SAME batch contains:
    # 1) The exact same ¥28.50 Meituan lunch charged 4 seconds later via Tenpay (`财付通-美团外卖`)
    # 2) The complementary ¥1000.00 WeChat withdrawal deposit 10 minutes later!
    # 3) A genuinely unique utility bill
    bank_csv = """\
交易时间,交易对方,收支类型,金额,币种,交易流水号
2026-08-05 12:30:06,财付通-美团外卖,支出,28.50,CNY,CMB001
2026-08-10 15:10:00,财付通提现,收入,1000.00,CNY,CMB002
2026-08-12 08:00:00,国家电网,支出,210.40,CNY,CMB003
"""
    exact_dup_doc = ImportDocument.from_text("交易时间,交易对方,收支类型,金额\n", "already.csv")

    shanghai = ZoneInfo("Asia/Shanghai")
    context = DeduplicationContext(
        known_files=[
            ExistingFileSnapshot(file_id="file-prior-1", sha256=exact_dup_doc.sha256),
        ],
        known_sources=[
            # WX002 was already imported in last month's overlapping export!
            ExistingSourceSnapshot(
                source_record_id="src-prior-wx2",
                source_hash="different-hash-due-to-status-update",
                provider="wechat",
                external_id="WX002",
                linked_transaction_id="txn-coffee-existing",
            )
        ],
        ledger_transactions=[
            # WX003 was manually entered by the user at the exact same instant!
            ExistingLedgerTransaction(
                transaction_id="txn-jd-manual",
                occurred_at=datetime(2026, 8, 7, 10, 0, 0, tzinfo=shanghai),
                direction="expense",
                amount_minor=19_900,
                currency="CNY",
                payee="京东商城",
            )
        ],
    )

    pipeline = BillImportPipeline(
        category_resolver=CategoryResolver(catalog=_SEEDED_CATALOG),
    )
    plan = pipeline.run_batch(
        [
            exact_dup_doc,
            ImportDocument.from_text(wechat_csv, "wechat.csv"),
            ImportDocument.from_text(bank_csv, "cmb.csv"),
        ],
        context=context,
    )

    # Layer 1: Exact file SHA-256 duplicate caught immediately
    assert plan.files[0].effective_status == "duplicate"
    assert plan.files[0].duplicate_of_file_id == "file-prior-1"

    wechat_decisions = {d.record.external_id: d for d in plan.files[1].decisions}
    bank_decisions = {d.record.external_id: d for d in plan.files[2].decisions}

    # WX001 is unique in WeChat file
    assert wechat_decisions["WX001"].status == "unique"
    assert wechat_decisions["WX001"].record.suggested_category_id == "c-food"

    # Layer 2: WX002 caught by `(provider, external_id)` even though raw row hash changed!
    assert wechat_decisions["WX002"].status == "external_id_duplicate"
    assert wechat_decisions["WX002"].duplicate_of_txn_id == "txn-coffee-existing"
    assert wechat_decisions["WX002"].legacy_validation_status == "exact_duplicate"

    # Layer 4: WX003 caught by exact ledger transaction match
    assert wechat_decisions["WX003"].status == "exact_ledger_duplicate"
    assert wechat_decisions["WX003"].duplicate_of_txn_id == "txn-jd-manual"

    # Layer 5: CMB001 (`财付通-美团外卖` 4s after `WX001`) caught as cross-channel near-duplicate!
    assert bank_decisions["CMB001"].status == "near_ledger_duplicate"
    assert (
        bank_decisions["CMB001"].duplicate_of_batch_ref
        == wechat_decisions["WX001"].record.source_ref
    )

    # CMB003 (`国家电网`) is unique and auto-categorized to `居家` (`c-home`)
    assert bank_decisions["CMB003"].status == "unique"
    assert bank_decisions["CMB003"].record.suggested_category_id == "c-home"

    # Refund matcher linked WX004 (`¥199.00` refund) via `merchant_order_id="M-JD-3"`!
    assert len(plan.refund_suggestions) == 1
    refund_sug = plan.refund_suggestions[0]
    assert refund_sug.refund_source_ref == wechat_decisions["WX004"].record.source_ref
    assert refund_sug.matched_batch_source_ref == wechat_decisions["WX003"].record.source_ref
    assert refund_sug.match_basis == "merchant_order_id"

    # Transfer pair matcher linked WeChat withdrawal WX005 (`¥1000.00`) <-> Bank deposit CMB002!
    assert len(plan.transfer_pairs) == 1
    pair = plan.transfer_pairs[0]
    assert pair.outflow_source_ref == wechat_decisions["WX005"].record.source_ref
    assert pair.inflow_source_ref == bank_decisions["CMB002"].record.source_ref
    assert pair.amount_minor == 100_000
    assert pair.time_delta_seconds == 600


def test_cross_channel_near_duplicate_against_ledger_when_bank_payee_is_generic_gateway():
    shanghai = ZoneInfo("Asia/Shanghai")
    engine = DeduplicationEngine()
    ctx = DeduplicationContext(
        ledger_transactions=[
            ExistingLedgerTransaction(
                transaction_id="txn-wechat-1",
                occurred_at=datetime(2026, 8, 5, 12, 30, 0, tzinfo=shanghai),
                direction="expense",
                amount_minor=5600,
                currency="CNY",
                payee="喜茶",
            )
        ]
    )
    # Later user uploads Bank CSV where bank only recorded "财付通" as counterparty 30s later
    # Also verify when ledger already has "财付通" and user uploads WeChat CSV with card method
    ctx_bank_first = DeduplicationContext(
        ledger_transactions=[
            ExistingLedgerTransaction(
                transaction_id="txn-bank-1",
                occurred_at=datetime(2026, 8, 5, 12, 30, 20, tzinfo=shanghai),
                direction="expense",
                amount_minor=5600,
                currency="CNY",
                payee="财付通",
            )
        ]
    )
    wechat_csv = """\
微信支付账单明细
交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注
2026-08-05 12:30:00,商户消费,喜茶,多肉葡萄,支出,¥56.00,招商银行(1234),支付成功,WX999,M999,/
"""
    parsed = create_default_registry().parse(ImportDocument.from_text(wechat_csv, "wx.csv"))
    decisions = engine.evaluate_records(parsed.records, ctx_bank_first)
    assert decisions[0].status == "near_ledger_duplicate"
    assert decisions[0].duplicate_of_txn_id == "txn-bank-1"

    # Unrelated merchant at the same price within 5 min without gateway label stays unique!
    unrelated_csv = "交易时间,交易对方,收支类型,金额\n2026-08-05 12:31:00,肯德基,支出,56.00\n"
    unrelated_parsed = create_default_registry().parse(
        ImportDocument.from_text(unrelated_csv, "other.csv")
    )
    unrelated_decisions = engine.evaluate_records(unrelated_parsed.records, ctx)
    assert unrelated_decisions[0].status == "unique"


def test_pluggable_registry_ocr_signatures_fallback_and_legacy_adapter(tmp_path: Path):
    registry = create_default_registry()

    # Valid PDF -> needs_ocr; spoofed PDF -> rejected
    valid_pdf = ImportDocument(filename="bill.pdf", raw_bytes=b"%PDF-1.7\n...")
    assert registry.parse(valid_pdf).status == "needs_ocr"

    spoofed_pdf = ImportDocument(filename="bill.pdf", raw_bytes=b"not a pdf")
    assert registry.parse(spoofed_pdf).status == "rejected"

    # Generic template named `微信测试.csv` gracefully falls back to GenericTableBillParser
    generic_named_wechat = (
        "交易时间,交易对方,收支类型,金额,币种,交易分类\n"
        "2026-08-05 12:00:00,示例餐厅,支出,35.00,CNY,餐饮\n"
    )
    csv_path = tmp_path / "微信测试.csv"
    csv_path.write_text(generic_named_wechat, encoding="utf-8")

    legacy_outcome = parse_import_file_pluggable(
        csv_path,
        csv_path.name,
        max_rows=1000,
        max_uncompressed_bytes=10 * 1024 * 1024,
    )
    assert isinstance(legacy_outcome, ParseOutcome)
    assert legacy_outcome.status == "parsed"
    assert legacy_outcome.candidate_count == 1
    assert isinstance(legacy_outcome.rows[0], ParsedRow)
    assert legacy_outcome.rows[0].can_create_candidate is True
    assert legacy_outcome.rows[0].amount_minor == 3500

    # Custom third-party plugin registration & unregistration
    class CustomJsonParser:
        provider_id = "custom_json"  # type: ignore[assignment]
        priority = 120

        def sniff(self, doc: ImportDocument) -> float:
            return 1.0 if doc.suffix == ".json" else 0.0

        def parse(self, doc: ImportDocument, limits: ParserLimits) -> ImportParseResult:
            return ImportParseResult("unknown", "parsed", "custom ok", records=[])

    custom_reg = ParserRegistry()
    custom_reg.register(CustomJsonParser())  # type: ignore[arg-type]
    assert custom_reg.get("custom_json") is not None
    assert custom_reg.parse(ImportDocument.from_text("{}", "bill.json")).message == "custom ok"
    assert custom_reg.unregister("custom_json") is True
    assert custom_reg.get("custom_json") is None

    with pytest.raises(TypeError):
        custom_reg.register(object())  # type: ignore[arg-type]
