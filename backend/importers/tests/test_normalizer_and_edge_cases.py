from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook

from importers import (
    DeduplicationConfig,
    DeduplicationContext,
    DeduplicationEngine,
    ExistingLedgerTransaction,
    ExistingSourceSnapshot,
    GenericTableBillParser,
    ImportDocument,
    OfxBillParser,
    ParserLimits,
    canonicalize_payee,
    create_default_registry,
    parse_currency_code,
    parse_minor_amount,
    parse_ofx_datetime,
)


def test_fullwidth_amounts_currencies_and_payee_canonicalization():
    assert parse_minor_amount("￥１，２３４．５６") == 123_456
    assert parse_minor_amount("HK$ 120.50") == 12_050
    assert parse_minor_amount("€45.00") == 4500
    assert parse_minor_amount("£10.05") == 1005
    assert parse_minor_amount("/") is None

    assert parse_currency_code("港币") == ("HKD", False)
    assert parse_currency_code("美金") == ("USD", False)
    assert parse_currency_code(None) == ("CNY", True)
    assert parse_currency_code("BTC_INVALID") == (None, False)

    assert canonicalize_payee("财付通-微信支付-星巴克咖啡") == "星巴克咖啡"
    assert canonicalize_payee("Alipay*Starbucks Coffee") == "starbucks coffee"


def test_semicolon_and_tsv_delimiters_duplicate_headers_and_overlong_payee():
    long_payee = "超长商户" * 80  # 320 chars > 300 limit
    semi_csv = (
        "date;payee;direction;amount;currency;remark;remark\n"
        f"2026-08-05 12:00:00;{long_payee};expense;18.00;EUR;备注A;备注B\n"
        "2026-08-06 12:00:00;巴黎面包房;expense;8.50;BITCOIN;法棍;早餐\n"
    )
    doc = ImportDocument.from_text(semi_csv, "../../tricky;name/statement.csv")
    assert doc.filename == "statement.csv"

    res = GenericTableBillParser().parse(doc, ParserLimits())
    assert res.status == "parsed"
    assert res.record_count == 2
    assert res.candidate_count == 0

    first, second = res.records
    assert "payee_too_long" in first.issues
    assert len(first.payee or "") == 300
    assert first.raw_payload["remark"] == "备注A"
    assert first.raw_payload["remark#2"] == "备注B"

    assert "invalid_currency" in second.issues

    tsv_text = "交易时间\t交易对方\t收支类型\t金额\n2026-08-05 09:00:00\t早餐铺\t支出\t6.50\n"
    tsv_res = GenericTableBillParser().parse(
        ImportDocument.from_text(tsv_text, "breakfast.tsv"),
        ParserLimits(),
    )
    assert tsv_res.status == "parsed"
    assert tsv_res.candidate_count == 1
    assert tsv_res.records[0].amount_minor == 650


def test_multi_sheet_xlsx_skips_cover_sheet_and_rejects_header_only_csv():
    wb = Workbook()
    cover = wb.active
    cover.title = "封面说明"
    cover.append(["2026年8月家庭对账汇总说明"])
    cover.append(["本页不含明细"])

    s1 = wb.create_sheet("储蓄卡")
    s1.append(["交易时间", "交易对方", "收支类型", "金额"])
    s1.append(["2026-08-01 08:00:00", "早市买菜", "支出", 22.5])

    s2 = wb.create_sheet("信用卡")
    s2.append(["交易时间", "交易对方", "收支类型", "金额"])
    s2.append(["2026-08-02 19:30:00", "家庭聚餐", "支出", 188.0])

    buf = BytesIO()
    wb.save(buf)
    res = GenericTableBillParser().parse(
        ImportDocument(filename="multi.xlsx", raw_bytes=buf.getvalue()),
        ParserLimits(),
    )
    assert res.status == "parsed"
    assert res.candidate_count == 2
    assert [r.source_ref for r in res.records] == ["储蓄卡!2", "信用卡!2"]

    header_only = ImportDocument.from_text(
        "交易时间,交易对方,收支类型,金额\n",
        "empty.csv",
    )
    empty_res = GenericTableBillParser().parse(header_only, ParserLimits())
    assert empty_res.status == "rejected"
    assert "没有账单记录" in empty_res.message


