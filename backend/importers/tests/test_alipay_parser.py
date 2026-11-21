from __future__ import annotations

from importers import AlipayBillParser, ImportDocument, ParserLimits, create_default_registry

_MODERN_ALIPAY_CSV = "\n".join(
    [
        "------------------------------------------------------------------------------------",
        "支付宝（中国）网络技术有限公司电子客户回单",
        "账号：[example_family@163.com]",
        "姓名：[李四]",
        "起始时间：[2026-08-01 00:00:00]    终止时间：[2026-08-31 23:59:59]",
        "------------------------------------交易记录明细列表------------------------------------",
        "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注",
        (
            "2026-08-03 18:45:12,餐饮美食,星巴克咖啡,star***@alipay.com,拿铁中杯,"
            "支出,32.00,招商银行储蓄卡(1234),交易成功,202608032200140001\t ,T20260803001\t ,"
        ),
        (
            "2026-08-04 10:12:00,日用百货,某淘宝旗舰店,shop***@taobao.com,未付款取消的商品,"
            "支出,199.00,,交易关闭,202608042200140002\t ,T20260804002\t ,"
        ),
        (
            "2026-08-07 09:30:00,投资理财,天弘基金管理有限公司,fund***@alipay.com,"
            "余额宝-2026.08.06-收益发放,收入,1.58,余额宝,交易成功,202608072200140003\t ,,"
        ),
        (
            "2026-08-09 14:20:00,退款,星巴克咖啡,star***@alipay.com,退款-拿铁中杯,"
            "不计收支,32.00,招商银行储蓄卡(1234),退款成功,202608092200140004\t ,T20260803001\t ,"
        ),
        (
            "2026-08-10 21:00:00,充值缴费,花呗,huabei***,花呗主动还款-2026年08月账单,"
            "不计收支,1500.00,招商银行储蓄卡(1234),还款成功,202608102200140005\t ,,"
        ),
        "------------------------------------------------------------------------------------",
        "共5笔记录",
        "已收入：1笔 1.58元",
        "已支出：1笔 32.00元",
        "导出时间：[2026-09-01 10:05:00]    用户：[李四]",
    ]
)

_CLASSIC_ALIPAY_WEB_CSV = "\n".join(
    [
        "支付宝交易记录明细查询",
        "账号:[138****0000]",
        "起始日期:[2026-08-01 00:00:00]    终止日期:[2026-08-31 23:59:59]",
        "---------------------------------交易记录明细列表---------------------------------",
        (
            "交易号                  ,商家订单号              ,交易创建时间            ,"
            "付款时间                ,最近修改时间            ,交易来源地  ,类型              ,"
            "交易对方            ,商品名称                ,金额（元）  ,收/支     ,"
            "交易状态        ,服务费（元） ,成功退款（元）  ,备注                  ,资金状态     ,"
        ),
        (
            "20260815000010001       ,M88801                  ,2026-08-15 19:00:00     ,"
            "2026-08-15 19:00:05     ,2026-08-15 19:00:05     ,其他        ,即时到账交易      ,"
            "盒马鲜生            ,盒马门店消费            ,88.60       ,支出      ,"
            "交易成功        ,0.00        ,0.00            ,                      ,已支出       ,"
        ),
        (
            "20260816000010002       ,M88802                  ,2026-08-16 08:00:00     ,"
            "                        ,2026-08-16 08:30:00     ,淘宝        ,担保交易          ,"
            "某某书店            ,算法导论                ,128.00      ,支出      ,"
            "交易关闭        ,0.00        ,0.00            ,超时关闭              ,             ,"
        ),
        "----------------------------------------------------------------------------------",
        "共2笔记录",
    ]
)


def test_modern_alipay_gb18030_csv_filters_closed_orders_and_strips_footer():
    registry = create_default_registry()
    doc = ImportDocument.from_text(
        _MODERN_ALIPAY_CSV,
        "alipay_record_20260901.csv",
        encoding="gb18030",
    )
    detected = registry.detect(doc)
    assert detected is not None
    assert detected.provider_id == "alipay"

    result = registry.parse(doc)
    assert result.status == "parsed"
    assert result.provider == "alipay"
    assert result.metadata["account"] == "example_family@163.com"
    assert result.metadata["member_name"] == "李四"
    assert result.metadata["period_end"] == "2026-08-31 23:59:59"

    # Footer summary lines after dashed divider must NOT become bogus rejected records
    assert result.record_count == 5
    assert result.candidate_count == 2  # Starbucks expense + Yu'eBao yield income
    assert result.ignored_count == 1  # Closed Taobao order (`交易关闭`)
    assert result.refund_count == 1  # Refund (`退款成功`)
    assert result.transfer_count == 1  # Huabei repayment (`不计收支`)

    by_id = {rec.external_id: rec for rec in result.records}
    coffee = by_id["202608032200140001"]
    assert coffee.direction == "expense"
    assert coffee.amount_minor == 3200
    assert coffee.original_category == "餐饮美食"
    assert coffee.merchant_order_id == "T20260803001"
    assert coffee.is_ledger_candidate is True

    # Crucial safety check: unpaid `交易关闭` row with `收/支=支出` is ignored, NOT an expense!
    closed_order = by_id["202608042200140002"]
    assert closed_order.direction == "ignored"
    assert closed_order.is_ledger_candidate is False
    assert "closed_or_unsettled_transaction" in closed_order.issues

    yield_in = by_id["202608072200140003"]
    assert yield_in.direction == "income"
    assert yield_in.amount_minor == 158
    assert yield_in.is_ledger_candidate is True

    refund = by_id["202608092200140004"]
    assert refund.direction == "refund"
    assert refund.merchant_order_id == "T20260803001"
    assert refund.is_ledger_candidate is False

    huabei = by_id["202608102200140005"]
    assert huabei.direction == "transfer"
    assert huabei.amount_minor == 150_000
    assert huabei.is_ledger_candidate is False


def test_classic_alipay_web_export_handles_padded_columns_and_paid_at_fallback():
    parser = AlipayBillParser()
    doc = ImportDocument.from_text(
        _CLASSIC_ALIPAY_WEB_CSV,
        "alipay_web_export.csv",
        encoding="gb18030",
    )
    assert parser.sniff(doc) >= 0.95
    result = parser.parse(doc, ParserLimits())
    assert result.status == "parsed"
    assert result.record_count == 2
    assert result.candidate_count == 1
    assert result.ignored_count == 1

    hema = result.records[0]
    assert hema.external_id == "20260815000010001"
    assert hema.payee == "盒马鲜生"
    assert hema.amount_minor == 8860
    assert hema.direction == "expense"
    assert hema.occurred_at is not None
    assert hema.occurred_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-15 19:00:05"

    # Second row had blank `付款时间`, fell back to `交易创建时间`, ignored via `交易关闭`
    closed = result.records[1]
    assert closed.direction == "ignored"
    assert closed.occurred_at is not None
    assert closed.occurred_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-16 08:00:00"
