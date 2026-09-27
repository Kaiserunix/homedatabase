from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from conftest import as_member
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from homedatabase_api.database import get_session_factory
from homedatabase_api.main import app
from homedatabase_api.models import Account, AccountGrant, ImportBatch


def expense_payload(
    *,
    account_id: str = "a-son-wallet",
    amount: int = 1_000,
    date: str = "2026-08-05T12:30:00+08:00",
    payee: str = "向XX超市付款",
    category_id: str | None = "c-food",
) -> dict[str, object]:
    return {
        "type": "expense",
        "payee": payee,
        "note": "",
        "categoryId": category_id,
        "accountId": account_id,
        "amountCents": amount,
        "currency": "CNY",
        "date": date,
    }


def test_revoked_manage_grant_blocks_creator_edits_and_batch_access(client):
    transaction = client.post(
        "/api/transactions",
        headers=as_member("m-dad"),
        json=expense_payload(account_id="a-family"),
    ).json()

    with get_session_factory()() as db:
        family_batch = ImportBatch(
            owner_id="m-dad",
            target_account_id="a-family",
            filename="family.csv",
            source="csv",
        )
        private_batch = ImportBatch(
            owner_id="m-dad",
            target_account_id="a-dad-bank",
            filename="private.csv",
            source="csv",
        )
        db.add_all([family_batch, private_batch])
        db.flush()
        family_batch_id = family_batch.id
        private_batch_id = private_batch.id
        grant = db.get(AccountGrant, ("a-family", "m-dad"))
        assert grant is not None
        grant.permission = "view"
        db.commit()

    update = client.patch(
        f"/api/transactions/{transaction['id']}",
        headers=as_member("m-dad"),
        json={"expectedVersion": transaction["version"], "payee": "不应成功"},
    )
    assert update.status_code == 403

    # An inaccessible private batch must be filtered, not turn the entire list
    # into a 403. A batch owner also loses access when target-account manage
    # permission is explicitly revoked.
    son_batches = client.get("/api/import-batches", headers=as_member("m-son"))
    assert son_batches.status_code == 200
    assert [item["id"] for item in son_batches.json()] == [family_batch_id]

    dad_batches = client.get("/api/import-batches", headers=as_member("m-dad"))
    assert dad_batches.status_code == 200
    assert [item["id"] for item in dad_batches.json()] == [private_batch_id]
    denied = client.get(f"/api/import-batches/{family_batch_id}", headers=as_member("m-dad"))
    assert denied.status_code == 403


def test_native_currency_balances_and_liability_sign_are_not_base_currency(client):
    with get_session_factory()() as db:
        db.add_all(
            [
                Account(
                    id="a-son-usd",
                    name="儿子美元账户",
                    kind="asset",
                    scope="personal",
                    owner_id="m-son",
                    currency="USD",
                    opening_balance_minor=10_000,
                ),
                Account(
                    id="a-son-card",
                    name="儿子信用卡",
                    kind="liability",
                    scope="personal",
                    owner_id="m-son",
                    currency="CNY",
                    opening_balance_minor=0,
                ),
            ]
        )
        db.commit()

    usd_expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            **expense_payload(account_id="a-son-usd", amount=1_000),
            "currency": "USD",
            "baseAmountCents": 7_200,
        },
    )
    assert usd_expense.status_code == 201
    usd_body = usd_expense.json()
    assert usd_body["currency"] == "USD"
    assert usd_body["baseCurrency"] == "CNY"
    assert sum(line["debitCents"] for line in usd_body["lines"]) == 7_200
    usd_line = next(line for line in usd_body["lines"] if line["accountId"] == "a-son-usd")
    assert usd_line["accountAmountCents"] == 1_000
    assert usd_line["accountCurrency"] == "USD"

    split = client.post(
        "/api/splits",
        headers=as_member("m-son"),
        json={
            "txnId": usd_body["id"],
            "totalCents": 1_000,
            "shares": [
                {"memberId": "m-son", "shareCents": 600},
                {"memberId": "m-mom", "shareCents": 400},
            ],
        },
    )
    assert split.status_code == 201, split.text
    assert split.json()["currency"] == "USD"

    card_expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(account_id="a-son-card", amount=5_000),
    )
    assert card_expense.status_code == 201

    balances = {
        item["id"]: item["balanceCents"]
        for item in client.get("/api/accounts", headers=as_member("m-son")).json()
    }
    assert balances["a-son-usd"] == 9_000
    assert balances["a-son-card"] == 5_000

    mismatched_transfer = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "transfer",
            "payee": "错误币种转账",
            "accountId": "a-son-wallet",
            "toAccountId": "a-family",
            "amountCents": 100,
            "currency": "USD",
            "baseAmountCents": 720,
            "date": "2026-08-05T12:30:00+08:00",
        },
    )
    assert mismatched_transfer.status_code == 422


