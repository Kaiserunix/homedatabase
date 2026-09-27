from __future__ import annotations

from io import BytesIO

from conftest import as_member
from openpyxl import load_workbook


def expense_payload(*, account_id: str = "a-son-wallet", amount: int = 1234):
    return {
        "type": "expense",
        "payee": "向XX超市付款",
        "note": "",
        "categoryId": None,
        "accountId": account_id,
        "amountCents": amount,
        "currency": "CNY",
        "date": "2026-08-05T12:30:00+08:00",
    }


def test_password_login_cookie_and_logout(client):
    unauthenticated = client.get("/api/auth/me")
    assert unauthenticated.status_code == 401

    login = client.post("/api/auth/login", json={"username": "son", "password": "test-password"})
    assert login.status_code == 200
    assert login.json()["member"]["identity"] == "son"
    assert login.cookies.get("hd_session")

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == "m-son"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_private_accounts_and_transactions_do_not_leak_to_admin(client):
    son_accounts = client.get("/api/accounts", headers=as_member("m-son"))
    assert son_accounts.status_code == 200
    son_ids = {item["id"] for item in son_accounts.json()}
    assert "a-son-wallet" in son_ids
    assert "a-family" in son_ids
    assert "a-dad-bank" not in son_ids

    created = client.post(
        "/api/transactions",
        headers=as_member("m-dad"),
        json=expense_payload(account_id="a-dad-bank"),
    )
    assert created.status_code == 201

    son_transactions = client.get("/api/transactions", headers=as_member("m-son"))
    assert son_transactions.status_code == 200
    assert son_transactions.json() == []

    dad_transactions = client.get("/api/transactions", headers=as_member("m-dad"))
    assert [item["id"] for item in dad_transactions.json()] == [created.json()["id"]]


def test_expense_can_be_uncategorised_and_is_balanced(client):
    response = client.post("/api/transactions", headers=as_member("m-son"), json=expense_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["payee"] == "向XX超市付款"
    assert body["categoryId"] is None
    assert sum(line["debitCents"] for line in body["lines"]) == 1234
    assert sum(line["creditCents"] for line in body["lines"]) == 1234

    missing_payee = expense_payload()
    missing_payee["payee"] = "   "
    assert (
        client.post("/api/transactions", headers=as_member("m-son"), json=missing_payee).status_code
        == 422
    )


def test_transfer_and_refund_keep_double_entry_balanced(client):
    transfer_payload = {
        "type": "transfer",
        "payee": "家庭账户调拨",
        "accountId": "a-son-wallet",
        "toAccountId": "a-family",
        "amountCents": 5000,
        "currency": "CNY",
        "date": "2026-08-05T08:00:00+08:00",
    }
    transfer = client.post("/api/transactions", headers=as_member("m-son"), json=transfer_payload)
    assert transfer.status_code == 201
    assert sum(x["debitCents"] for x in transfer.json()["lines"]) == 5000
    assert sum(x["creditCents"] for x in transfer.json()["lines"]) == 5000

    expense = client.post(
        "/api/transactions", headers=as_member("m-son"), json=expense_payload(amount=888)
    )
    refund_payload = {
        "type": "refund",
        "payee": "XX超市退款",
        "accountId": "a-son-wallet",
        "amountCents": 300,
        "currency": "CNY",
        "date": "2026-08-06T09:00:00+08:00",
        "refundOf": expense.json()["id"],
    }
    refund = client.post("/api/transactions", headers=as_member("m-son"), json=refund_payload)
    assert refund.status_code == 201
    assert refund.json()["refundOf"] == expense.json()["id"]
    assert sum(x["debitCents"] for x in refund.json()["lines"]) == 300
    assert sum(x["creditCents"] for x in refund.json()["lines"]) == 300


def test_split_recipient_must_explicitly_accept_and_material_edit_invalidates(client):
    transaction = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(account_id="a-family", amount=10000),
    ).json()
    created = client.post(
        "/api/splits",
        headers=as_member("m-son"),
        json={
            "txnId": transaction["id"],
            "totalCents": 10000,
            "shares": [
                {"memberId": "m-son", "shareCents": 6000},
                {"memberId": "m-mom", "shareCents": 4000},
            ],
        },
    )
    assert created.status_code == 201
    split = created.json()
    assert split["title"] == "向XX超市付款"
    mom_share = next(x for x in split["shares"] if x["memberId"] == "m-mom")
    assert mom_share["status"] == "pending"

    accepted = client.post(
        f"/api/splits/{split['id']}/respond",
        headers=as_member("m-mom"),
        json={"accept": True, "expectedVersion": split["version"]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "settled"

    edited = client.patch(
        f"/api/transactions/{transaction['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": transaction["version"], "payee": "更正后的账面名称"},
    )
    assert edited.status_code == 200
    refreshed = client.get("/api/splits", headers=as_member("m-mom")).json()[0]
    assert refreshed["title"] == "更正后的账面名称"
    assert refreshed["status"] == "open"
    assert next(x for x in refreshed["shares"] if x["memberId"] == "m-mom")["status"] == "pending"


def test_tier_five_export_is_one_workbook_with_five_progressive_sheets(client):
    client.post("/api/transactions", headers=as_member("m-son"), json=expense_payload(amount=4321))
    response = client.get(
        "/api/reports/export",
        headers=as_member("m-son"),
        params={"tier": 5, "from": "2026-08-01", "to": "2026-08-31"},
    )
    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    workbook = load_workbook(BytesIO(response.content), read_only=False, data_only=False)
    assert workbook.sheetnames == ["阅读账单", "交易明细", "复式分录", "核对汇总", "统计分析"]
    assert workbook["阅读账单"]["B2"].value == "向XX超市付款"
    assert len(workbook["统计分析"]._charts) == 2
