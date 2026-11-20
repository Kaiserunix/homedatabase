from __future__ import annotations

import inspect
import io
import json
from datetime import date
from pathlib import Path

import pytest
from conftest import as_member
from PIL import Image
from pydantic import ValidationError

from homedatabase_api.ai.bill_parser import BillOcrChunkRequest, OpenAiVisionOcrProvider
from homedatabase_api.ai.cfo_provider import OpenAiCompatibleCfoProvider
from homedatabase_api.config import Settings, get_settings
from homedatabase_api.database import get_session_factory
from homedatabase_api.main import app
from homedatabase_api.models import Account
from homedatabase_api.routers import auth as auth_router
from homedatabase_api.routers import cfo as cfo_router
from homedatabase_api.routers import ledger as ledger_router
from homedatabase_api.routers import system as system_router
from homedatabase_api.services import ocr_tasks
from homedatabase_api.services.import_parsers import (
    ParsedRow,
    ParseOutcome,
    register_statement_parser,
)
from homedatabase_api.services.ocr_results import OcrResultError


def _post_txn(
    client,
    member_id: str,
    *,
    txn_type: str = "expense",
    payee: str,
    account_id: str = "a-family",
    amount_cents: int,
    occurred_at: str,
    category_id: str | None = "c-food",
    note: str = "",
    refund_of: str | None = None,
    to_account_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": txn_type,
        "payee": payee,
        "note": note,
        "accountId": account_id,
        "amountCents": amount_cents,
        "currency": "CNY",
        "date": occurred_at,
    }
    if txn_type != "transfer":
        payload["categoryId"] = category_id
    if refund_of is not None:
        payload["refundOf"] = refund_of
    if to_account_id is not None:
        payload["toAccountId"] = to_account_id
    response = client.post("/api/transactions", headers=as_member(member_id), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_cfo_capabilities_reports_local_deterministic_privacy_boundary(client):
    unauth = client.get("/api/cfo/capabilities")
    assert unauth.status_code == 401

    res = client.get("/api/cfo/capabilities", headers=as_member("m-son"))
    assert res.status_code == 200
    body = res.json()
    assert body["cfoProvider"] == "deterministic-local"
    assert body["ocrProvider"] == "codex-cli"
    assert body["externalLlmEnabled"] is False
    assert "零外部 AI 调用" in body["privacyBoundary"]
    assert "financial_memory" in body["supportedIntents"]


def test_cfo_financial_memory_computes_cross_month_refunds_splits_and_import_provenance(client):
    csv_bytes = (
        "交易时间,交易对方,收支类型,金额,交易分类\n"
        "2025-11-10 14:00:00,宜家装修主材,支出,100.00,居家\n"
    ).encode("utf-8-sig")
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-family"},
        files=[("files", ("ikea-2025.csv", csv_bytes, "text/csv"))],
    )
    assert uploaded.status_code == 201
    batch = uploaded.json()
    confirmed = client.post(
        f"/api/import-batches/{batch['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [batch["candidates"][0]["id"]]},
    )
    assert confirmed.status_code == 200
    imported_txn = next(
        t
        for t in client.get("/api/transactions", headers=as_member("m-son")).json()
        if t["batchId"] == batch["id"]
    )

    # Cross-year / cross-month partial refund of 20.00 CNY (net = 80.00 CNY = 8000 cents)
    _post_txn(
        client,
        "m-son",
        txn_type="refund",
        payee="宜家装修退差价",
        account_id="a-family",
        amount_cents=2_000,
        occurred_at="2026-01-05T10:00:00+08:00",
        category_id="c-home",
        refund_of=imported_txn["id"],
    )

    # Split original 100.00 CNY: 60% m-son (6000) / 40% m-mom (4000)
    split_res = client.post(
        "/api/splits",
        headers=as_member("m-son"),
        json={
            "txnId": imported_txn["id"],
            "totalCents": 10_000,
            "shares": [
                {"memberId": "m-son", "shareCents": 6_000},
                {"memberId": "m-mom", "shareCents": 4_000},
            ],
        },
    )
    assert split_res.status_code == 201
    split = split_res.json()
    assert (
        client.post(
            f"/api/splits/{split['id']}/respond",
            headers=as_member("m-mom"),
            json={"accept": True, "expectedVersion": split["version"]},
        ).status_code
        == 200
    )

    son_ask = client.post(
        "/api/cfo/ask",
        headers=as_member("m-son"),
        json={
            "question": "找出去年与装修有关的开销，分开列出已退款、已分摊和最终自己承担的金额",
            "asOfDate": "2026-09-15",
        },
    )
    assert son_ask.status_code == 200, son_ask.text
    son_body = son_ask.json()
    assert son_body["intent"] == "financial_memory"
    assert son_body["resolvedQuery"]["startsOn"] == "2025-01-01"
    assert son_body["resolvedQuery"]["endsOn"] == "2025-12-31"
    metrics = son_body["metrics"]
    assert metrics["grossExpenseCents"] == 10_000
    assert metrics["linkedRefundCents"] == 2_000
    assert metrics["netExpenseCents"] == 8_000
    assert metrics["splitReimbursedCents"] == 3_200
    assert metrics["ownBurdenCents"] == 4_800
    assert "48.00" in son_body["answer"]

    citation = son_body["citations"][0]
    assert citation["transactionId"] == imported_txn["id"]
    assert citation["source"] == "import"
    assert citation["sourceFilename"] == "ikea-2025.csv"
    assert citation["sourceRef"] == "CSV!2"
    assert citation["splitStatus"] == "settled"
    assert citation["linkedRefundCents"] == 2_000
    assert citation["ownBurdenCents"] == 4_800

    # Mom queries the exact same memory: her share is 40% of 80.00 = 32.00 CNY (3200 cents)
    mom_ask = client.post(
        "/api/cfo/ask",
        headers=as_member("m-mom"),
        json={
            "question": "找出去年与装修有关的开销，分开列出已退款、已分摊和最终自己承担的金额",
            "asOfDate": "2026-09-15",
        },
    ).json()
    assert mom_ask["metrics"]["ownBurdenCents"] == 3_200
    assert mom_ask["metrics"]["splitReimbursedCents"] == 4_800


