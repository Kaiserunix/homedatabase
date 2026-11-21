from __future__ import annotations

import zipfile
from io import BytesIO

from openpyxl import Workbook

from importers import (
    MAX_SAFE_MINOR,
    GenericTableBillParser,
    ImportDocument,
    ParserLimits,
    create_default_registry,
)


def test_bank_csv_supports_split_date_time_and_debit_credit_columns_with_memo_fallback():
    bank_csv = (
        "中国招商银行历史交易明细\n"
        "交易日期,具体时间,交易类型,对方户名,借方发生额,贷方发生额,币种,交易流水号,交易摘要\n"
        "20260805,123015,快捷支付,财付通-美团外卖,28.50,0.00,人民币,CMB2026080501,网上支付\n"
        "20260820,031500,结息,,,12.88,CNY,CMB2026082002,活期存款结息\n"
        "20260821,180000,消费,境外书店,(15.20),,USD,CMB2026082103,外币购书\n"
    )
    doc = ImportDocument.from_text(bank_csv, "cmb_statement.csv", encoding="utf-8")
    parser = GenericTableBillParser()
    result = parser.parse(doc, ParserLimits())

    assert result.status == "parsed"
    assert result.record_count == 3
    assert result.candidate_count == 3

    first, second, third = result.records
    assert first.occurred_at is not None
    assert first.occurred_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-05 12:30:15"
    assert first.direction == "expense"
    assert first.amount_minor == 2850
    assert first.payee == "财付通-美团外卖"
    assert first.canonical_payee == "美团外卖"
    assert first.external_id == "CMB2026080501"

    # Empty `对方户名` falls back to `交易摘要` ("活期存款结息")
    assert second.direction == "income"
    assert second.amount_minor == 1288
    assert second.payee == "活期存款结息"

    # Accounting parentheses `(15.20)` in debit column + USD currency
    assert third.direction == "expense"
    assert third.amount_minor == 1520
    assert third.currency == "USD"


def test_header_priority_prevents_generic_type_from_shadowing_explicit_inout():
    csv_text = (
        "交易时间,交易类型,备注,交易对方,收支类型,金额\n"
        "2026-08-10 10:00:00,日常订单,请勿当作商户,社区生鲜店,支出,45.60\n"
    )
    doc = ImportDocument.from_text(csv_text, "priority.csv")
    result = GenericTableBillParser().parse(doc, ParserLimits())
    assert result.status == "parsed"
    rec = result.records[0]
    assert rec.payee == "社区生鲜店"
    assert rec.item_summary == "请勿当作商户"
    assert rec.direction == "expense"
    assert rec.amount_minor == 4560
    assert rec.is_ledger_candidate is True


def test_money_precision_and_safe_minor_bounds_are_enforced():
    max_major = f"{MAX_SAFE_MINOR // 100}.{MAX_SAFE_MINOR % 100:02d}"
    overflow_major = f"{(MAX_SAFE_MINOR + 1) // 100}.{(MAX_SAFE_MINOR + 1) % 100:02d}"
    csv_text = (
        "交易时间,交易对方,收支类型,金额,币种\n"
        f"2026-08-01 10:00:00,安全上限商户,支出,{max_major},CNY\n"
        f"2026-08-01 10:01:00,越界商户,支出,{overflow_major},CNY\n"
        "2026-08-01 10:02:00,三位小数商户,支出,12.345,CNY\n"
    )
    doc = ImportDocument.from_text(csv_text, "bounds.csv")
    result = GenericTableBillParser().parse(doc, ParserLimits())
    assert result.status == "parsed"
    assert result.candidate_count == 1
    assert result.rejected_count == 2
    assert result.records[0].amount_minor == MAX_SAFE_MINOR
    assert "amount_exceeds_safe_range" in result.records[1].issues
    assert "amount_has_more_than_two_decimal_places" in result.records[2].issues
    assert "超出安全范围" in result.message
    assert "超过两位小数" in result.message


def test_xlsx_ambiguous_ieee754_float_and_zip_bomb_defenses():
    wb = Workbook()
    ws = wb.active
    ws.append(["交易时间", "交易对方", "收支类型", "金额"])
    # IEEE-754 float at 7.5e13 + 0.5 is < MAX_SAFE_MINOR/100 yet math.ulp == 0.015625 >= 0.01
    ws.append(["2026-08-01 10:00:00", "浮点精度测试", "支出", 75_000_000_000_000.5])
    buf = BytesIO()
    wb.save(buf)

    doc = ImportDocument(filename="float_precision.xlsx", raw_bytes=buf.getvalue())
    result = GenericTableBillParser().parse(doc, ParserLimits())
    assert result.status == "parsed"
    assert result.candidate_count == 0
    assert "ambiguous_xlsx_float_amount" in result.records[0].issues

    # High compression ratio (>500x) zip bomb rejected fail-closed
    bomb_buf = BytesIO()
    with zipfile.ZipFile(bomb_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/sharedStrings.xml", b"A" * (2 * 1024 * 1024))
    bomb_doc = ImportDocument(filename="bomb.xlsx", raw_bytes=bomb_buf.getvalue())
    bomb_res = GenericTableBillParser().parse(bomb_doc, ParserLimits())
    assert bomb_res.status == "rejected"
    assert "压缩比异常" in bomb_res.message


def test_binary_csv_and_legacy_xls_and_row_limits_are_rejected():
    registry = create_default_registry()

    binary_csv = ImportDocument(filename="bad.csv", raw_bytes=b"date,amount\x00\x01\x02")
    assert registry.parse(binary_csv).status == "rejected"

    legacy_xls = ImportDocument(filename="old_bank.xls", raw_bytes=b"\xd0\xcf\x11\xe0")
    xls_res = registry.parse(legacy_xls)
    assert xls_res.status == "rejected"
    assert "XLS" in xls_res.message

    three_rows_csv = (
        "交易时间,交易对方,收支类型,金额\n"
        "2026-08-01 10:00:00,A,支出,1.00\n"
        "2026-08-02 10:00:00,B,支出,2.00\n"
        "2026-08-03 10:00:00,C,支出,3.00\n"
    )
    limited = registry.parse(
        ImportDocument.from_text(three_rows_csv, "many.csv"),
        limits=ParserLimits(max_rows=2),
    )
    assert limited.status == "rejected"
    assert "超过 2 行" in limited.message
