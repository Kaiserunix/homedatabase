from __future__ import annotations

from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook

from importers import ImportDocument, ParserLimits, WeChatBillParser, create_default_registry

_SAMPLE_WECHAT_CSV = "\n".join(
    [
        "微信支付账单明细",
        "微信昵称：[测试家庭成员]",
        "起始时间：[2026-08-01 00:00:00] 终止时间：[2026-08-31 23:59:59]",
        "导出类型：[全部]",
        "导出时间：[2026-09-01 10:00:00]",
        "",
        "共7笔记录",
        "收入：2笔 220.00元",
        "支出：3笔 178.50元",
        "中性交易：1笔 1000.00元",
        "----------------------微信支付账单明细列表--------------------",
        "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注",
        (
            "2026-08-05 12:30:02,商户消费,美团外卖,工作午餐,支出,¥28.50,"
            "招商银行储蓄卡(1234),支付成功,'420000202608050001\t,'M20260805001\t,/"
        ),
        (
            "2026-08-06 20:15:10,微信红包,发给妈妈,/,支出,¥100.00,"
            "零钱,对方已收钱,100003990120260806002\t,/,节日红包"
        ),
        "2026-08-06 20:20:00,微信红包,爸爸,/,收入,¥200.00,/,已存入零钱,100003990120260806003\t,/,/",
        (
            "2026-08-08 09:00:00,商户消费,生鲜超市,有机蔬菜,支出,¥50.00,"
            "零钱,已退款(￥20.00),420000202608080004\t,M20260808004\t,/"
        ),
        (
            "2026-08-08 11:30:00,商户消费-退款,生鲜超市,有机蔬菜,收入,¥20.00,"
            "零钱,已退款(￥20.00),420000202608080005\t,M20260808004\t,/"
        ),
        (
            "2026-08-10 15:00:00,零钱提现,招商银行(1234),/,/,¥1000.00,"
            "零钱,提现已到账,202608100006\t,/,/"
        ),
        (
            "2026-08-11 16:00:00,商户消费,某网店,取消订单,支出,¥99.00,"
            "零钱,交易关闭,420000202608110007\t,M20260811007\t,/"
        ),
    ]
)


def test_wechat_csv_parses_preamble_red_packets_transfers_refunds_and_closed_rows():
    registry = create_default_registry()
    doc = ImportDocument.from_text(
        _SAMPLE_WECHAT_CSV,
        "微信支付账单(20260801-20260831).csv",
        encoding="utf-8-sig",
    )
    detected = registry.detect(doc)
    assert detected is not None
    assert detected.provider_id == "wechat"

    result = registry.parse(doc)
    assert result.status == "parsed"
    assert result.provider == "wechat"
    assert result.metadata["nickname"] == "测试家庭成员"
    assert result.metadata["period_start"] == "2026-08-01 00:00:00"
    assert result.metadata["period_end"] == "2026-08-31 23:59:59"

    assert result.record_count == 7
    # 3 valid expenses (lunch, red packet sent, grocery) + 1 valid income (red packet received)
    assert result.candidate_count == 4
    assert result.refund_count == 1
    assert result.transfer_count == 1
    assert result.ignored_count == 1

    by_id = {rec.external_id: rec for rec in result.records}
    # Verify Excel quote and trailing tab were stripped from external_id and merchant_order_id
    lunch = by_id["420000202608050001"]
    assert lunch.merchant_order_id == "M20260805001"
    assert lunch.direction == "expense"
    assert lunch.amount_minor == 2850
    assert lunch.payee == "美团外卖"
    assert lunch.item_summary == "工作午餐"
    assert lunch.payment_method == "招商银行储蓄卡(1234)"
    assert lunch.is_ledger_candidate is True
    assert lunch.occurred_at is not None
    assert lunch.occurred_at.tzinfo == ZoneInfo("Asia/Shanghai")

    # Regression check: `交易类型 = 微信红包` does NOT hijack `收/支`!
    rp_out = by_id["100003990120260806002"]
    assert rp_out.direction == "expense"
    assert rp_out.amount_minor == 10_000
    assert rp_out.is_ledger_candidate is True
    assert rp_out.item_summary == "节日红包"

    rp_in = by_id["100003990120260806003"]
    assert rp_in.direction == "income"
    assert rp_in.amount_minor == 20_000
    assert rp_in.is_ledger_candidate is True

    # Original partially-refunded expense stays a valid expense with warning
    grocery_exp = by_id["420000202608080004"]
    assert grocery_exp.direction == "expense"
    assert grocery_exp.is_ledger_candidate is True
    assert "expense_has_subsequent_refund" in grocery_exp.warnings

    # Refund row is classified as `refund` and preserves matching merchant_order_id
    grocery_ref = by_id["420000202608080005"]
    assert grocery_ref.direction == "refund"
    assert grocery_ref.merchant_order_id == "M20260808004"
    assert grocery_ref.is_ledger_candidate is False
    assert "refund_requires_original_transaction" in grocery_ref.issues

    # Neutral withdrawal `/` is classified as `transfer`
    withdrawal = by_id["202608100006"]
    assert withdrawal.direction == "transfer"
    assert withdrawal.amount_minor == 100_000
    assert withdrawal.is_ledger_candidate is False
    assert "neutral_transfer_requires_counter_account" in withdrawal.issues

    # Closed order is ignored
    closed = by_id["420000202608110007"]
    assert closed.direction == "ignored"
    assert closed.is_ledger_candidate is False


def test_wechat_xlsx_export_is_detected_and_parsed():
    wb = Workbook()
    ws = wb.active
    ws.title = "微信支付账单明细"
    ws.append(["微信支付账单明细"])
    ws.append(["微信昵称：[小家]"])
    ws.append(["----------------------微信支付账单明细列表--------------------"])
    ws.append(
        [
            "交易时间",
            "交易类型",
            "交易对方",
            "商品",
            "收/支",
            "金额(元)",
            "支付方式",
            "当前状态",
            "交易单号",
            "商户单号",
            "备注",
        ]
    )
    ws.append(
        [
            "2026-08-12 08:15:00",
            "商户消费",
            "深圳地铁",
            "乘车码",
            "支出",
            "¥4.00",
            "零钱",
            "支付成功",
            "420000202608129999",
            "M9999",
            "/",
        ]
    )
    buf = BytesIO()
    wb.save(buf)

    doc = ImportDocument(filename="微信支付账单流水文件.xlsx", raw_bytes=buf.getvalue())
    parser = WeChatBillParser()
    assert parser.sniff(doc) >= 0.95
    result = parser.parse(doc, ParserLimits())
    assert result.status == "parsed"
    assert result.candidate_count == 1
    rec = result.records[0]
    assert rec.payee == "深圳地铁"
    assert rec.amount_minor == 400
    assert rec.direction == "expense"