def test_cfo_ask_explains_spending_changes_and_frequency_vs_ticket_drivers(client):
    # Baseline window (Apr-Jun 2026): 2 takeout orders @ 30.00 CNY = 60.00 CNY
    for day in ("2026-05-10", "2026-06-12"):
        _post_txn(
            client,
            "m-son",
            payee="美团外卖午餐",
            account_id="a-son-wallet",
            amount_cents=3_000,
            occurred_at=f"{day}T12:00:00+08:00",
            category_id="c-food",
        )
    # Current window (Jul-Sep 2026): 5 takeout orders @ 30.00 CNY = 150.00 CNY -> pure frequency!
    for day in ("2026-07-05", "2026-07-18", "2026-08-03", "2026-08-21", "2026-09-10"):
        _post_txn(
            client,
            "m-son",
            payee="美团外卖晚餐",
            account_id="a-son-wallet",
            amount_cents=3_000,
            occurred_at=f"{day}T19:00:00+08:00",
            category_id="c-food",
        )

    freq_res = client.post(
        "/api/cfo/ask",
        headers=as_member("m-son"),
        json={
            "question": "过去三个月外卖花了多少？上涨主要来自次数还是客单价？",
            "asOfDate": "2026-09-30",
        },
    )
    assert freq_res.status_code == 200
    freq_body = freq_res.json()
    assert freq_body["intent"] == "frequency_vs_ticket"
    assert freq_body["metrics"]["netExpenseCents"] == 15_000
    assert freq_body["metrics"]["previousNetExpenseCents"] == 6_000
    assert freq_body["metrics"]["expenseCount"] == 5
    assert freq_body["metrics"]["previousExpenseCount"] == 2
    assert freq_body["metrics"]["primaryGrowthDriver"] == "frequency"
    assert "次数增加" in freq_body["answer"]

    # Add big medical + education expenses in September to test month-over-month top 3 changes
    _post_txn(
        client,
        "m-son",
        payee="口腔医院检查",
        account_id="a-son-wallet",
        amount_cents=45_000,
        occurred_at="2026-09-12T10:00:00+08:00",
        category_id="c-medical",
    )
    _post_txn(
        client,
        "m-son",
        payee="在线算法课程",
        account_id="a-son-wallet",
        amount_cents=28_000,
        occurred_at="2026-09-15T20:00:00+08:00",
        category_id="c-education",
    )

    change_res = client.post(
        "/api/cfo/ask",
        headers=as_member("m-son"),
        json={
            "question": "这个月为什么多花了钱？把变化最大的三项和对应账单给我",
            "asOfDate": "2026-09-30",
        },
    )
    assert change_res.status_code == 200
    change_body = change_res.json()
    assert change_body["intent"] == "spending_change"
    top_labels = [item["label"] for item in change_body["topChanges"][:2]]
    assert top_labels == ["医疗", "教育"]
    assert len(change_body["topChanges"][0]["transactionIds"]) == 1