def test_shared_account_movements_are_reconcilable_without_private_account_leak(client):
    transfer = client.post(
        "/api/transactions",
        headers=as_member("m-dad"),
        json={
            "type": "transfer",
            "payee": "爸爸招商银行尾号1234转入",
            "accountId": "a-dad-bank",
            "toAccountId": "a-family",
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-05T12:30:00+08:00",
        },
    )
    assert transfer.status_code == 201
    assert transfer.json()["payee"] == "爸爸招商银行尾号1234转入"

    balances = {
        item["id"]: item["balanceCents"]
        for item in client.get("/api/accounts", headers=as_member("m-son")).json()
    }
    assert balances["a-family"] == 100

    visible = client.get("/api/transactions", headers=as_member("m-son")).json()
    assert len(visible) == 1
    projected = visible[0]
    assert projected["payee"] == "私人账户往来（已脱敏）"
    assert projected["note"] == ""
    assert "a-dad-bank" not in {line["accountId"] for line in projected["lines"]}
    assert "private:counterparty" in {line["accountId"] for line in projected["lines"]}
    assert sum(line["debitCents"] for line in projected["lines"]) == sum(
        line["creditCents"] for line in projected["lines"]
    )
    assert (
        client.get(
            "/api/transactions",
            headers=as_member("m-son"),
            params={"keyword": "招商"},
        ).json()
        == []
    )

    report = client.get(
        "/api/reports/export",
        headers=as_member("m-son"),
        params={"tier": 4, "from": "2026-08-01", "to": "2026-08-31"},
    )
    workbook = load_workbook(BytesIO(report.content), read_only=False, data_only=False)
    reconcile = workbook["核对汇总"]
    family_row = next(row for row in reconcile.iter_rows() if row[0].value == "家庭公共账户")
    assert family_row[2].value == "1.00"
    journal_text = "\n".join(
        str(cell.value or "") for row in workbook["复式分录"].iter_rows() for cell in row
    )
    assert "私人账户（已脱敏）" in journal_text
    assert "爸爸银行卡" not in journal_text
    assert "招商" not in journal_text


def test_refunds_inherit_category_are_capped_and_block_original_deletion(client):
    expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(account_id="a-son-wallet", amount=1_000),
    ).json()
    refund = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "XX超市退款",
            "accountId": "a-son-wallet",
            "amountCents": 600,
            "currency": "CNY",
            "date": "2026-08-06T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    )
    assert refund.status_code == 201
    assert refund.json()["categoryId"] == "c-food"

    excessive = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "超额退款",
            "accountId": "a-son-wallet",
            "amountCents": 401,
            "currency": "CNY",
            "date": "2026-08-07T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    )
    assert excessive.status_code == 422

    deletion = client.request(
        "DELETE",
        f"/api/transactions/{expense['id']}",
        headers=as_member("m-son"),
        json={"reason": "不应掩盖已发生退款"},
    )
    assert deletion.status_code == 409


def test_refund_restore_rechecks_cumulative_cap(client):
    expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(account_id="a-son-wallet", amount=100),
    ).json()
    recycled_refund = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "第一笔全额退款",
            "accountId": "a-son-wallet",
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-06T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    ).json()
    deleted = client.request(
        "DELETE",
        f"/api/transactions/{recycled_refund['id']}",
        headers=as_member("m-son"),
        json={"reason": "暂时撤销"},
    )
    assert deleted.status_code == 204

    replacement = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "替代全额退款",
            "accountId": "a-son-wallet",
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-07T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    )
    assert replacement.status_code == 201

    blocked = client.post(
        f"/api/transactions/{recycled_refund['id']}/restore",
        headers=as_member("m-son"),
        json={"expectedVersion": 2},
    )
    assert blocked.status_code == 422
    active_refunds = [
        item
        for item in client.get("/api/transactions", headers=as_member("m-son")).json()
        if item["refundOf"] == expense["id"]
    ]
    assert [item["id"] for item in active_refunds] == [replacement.json()["id"]]


