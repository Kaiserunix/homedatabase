from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from importers import ImportDocument, OfxBillParser, ParserLimits, create_default_registry

_OFX_V1_SGML = """\
OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:UTF-8
CHARSET:UTF-8
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<DTSERVER>20260901120000[+8:CST]
<LANGUAGE>CHS
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1001
<STMTRS>
<CURDEF>CNY
<BANKACCTFROM>
<BANKID>03080000
<ACCTID>6225880012349876
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260801000000[+8:CST]
<DTEND>20260831235959[+8:CST]
<STMTTRN>
<TRNTYPE>POS
<DTPOSTED>20260805123000[+8:CST]
<TRNAMT>-28.50
<FITID>OFX202608050001
<NAME>美团外卖
<MEMO>工作午餐订单
</STMTTRN>
<STMTTRN>
<TRNTYPE>DIRECTDEP
<DTPOSTED>20260815090000[+8:CST]
<TRNAMT>12500.00
<FITID>OFX202608150002
<NAME>示例科技公司
<MEMO>8月工资代发
</STMTTRN>
<STMTTRN>
<TRNTYPE>XFER
<DTPOSTED>20260820180000[+8:CST]
<TRNAMT>-2000.00
<FITID>OFX202608200003
<NAME>房东张先生
<MEMO>8月房租转账
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""

_OFX_V2_XML_CREDIT_CARD = """\
<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX>
  <CREDITCARDMSGSRSV1>
    <CCSTMTTRNRS>
      <CCSTMTRS>
        <CURDEF>USD</CURDEF>
        <CCACCTFROM>
          <ACCTID>4111222233334444</ACCTID>
        </CCACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260818142000.000[-4:EDT]</DTPOSTED>
            <TRNAMT>-19.99</TRNAMT>
            <FITID>CC-USD-001</FITID>
            <NAME>GITHUB INC</NAME>
            <MEMO>COPILOT SUBSCRIPTION</MEMO>
            <ORIGCURRENCY>
              <CURSYM>EUR</CURSYM>
              <CURRATE>1.0850</CURRATE>
            </ORIGCURRENCY>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT</TRNTYPE>
            <DTPOSTED>20260819100000Z</DTPOSTED>
            <TRNAMT>19.99</TRNAMT>
            <FITID>CC-USD-002</FITID>
            <NAME>GITHUB INC</NAME>
            <MEMO>REFUND SUBSCRIPTION</MEMO>
          </STMTTRN>
        </BANKTRANLIST>
      </CCSTMTRS>
    </CCSTMTTRNRS>
  </CREDITCARDMSGSRSV1>
</OFX>
"""


def test_ofx_v1_sgml_parses_unclosed_tags_timezones_and_masks_account_id():
    registry = create_default_registry()
    doc = ImportDocument.from_text(_OFX_V1_SGML, "cmb_202608.ofx")
    detected = registry.detect(doc)
    assert detected is not None
    assert detected.provider_id == "ofx"

    result = registry.parse(doc)
    assert result.status == "parsed"
    assert result.provider == "ofx"
    assert result.metadata["statement_type"] == "bank"
    assert result.metadata["bank_id"] == "03080000"
    # Verify PII masking on account number metadata
    assert result.metadata["account_id"] == "***9876"
    assert "62258800" not in result.metadata["account_id"]

    assert result.record_count == 3
    assert result.candidate_count == 3

    lunch, salary, rent = result.records
    assert lunch.external_id == "OFX202608050001"
    assert lunch.direction == "expense"
    assert lunch.amount_minor == 2850
    assert lunch.payee == "美团外卖"
    assert lunch.item_summary == "工作午餐订单"
    assert lunch.occurred_at_utc == datetime(2026, 8, 5, 4, 30, 0, tzinfo=UTC)

    assert salary.direction == "income"
    assert salary.amount_minor == 1_250_000

    assert rent.direction == "expense"
    assert "ofx_trntype_xfer" in rent.warnings

    # Optional strict mode classifies XFER as non-posting transfer
    strict_xfer_parser = OfxBillParser(classify_xfer_as_transfer=True)
    strict_res = strict_xfer_parser.parse(doc, ParserLimits())
    assert strict_res.candidate_count == 2
    assert strict_res.transfer_count == 1
    assert strict_res.records[2].direction == "transfer"


def test_ofx_v2_xml_credit_card_parses_fx_metadata_and_refund():
    doc = ImportDocument.from_text(_OFX_V2_XML_CREDIT_CARD, "card.qfx")
    parser = OfxBillParser()
    assert parser.sniff(doc) >= 0.95
    result = parser.parse(doc, ParserLimits())

    assert result.status == "parsed"
    assert result.metadata["statement_type"] == "credit_card"
    assert result.metadata["account_id"] == "***4444"
    assert result.record_count == 2
    assert result.candidate_count == 1
    assert result.refund_count == 1

    charge, refund = result.records
    assert charge.direction == "expense"
    assert charge.currency == "USD"
    assert charge.amount_minor == 1999
    assert charge.fx_rate == Decimal("1.0850")
    assert charge.raw_payload["FX_CURSYM"] == "EUR"
    # 14:20 EDT (-04:00) == 18:20 UTC
    assert charge.occurred_at_utc == datetime(2026, 8, 18, 18, 20, 0, tzinfo=UTC)

    assert refund.direction == "refund"
    assert refund.is_ledger_candidate is False
    assert "refund_requires_original_transaction" in refund.issues


def test_ofx_rejects_xxe_doctype_and_entity_declarations():
    malicious_ofx = """\
<?xml version="1.0"?>
<!DOCTYPE ofx [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>CNY</CURDEF></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""
    doc = ImportDocument.from_text(malicious_ofx, "evil.ofx")
    result = OfxBillParser().parse(doc, ParserLimits())
    assert result.status == "rejected"
    assert "DOCTYPE/ENTITY" in result.message