def test_cfo_enforces_member_isolation_and_prevents_private_keyword_oracle(client):
    # Dad's strictly private expense
    _post_txn(
        client,
        "m-dad",
        payee="爸爸私密体检中心",
        account_id="a-dad-bank",
        amount_cents=80_000,
        occurred_at="2026-08-10T09:00:00+08:00",
        category_id="c-medical",
    )
    # Dad transfers from private bank to shared family account
    _post_txn(
        client,
        "m-dad",
        txn_type="transfer",
        payee="爸爸招商银行尾号8888转入",
        account_id="a-dad-bank",
        to_account_id="a-family",
        amount_cents=50_000,
        occurred_at="2026-08-12T10:00:00+08:00",
    )

    # Admin son cannot query Dad's private account explicitly
    denied = client.post(
        "/api/cfo/ask",
        headers=as_member("m-son"),
        json={"question": "8月医疗花了多少", "accountId": "a-dad-bank", "asOfDate": "2026-08-31"},
    )
    assert denied.status_code == 403

    # Admin son asking about medical expenses sees ZERO from Dad's private bank
    son_med = client.post(
        "/api/cfo/ask",
        headers=as_member("m-son"),
        json={"question": "2026年8月医疗花了多少", "asOfDate": "2026-08-31"},
    ).json()
    assert son_med["metrics"]["grossExpenseCents"] == 0
    assert son_med["citations"] == []

    # Keyword probe for "招商" on transfer must NOT match hidden counterparty text
    oracle_probe = client.post(
        "/api/cfo/ask",
        headers=as_member("m-son"),
        json={"question": "查找与“招商”有关的账单", "asOfDate": "2026-08-31"},
    ).json()
    assert oracle_probe["citations"] == []

    # Overview of August shows the shared transfer with redacted payee only
    overview = client.post(
        "/api/cfo/ask",
        headers=as_member("m-son"),
        json={"question": "2026年8月账本总览", "asOfDate": "2026-08-31"},
    ).json()
    assert len(overview["citations"]) == 1
    assert overview["citations"][0]["payee"] == "私人账户往来（已脱敏）"
    assert "招商" not in overview["answer"]


def test_cashflow_radar_detects_recurring_bills_and_simulates_planned_events(client):
    with get_session_factory()() as db:
        family = db.get(Account, "a-family")
        assert family is not None
        family.opening_balance_minor = 1_000_000  # 10,000.00 CNY opening
        db.add(
            Account(
                id="a-son-usd-radar",
                name="美元储蓄",
                kind="asset",
                scope="personal",
                owner_id="m-son",
                currency="USD",
                opening_balance_minor=500_000,
            )
        )
        db.commit()

    # Recurring monthly rent on the 5th (3,000.00 CNY) in Jul, Aug, Sep
    for month in ("07", "08", "09"):
        _post_txn(
            client,
            "m-son",
            payee="阳光公寓房租",
            account_id="a-family",
            amount_cents=300_000,
            occurred_at=f"2026-{month}-05T09:00:00+08:00",
            category_id="c-home",
        )
    # Recurring monthly salary on the 25th (4,000.00 CNY) in Jul, Aug, Sep
    for month in ("07", "08", "09"):
        _post_txn(
            client,
            "m-son",
            txn_type="income",
            payee="科技公司月薪",
            account_id="a-family",
            amount_cents=400_000,
            occurred_at=f"2026-{month}-25T10:00:00+08:00",
            category_id="c-salary",
        )

    # Current family balance as of 2026-09-30: 10,000 - 9,000 + 12,000 = 13,000.00 CNY (1,300,000)
    # Simulate an immediate 12,000.00 CNY planned expense on 2026-10-02 (before Oct 5 rent!)
    early_trip = client.post(
        "/api/cfo/cashflow-radar",
        headers=as_member("m-son"),
        json={
            "asOfDate": "2026-09-30",
            "horizonDays": 60,
            "lookbackDays": 90,
            "minimumSafeBalanceCents": 100_000,
            "plannedEvents": [
                {
                    "title": "国庆家庭旅行",
                    "direction": "expense",
                    "amountCents": 1_200_000,
                    "occursOn": "2026-10-02",
                }
            ],
        },
    )
    assert early_trip.status_code == 200, early_trip.text
    early_body = early_trip.json()
    assert early_body["startingLiquidBalanceCents"] == 1_300_000
    assert early_body["excludedForeignAccountIds"] == ["a-son-usd-radar"]
    recurring_payees = {item["payee"]: item for item in early_body["recurringCandidates"]}
    assert "阳光公寓房租" in recurring_payees
    assert recurring_payees["阳光公寓房租"]["cadence"] == "monthly"
    assert recurring_payees["阳光公寓房租"]["nextExpectedDate"] == "2026-10-05"
    assert "科技公司月薪" in recurring_payees

    # Week 1 (Oct 1..Oct 7) has 12,000 trip + 3,000 rent = 15,000 outflow -> -2,000 CNY deficit!
    baseline_early = next(s for s in early_body["scenarios"] if s["scenario"] == "baseline")
    assert baseline_early["lowestBalanceCents"] == -200_000
    assert baseline_early["breachesSafeBalance"] is True
    assert early_body["riskAlerts"]

    # Delaying the 12,000 CNY trip to 2026-11-26 (after Nov 25 salary) avoids the Nov 5 rent crunch!
    delayed_trip = client.post(
        "/api/cfo/cashflow-radar",
        headers=as_member("m-son"),
        json={
            "asOfDate": "2026-09-30",
            "horizonDays": 60,
            "lookbackDays": 90,
            "minimumSafeBalanceCents": 100_000,
            "plannedEvents": [
                {
                    "title": "延后家庭旅行",
                    "direction": "expense",
                    "amountCents": 1_200_000,
                    "occursOn": "2026-11-26",
                }
            ],
        },
    ).json()
    baseline_delayed = next(s for s in delayed_trip["scenarios"] if s["scenario"] == "baseline")
    assert baseline_delayed["lowestBalanceCents"] == 300_000
    assert baseline_delayed["breachesSafeBalance"] is False