def test_refund_restore_requires_live_original_and_resyncs_category(client):
    expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(category_id="c-food", amount=100),
    ).json()
    refund = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "回收站退款",
            "accountId": "a-son-wallet",
            "amountCents": 50,
            "currency": "CNY",
            "date": "2026-08-06T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    ).json()
    assert (
        client.request(
            "DELETE",
            f"/api/transactions/{refund['id']}",
            headers=as_member("m-son"),
            json={"reason": "等待核对"},
        ).status_code
        == 204
    )
    edited = client.patch(
        f"/api/transactions/{expense['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": expense["version"], "categoryId": "c-transport"},
    )
    assert edited.status_code == 200

    restored = client.post(
        f"/api/transactions/{refund['id']}/restore",
        headers=as_member("m-son"),
        json={"expectedVersion": 2},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["categoryId"] == "c-transport"
    assert "expense:c-transport" in {line["ledgerCode"] for line in restored.json()["lines"]}

    orphan_expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(category_id="c-food", amount=100),
    ).json()
    orphan_refund = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "不能孤立恢复",
            "accountId": "a-son-wallet",
            "amountCents": 50,
            "currency": "CNY",
            "date": "2026-08-08T09:00:00+08:00",
            "refundOf": orphan_expense["id"],
        },
    ).json()
    assert (
        client.request(
            "DELETE",
            f"/api/transactions/{orphan_refund['id']}",
            headers=as_member("m-son"),
            json={"reason": "先撤销退款"},
        ).status_code
        == 204
    )
    assert (
        client.request(
            "DELETE",
            f"/api/transactions/{orphan_expense['id']}",
            headers=as_member("m-son"),
            json={"reason": "再撤销原支出"},
        ).status_code
        == 204
    )
    orphan_restore = client.post(
        f"/api/transactions/{orphan_refund['id']}/restore",
        headers=as_member("m-son"),
        json={"expectedVersion": 2},
    )
    assert orphan_restore.status_code == 422


def test_category_edits_keep_header_entry_lines_and_refunds_in_sync(client):
    expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(category_id="c-food"),
    ).json()
    refund = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "同步分类退款",
            "accountId": "a-son-wallet",
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-06T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    ).json()

    edited = client.patch(
        f"/api/transactions/{expense['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": expense["version"], "categoryId": "c-transport"},
    )
    assert edited.status_code == 200
    assert edited.json()["categoryId"] == "c-transport"
    assert "expense:c-transport" in {line["ledgerCode"] for line in edited.json()["lines"]}

    refreshed_refund = client.get(
        f"/api/transactions/{refund['id']}", headers=as_member("m-son")
    ).json()
    assert refreshed_refund["categoryId"] == "c-transport"
    assert "expense:c-transport" in {line["ledgerCode"] for line in refreshed_refund["lines"]}
    rejected = client.patch(
        f"/api/transactions/{refund['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": refreshed_refund["version"], "categoryId": "c-food"},
    )
    assert rejected.status_code == 422


def test_original_category_edit_cannot_mutate_a_refund_in_a_locked_period(client):
    expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(category_id="c-food"),
    ).json()
    refund = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "锁定期退款",
            "accountId": "a-son-wallet",
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-09-01T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    ).json()
    locked = client.post(
        "/api/period-locks",
        headers=as_member("m-son"),
        json={"startsOn": "2026-09-01", "endsOn": "2026-09-01", "reason": "月结"},
    )
    assert locked.status_code == 201, locked.text

    edited = client.patch(
        f"/api/transactions/{expense['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": expense["version"], "categoryId": "c-transport"},
    )
    assert edited.status_code == 409, edited.text
    assert edited.json()["detail"] == "该账期已锁定"

    unchanged_expense = client.get(
        f"/api/transactions/{expense['id']}", headers=as_member("m-son")
    ).json()
    unchanged_refund = client.get(
        f"/api/transactions/{refund['id']}", headers=as_member("m-son")
    ).json()
    assert (unchanged_expense["categoryId"], unchanged_expense["version"]) == (
        "c-food",
        expense["version"],
    )
    assert (unchanged_refund["categoryId"], unchanged_refund["version"]) == (
        "c-food",
        refund["version"],
    )