def test_unclosed_sgml_stmttrn_fractional_tz_and_image_magic_signatures():
    # Fractional timezone offset (+5.5 IST = +05:30)
    ist_dt = parse_ofx_datetime("20260805153000[+5.5:IST]")
    assert ist_dt is not None
    assert ist_dt.astimezone(UTC) == datetime(2026, 8, 5, 10, 0, 0, tzinfo=UTC)

    # Legacy OFX 1.02 where <STMTTRN> tags themselves are NOT closed by </STMTTRN>
    unclosed_ofx = """\
OFXHEADER:100
DATA:OFXSGML
<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>HKD
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260805080000[+8:HKT]
<TRNAMT>-42.00
<FITID>HK001
<NAME>MTR CORP
<STMTTRN>
<TRNTYPE>OTHER
<DTPOSTED>20260805090000[+8:HKT]
<TRNAMT>0.00
<FITID>HK002
<NAME>BALANCE INQUIRY
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""
    ofx_res = OfxBillParser().parse(
        ImportDocument.from_text(unclosed_ofx, "mtr.ofx"),
        ParserLimits(),
    )
    assert ofx_res.status == "parsed"
    assert ofx_res.record_count == 2
    assert ofx_res.candidate_count == 1
    assert ofx_res.ignored_count == 1
    assert ofx_res.records[0].currency == "HKD"
    assert ofx_res.records[0].amount_minor == 4200

    # Image magic signatures for PNG, JPEG, WEBP, HEIC
    reg = create_default_registry()
    assert reg.parse(ImportDocument("a.png", b"\x89PNG\r\n\x1a\n1234")).status == "needs_ocr"
    assert reg.parse(ImportDocument("a.jpg", b"\xff\xd8\xff\xe01234")).status == "needs_ocr"
    assert reg.parse(ImportDocument("a.webp", b"RIFF\x00\x00\x00\x00WEBP12")).status == "needs_ocr"
    assert reg.parse(ImportDocument("a.heic", b"\x00\x00\x00\x18ftypheic12")).status == "needs_ocr"
    assert reg.parse(ImportDocument("a.png", b"GIF89a-spoofed")).status == "rejected"


def test_dedup_batch_internal_row_hash_and_refund_window_boundaries():
    dup_rows_csv = (
        "交易时间,交易对方,收支类型,金额\n"
        "2026-08-05 12:00:00,重复测试店,支出,15.00\n"
        "2026-08-05 12:00:00,重复测试店,支出,15.00\n"
    )
    parsed = GenericTableBillParser().parse(
        ImportDocument.from_text(dup_rows_csv, "dup.csv"),
        ParserLimits(),
    )
    engine = DeduplicationEngine(DeduplicationConfig(refund_lookback_window=timedelta(days=30)))
    decisions = engine.evaluate_records(parsed.records, DeduplicationContext())
    assert decisions[0].status == "unique"
    assert decisions[1].status == "batch_internal_duplicate"
    assert decisions[1].legacy_validation_status == "duplicate_source_record"

    # Prior unlinked source row hash -> duplicate_source_row
    prior_ctx = DeduplicationContext(
        known_sources=[
            ExistingSourceSnapshot(
                source_record_id="src-old",
                source_hash=parsed.records[0].source_hash,
                linked_transaction_id=None,
            )
        ]
    )
    prior_decisions = engine.evaluate_records([parsed.records[0]], prior_ctx)
    assert prior_decisions[0].status == "duplicate_source_row"
    assert prior_decisions[0].legacy_validation_status == "duplicate_source_record"

    # Refund outside 30-day lookback window is NOT falsely linked without merchant_order_id
    shanghai = ZoneInfo("Asia/Shanghai")
    old_exp_ctx = DeduplicationContext(
        ledger_transactions=[
            ExistingLedgerTransaction(
                transaction_id="txn-too-old",
                occurred_at=datetime(2026, 5, 1, 10, 0, 0, tzinfo=shanghai),
                direction="expense",
                amount_minor=5000,
                currency="CNY",
                payee="某商店",
            )
        ]
    )
    refund_csv = "交易时间,交易对方,收支类型,金额\n2026-08-15 10:00:00,某商店,退款,50.00\n"
    refund_parsed = GenericTableBillParser().parse(
        ImportDocument.from_text(refund_csv, "refund.csv"),
        ParserLimits(),
    )
    assert engine.match_refunds(refund_parsed.records, old_exp_ctx) == []