def test_pluggable_cfo_and_ocr_providers_enforce_schema_and_whitelist_guards(tmp_path):
    # 1. OpenAiCompatibleCfoProvider strips unauthorized IDs and rejects hallucinated numbers
    settings = get_settings().model_copy(
        update={
            "cfo_provider": "openai-compatible",
            "cfo_api_base_url": "https://llm.example.test/v1",
            "cfo_api_key": "sk-test-only",
        }
    )
    calls = 0

    def fake_cfo_transport(_url, _body, _key, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": "overview",
                                    "startsOn": "2026-08-01",
                                    "endsOn": "2026-08-31",
                                    "accountIds": ["a-dad-bank", "a-son-wallet"],
                                    "categoryIds": ["c-food", "c-injected"],
                                }
                            )
                        }
                    }
                ]
            }
        # Hallucinated narration that omits verified net_expense_cents ("12.34")
        return {"choices": [{"message": {"content": "本月净支出 999999.00 元！"}}]}

    provider = OpenAiCompatibleCfoProvider(settings=settings, transport_fn=fake_cfo_transport)
    from homedatabase_api.ai.cfo_provider import (
        CfoAccountHint,
        CfoCategoryHint,
        CfoPlanningContext,
        VerifiedCfoFacts,
    )

    plan = provider.plan_query(
        "8月餐饮花了多少",
        context=CfoPlanningContext(
            as_of_date=date(2026, 8, 31),
            categories=[CfoCategoryHint(id="c-food", name="餐饮", kind="expense", parent_id=None)],
            accounts=[
                CfoAccountHint(id="a-son-wallet", name="儿子零钱", scope="personal", currency="CNY")
            ],
        ),
    )
    assert plan.account_ids == ["a-son-wallet"]
    assert plan.category_ids == ["c-food"]

    narrated = provider.narrate(
        VerifiedCfoFacts(
            question="8月餐饮花了多少",
            intent="overview",
            starts_on=date(2026, 8, 1),
            ends_on=date(2026, 8, 31),
            comparison_starts_on=None,
            comparison_ends_on=None,
            gross_expense_cents=1234,
            linked_refund_cents=0,
            net_expense_cents=1234,
            split_reimbursed_cents=0,
            own_burden_cents=1234,
            income_cents=0,
            net_cashflow_cents=-1234,
            expense_count=1,
            average_expense_cents=1234,
            previous_net_expense_cents=None,
            net_expense_delta_cents=None,
            previous_expense_count=None,
            previous_average_expense_cents=None,
            primary_growth_driver="none",
            top_changes=[],
            citation_count=1,
        )
    )
    # Hallucinated "999999.00" rejected in favor of deterministic verified answer "12.34"
    assert "999999.00" not in narrated
    assert "12.34" in narrated

    # 2. OpenAiVisionOcrProvider validates structured output through authoritative money parser
    buf = io.BytesIO()
    Image.new("RGB", (120, 60), "white").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    def fake_vision_transport(_url, body, _key, _timeout):
        assert body["response_format"]["type"] == "json_schema"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "transactions": [
                                    {
                                        "occurredAt": "2026-08-20T18:00:00+08:00",
                                        "payee": "视觉模型餐厅",
                                        "direction": "expense",
                                        "amount": "56.78",
                                        "currency": "CNY",
                                        "category": "餐饮",
                                        "sourceRef": "page-001#1",
                                        "confidence": 0.94,
                                    }
                                ],
                                "warnings": [],
                            }
                        )
                    }
                }
            ]
        }

    vision = OpenAiVisionOcrProvider(
        render_pages_fn=ocr_tasks._render_chunk_pages,
        transport_fn=fake_vision_transport,
    )
    vision_settings = get_settings().model_copy(
        update={
            "ocr_enabled": True,
            "ocr_provider": "openai-vision",
            "ocr_api_base_url": "https://vision.example.test/v1",
            "ocr_api_key": "sk-vision",
        }
    )
    outcome = vision.extract_chunk(
        chunk=BillOcrChunkRequest(
            source_file_id="file-1",
            batch_id="batch-1",
            filename="receipt.png",
            encrypted_path=tmp_path / "dummy.hdsrc",
            page_start=0,
            page_end=1,
        ),
        payload=png_bytes,
        settings=vision_settings,
    )
    assert outcome.status == "parsed"
    assert outcome.candidate_count == 1
    assert outcome.rows[0].amount_minor == 5678
    assert outcome.rows[0].payee == "视觉模型餐厅"

    # Prompt-injected extra key in vision output is rejected
    bad_vision = OpenAiVisionOcrProvider(
        render_pages_fn=ocr_tasks._render_chunk_pages,
        transport_fn=lambda *_args: {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "transactions": [],
                                "warnings": [],
                                "exfiltrate": "secret",
                            }
                        )
                    }
                }
            ]
        },
    )
    with pytest.raises(OcrResultError):
        bad_vision.extract_chunk(
            chunk=BillOcrChunkRequest(
                source_file_id="file-2",
                batch_id="batch-1",
                filename="receipt.png",
                encrypted_path=tmp_path / "dummy.hdsrc",
                page_start=0,
                page_end=1,
            ),
            payload=png_bytes,
            settings=vision_settings,
        )