def test_split_change_invalidates_every_recipient_and_rejects_stale_acceptance(client):
    transaction = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(account_id="a-family", amount=10_000),
    ).json()
    split = client.post(
        "/api/splits",
        headers=as_member("m-son"),
        json={
            "txnId": transaction["id"],
            "totalCents": 10_000,
            "shares": [
                {"memberId": "m-son", "shareCents": 4_000},
                {"memberId": "m-mom", "shareCents": 3_000},
                {"memberId": "m-dad", "shareCents": 3_000},
            ],
        },
    ).json()
    for member_id in ("m-mom", "m-dad"):
        accepted = client.post(
            f"/api/splits/{split['id']}/respond",
            headers=as_member(member_id),
            json={"accept": True, "expectedVersion": split["version"]},
        )
        assert accepted.status_code == 200
    assert accepted.json()["status"] == "settled"

    changed = client.patch(
        f"/api/splits/{split['id']}/shares/m-mom",
        headers=as_member("m-son"),
        json={"shareCents": 3_500, "expectedVersion": split["version"]},
    )
    assert changed.status_code == 200
    changed_body = changed.json()
    assert changed_body["version"] == 2
    recipient_statuses = {share["memberId"]: share["status"] for share in changed_body["shares"]}
    assert recipient_statuses["m-mom"] == "pending"
    assert recipient_statuses["m-dad"] == "pending"

    stale = client.post(
        f"/api/splits/{split['id']}/respond",
        headers=as_member("m-dad"),
        json={"accept": True, "expectedVersion": 1},
    )
    assert stale.status_code == 409

    deletion = client.request(
        "DELETE",
        f"/api/transactions/{transaction['id']}",
        headers=as_member("m-son"),
        json={"reason": "已有分摊不能直接删除"},
    )
    assert deletion.status_code == 409


def test_report_uses_period_opening_refund_net_and_never_executes_cell_text(client):
    with get_session_factory()() as db:
        account = db.get(Account, "a-son-wallet")
        assert account is not None
        account.opening_balance_minor = 10_000
        db.commit()

    july_expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(
            amount=1_000,
            date="2026-07-15T12:00:00+08:00",
            payee="七月支出",
        ),
    ).json()
    client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": '=HYPERLINK("https://evil.example","click")',
            "accountId": "a-son-wallet",
            "amountCents": 300,
            "currency": "CNY",
            "date": "2026-08-05T09:00:00+08:00",
            "refundOf": july_expense["id"],
        },
    )
    client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=expense_payload(amount=500, date="2026-08-10T12:00:00+08:00"),
    )

    response = client.get(
        "/api/reports/export",
        headers=as_member("m-son"),
        params={"tier": 5, "from": "2026-08-01", "to": "2026-08-31"},
    )
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content), read_only=False, data_only=False)

    readable = workbook["阅读账单"]
    formula_like_cell = next(
        cell for cell in readable["B"] if cell.value and "HYPERLINK" in cell.value
    )
    assert formula_like_cell.data_type == "s"

    reconcile = workbook["核对汇总"]
    wallet_row = next(row for row in reconcile.iter_rows() if row[0].value == "儿子零钱")
    assert wallet_row[1].value == "90.00"  # 100 opening - 10 July expense
    assert wallet_row[2].value == "3.00"  # refund increases the asset
    assert wallet_row[3].value == "5.00"  # August expense decreases it
    assert wallet_row[4].value == "88.00"

    stats = workbook["统计分析"]
    assert stats["B6"].value == "2.00"  # August net expense: 5 - 3 refund


def test_bearer_logout_revokes_the_presented_session(reset_database):
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "son", "password": "test-password"}
        )
        token = login.cookies.get("hd_session")
        assert token
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_import_batch_permission_query_does_not_disclose_deleted_accounts(client):
    with get_session_factory()() as db:
        batch = ImportBatch(
            owner_id="m-dad",
            target_account_id="a-dad-bank",
            filename="private.csv",
            source="csv",
            imported_at=datetime.now(UTC),
        )
        db.add(batch)
        db.commit()
        batch_id = batch.id
        account = db.scalar(select(Account).where(Account.id == "a-dad-bank"))
        assert account is not None
        account.deleted_at = datetime.now(UTC)
        db.commit()

    assert client.get("/api/import-batches", headers=as_member("m-dad")).json() == []
    assert (
        client.get(f"/api/import-batches/{batch_id}", headers=as_member("m-dad")).status_code == 403
    )