def test_production_ai_provider_settings_fail_closed_without_https_and_keys():
    base_prod = {
        "environment": "production",
        "database_url": "postgresql+psycopg://ledger:secret@postgres/homedatabase",
        "dev_member_id": None,
        "session_cookie_secure": True,
        "auto_create_schema": False,
        "webauthn_rp_id": "finance.example.test",
        "webauthn_origins": ["https://finance.example.test"],
        "allowed_hosts": ["finance.example.test"],
        "source_encryption_key": "A" * 43,
        "backup_encryption_key": "B" * 43,
    }
    with pytest.raises(ValidationError, match="HTTPS cfo_api_base_url"):
        Settings(
            **base_prod,
            cfo_provider="openai-compatible",
            cfo_api_base_url="http://insecure.example.test/v1",
            cfo_api_key="sk-test",
        )
    with pytest.raises(ValidationError, match="HOMEDATABASE_OCR_API_KEY"):
        Settings(
            **base_prod,
            ocr_enabled=True,
            ocr_provider="openai-vision",
            ocr_api_base_url="https://vision.example.test/v1",
            ocr_api_key=None,
        )


def test_custom_statement_parser_plugin_and_layered_router_decoupling(client):
    class CustomJsonlBillParser:
        extensions = (".hdjsonl",)

        def parse(
            self,
            path: Path,
            *,
            max_rows: int,
            max_uncompressed_bytes: int,
        ) -> ParseOutcome:
            _ = (max_rows, max_uncompressed_bytes)
            item = json.loads(path.read_text(encoding="utf-8"))
            from datetime import datetime
            from decimal import Decimal

            row = ParsedRow(
                source_ref="jsonl!1",
                raw_payload=item,
                source_hash="custom-hash-001",
                occurred_at=datetime.fromisoformat(item["date"]),
                payee=item["payee"],
                direction="expense",
                amount_minor=int(item["amountCents"]),
                currency="CNY",
                original_category="餐饮",
                confidence=Decimal("0.99"),
            )
            return ParseOutcome(status="parsed", message="自定义插件解析 1 条", rows=[row])

    register_statement_parser(CustomJsonlBillParser())
    payload = json.dumps(
        {
            "date": "2026-08-18T12:00:00+08:00",
            "payee": "插件账单咖啡馆",
            "amountCents": 2600,
        }
    ).encode("utf-8")
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("custom.hdjsonl", payload, "application/json"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["candidateCount"] == 1
    assert uploaded.json()["candidates"][0]["payee"] == "插件账单咖啡馆"

    # Verify HTTP routers do not construct raw SQLAlchemy select() queries directly
    for router_module in (auth_router, cfo_router, ledger_router, system_router):
        source = inspect.getsource(router_module)
        assert "select(" not in source, f"{router_module.__name__} leaked SQLAlchemy select()"

    # Verify OpenAPI documents all new CFO endpoints with security & bounded *Cents
    openapi = app.openapi()
    for route_path in ("/api/cfo/capabilities", "/api/cfo/ask", "/api/cfo/cashflow-radar"):
        assert route_path in openapi["paths"]


def test_wechat_csv_header_priority_and_candidate_direction_patch(client):
    wechat_csv = (
        "微信支付账单明细\n"
        "昵称：[示例用户]\n"
        "----------------------微信支付账单明细列表--------------------\n"
        "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态\n"
        "2026-08-18 12:00:00,微信红包,老同学,生日快乐,支出,¥66.00,零钱,支付成功\n"
        "2026-08-18 13:15:00,群收款,球友群,羽毛球场地费,收入,¥45.50,零钱,已收钱\n"
    ).encode("utf-8-sig")
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"source": "wechat", "targetAccountId": "a-son-wallet"},
        files=[("files", ("wechat-export.csv", wechat_csv, "text/csv"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["candidateCount"] == 2
    by_payee = {c["payee"]: c for c in body["candidates"]}
    assert by_payee["老同学"]["direction"] == "expense"
    assert by_payee["老同学"]["amountCents"] == 6_600
    assert by_payee["球友群"]["direction"] == "income"
    assert by_payee["球友群"]["amountCents"] == 4_550

    # Patch direction from expense -> income clears incompatible expense category automatically
    cand = by_payee["老同学"]
    with_cat = client.patch(
        f"/api/import-batches/{body['id']}/candidates/{cand['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": cand["version"], "suggestedCategoryId": "c-food"},
    )
    assert with_cat.status_code == 200
    flipped = client.patch(
        f"/api/import-batches/{body['id']}/candidates/{cand['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": with_cat.json()["version"], "direction": "income"},
    )
    assert flipped.status_code == 200
    assert flipped.json()["direction"] == "income"
    assert flipped.json()["suggestedCategoryId"] is None


def test_alipay_category_synonyms_and_permission_safe_payee_memory(client):
    # Dad categorizes a private merchant in his private account -> must NOT leak to Son
    _post_txn(
        client,
        "m-dad",
        payee="私房书店",
        account_id="a-dad-bank",
        amount_cents=5_000,
        occurred_at="2026-08-01T10:00:00+08:00",
        category_id="c-education",
    )
    # Son categorizes his own merchant -> SHOULD be remembered on next uncategorized CSV
    _post_txn(
        client,
        "m-son",
        payee="山姆会员商店",
        account_id="a-son-wallet",
        amount_cents=20_000,
        occurred_at="2026-08-02T10:00:00+08:00",
        category_id="c-grocery",
    )

    alipay_csv = (
        "交易时间,交易对方,收/支,金额,交易分类\n"
        "2026-08-20 12:00:00,街角面馆,支出,24.00,餐饮美食\n"
        "2026-08-20 15:00:00,山姆会员商店,支出,198.00,\n"
        "2026-08-20 17:00:00,私房书店,支出,50.00,\n"
    ).encode("utf-8-sig")
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"source": "alipay", "targetAccountId": "a-son-wallet"},
        files=[("files", ("alipay.csv", alipay_csv, "text/csv"))],
    )
    assert uploaded.status_code == 201
    by_payee = {c["payee"]: c for c in uploaded.json()["candidates"]}
    assert by_payee["街角面馆"]["suggestedCategoryId"] == "c-food"
    assert by_payee["山姆会员商店"]["suggestedCategoryId"] == "c-grocery"
    assert by_payee["私房书店"]["suggestedCategoryId"] is None
