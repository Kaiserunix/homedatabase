from __future__ import annotations

from io import BytesIO

from conftest import as_member
from openpyxl import Workbook, load_workbook

from homedatabase_api.database import get_session_factory
from homedatabase_api.main import app
from homedatabase_api.models import SourceRecord
from homedatabase_api.money import MAX_SAFE_MINOR


def _expense(amount_cents: int, *, account_id: str = "a-son-wallet", payee: str = "边界金额"):
    return {
        "type": "expense",
        "payee": payee,
        "accountId": account_id,
        "amountCents": amount_cents,
        "currency": "CNY",
        "date": "2026-08-05T12:30:00+08:00",
    }


def _create_account(client, name: str, opening_balance: int = 0) -> str:
    created = client.post(
        "/api/accounts",
        headers=as_member("m-son"),
        json={
            "name": name,
            "kind": "asset",
            "scope": "personal",
            "currency": "CNY",
            "openingBalanceCents": opening_balance,
            "icon": "WalletCards",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_manual_transaction_rejects_unsafe_integer_and_accepts_max(client):
    coerced_string = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(100), "amountCents": "100"},
    )
    assert coerced_string.status_code == 422

    rejected = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=_expense(MAX_SAFE_MINOR + 1),
    )
    assert rejected.status_code == 422

    accepted = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=_expense(MAX_SAFE_MINOR),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["amountCents"] == MAX_SAFE_MINOR


def test_openapi_bounds_every_cents_field_to_safe_integer():
    schemas = app.openapi()["components"]["schemas"]
    cents_fields = []
    for schema_name, schema in schemas.items():
        for field_name, field_schema in schema.get("properties", {}).items():
            if not field_name.endswith("Cents"):
                continue
            integer_schema = next(
                (
                    option
                    for option in field_schema.get("anyOf", [field_schema])
                    if option.get("type") == "integer"
                ),
                None,
            )
            assert integer_schema is not None, (schema_name, field_name)
            assert integer_schema.get("maximum") == MAX_SAFE_MINOR, (schema_name, field_name)
            cents_fields.append((schema_name, field_name))
    assert {
        ("AccountOut", "balanceCents"),
        ("AccountCreateInput", "openingBalanceCents"),
        ("EntryLineOut", "debitCents"),
        ("TransactionOut", "amountCents"),
        ("ImportCandidateOut", "amountCents"),
        ("SplitOut", "totalCents"),
    } <= set(cents_fields)


def test_fx_rate_rejects_zero_and_safe_integer_overflow(client):
    zero = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(100), "currency": "USD", "fxRate": "0"},
    )
    assert zero.status_code == 422

    excessive_precision = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(100), "currency": "USD", "fxRate": "1.12345678901"},
    )
    assert excessive_precision.status_code == 422

    rounds_to_zero = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(1), "currency": "USD", "fxRate": "0.001"},
    )
    assert rounds_to_zero.status_code == 422

    overflow = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(MAX_SAFE_MINOR), "currency": "USD", "fxRate": "2"},
    )
    assert overflow.status_code == 422


def test_base_currency_fx_rate_must_be_exactly_one(client):
    rounded_to_same_cent = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(1), "fxRate": "1.999"},
    )
    assert rounded_to_same_cent.status_code == 422

    small_difference = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(100), "fxRate": "1.004"},
    )
    assert small_difference.status_code == 422

    exact = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(100), "fxRate": "1.0"},
    )
    assert exact.status_code == 201, exact.text
    assert exact.json()["baseAmountCents"] == 100


def test_import_candidate_fx_rejects_the_same_conversion_overflow(client):
    content = (
        "交易时间,交易对方,收支类型,金额,币种\n"
        "2026-08-05 12:30:00,外币边界,支出,90071992547409.91,USD\n"
    ).encode()
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("fx-overflow.csv", content, "text/csv"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    candidate = uploaded.json()["candidates"][0]
    patched = client.patch(
        f"/api/import-batches/{uploaded.json()['id']}/candidates/{candidate['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": candidate["version"], "fxRate": "2"},
    )
    assert patched.status_code == 422


def test_import_rejects_unsafe_amounts_without_candidates(client):
    content = (
        "交易时间,交易对方,收支类型,金额\n"
        "2026-08-05 12:30:00,超界账单,支出,90071992547409.92\n"
        "2026-08-05 12:31:00,三位小数,支出,1.001\n"
    ).encode()
    response = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("unsafe.csv", content, "text/csv"))],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["candidates"] == []
    assert body["files"][0]["rejectedCount"] == 2
    assert "金额超出安全范围" in body["files"][0]["parseMessage"]
    assert "金额超过两位小数" in body["files"][0]["parseMessage"]
    with get_session_factory()() as db:
        issue_sets = [record.raw_payload["issues"] for record in db.query(SourceRecord).all()]
        assert any("amount_exceeds_safe_range" in issues for issues in issue_sets)
        assert any("amount_has_more_than_two_decimal_places" in issues for issues in issue_sets)


def test_import_rejects_ambiguous_large_xlsx_float(client):
    output = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["交易时间", "交易对方", "收支类型", "金额"])
    sheet.append(["2026-08-05 12:30:00", "浮点大数", "支出", 90071992547409.91])
    workbook.save(output)

    response = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[
            (
                "files",
                (
                    "ambiguous.xlsx",
                    output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
        ],
    )
    assert response.status_code == 201, response.text
    assert response.json()["candidates"] == []
    assert "XLSX 大数精度不明确" in response.json()["files"][0]["parseMessage"]
    with get_session_factory()() as db:
        record = db.query(SourceRecord).one()
        assert "ambiguous_xlsx_float_amount" in record.raw_payload["issues"]


def test_import_confirm_rejects_projected_balance_overflow_atomically(client):
    content = (
        "交易时间,交易对方,收支类型,金额\n"
        "2026-08-05 12:30:00,第一笔,支出,90071992547409.91\n"
        "2026-08-05 12:31:00,第二笔,支出,0.01\n"
    ).encode()
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("overflow-batch.csv", content, "text/csv"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    candidate_ids = [item["id"] for item in uploaded.json()["candidates"]]
    assert len(candidate_ids) == 2

    confirmed = client.post(
        f"/api/import-batches/{uploaded.json()['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": candidate_ids},
    )
    assert confirmed.status_code == 422, confirmed.text
    transactions = client.get("/api/transactions", headers=as_member("m-son"))
    assert transactions.status_code == 200
    assert transactions.json() == []


def test_delete_and_restore_reject_projected_balance_overflow(client):
    delete_account = _create_account(client, "删除边界", MAX_SAFE_MINOR)
    expense = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=_expense(1, account_id=delete_account, payee="待删支出"),
    ).json()
    income = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(1, account_id=delete_account, payee="后续收入"), "type": "income"},
    )
    assert income.status_code == 201, income.text
    blocked_delete = client.request(
        "DELETE",
        f"/api/transactions/{expense['id']}",
        headers=as_member("m-son"),
        json={"reason": "会让余额溢出"},
    )
    assert blocked_delete.status_code == 422

    restore_account = _create_account(client, "恢复边界")
    recycled = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=_expense(MAX_SAFE_MINOR, account_id=restore_account, payee="待恢复支出"),
    ).json()
    deleted = client.request(
        "DELETE",
        f"/api/transactions/{recycled['id']}",
        headers=as_member("m-son"),
        json={"reason": "构造恢复边界"},
    )
    assert deleted.status_code == 204
    followup = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json=_expense(1, account_id=restore_account, payee="删除后的支出"),
    )
    assert followup.status_code == 201, followup.text
    recycle_bin = client.get("/api/transactions/recycle-bin", headers=as_member("m-son")).json()
    recycled_version = next(item["version"] for item in recycle_bin if item["id"] == recycled["id"])
    blocked_restore = client.post(
        f"/api/transactions/{recycled['id']}/restore",
        headers=as_member("m-son"),
        json={"expectedVersion": recycled_version},
    )
    assert blocked_restore.status_code == 422


def test_import_undo_rejects_projected_balance_overflow(client):
    account_id = _create_account(client, "撤销边界", MAX_SAFE_MINOR)
    content = ("交易时间,交易对方,收支类型,金额\n2026-08-05 12:30:00,导入支出,支出,0.01\n").encode()
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": account_id},
        files=[("files", ("undo-overflow.csv", content, "text/csv"))],
    )
    candidate_id = uploaded.json()["candidates"][0]["id"]
    confirmed = client.post(
        f"/api/import-batches/{uploaded.json()['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [candidate_id]},
    )
    assert confirmed.status_code == 200, confirmed.text
    income = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={**_expense(1, account_id=account_id, payee="导入后收入"), "type": "income"},
    )
    assert income.status_code == 201, income.text

    undone = client.post(
        f"/api/import-batches/{uploaded.json()['id']}/undo",
        headers=as_member("m-son"),
    )
    assert undone.status_code == 422
    batch = client.get(f"/api/import-batches/{uploaded.json()['id']}", headers=as_member("m-son"))
    assert batch.json()["status"] == "posted"


def test_xlsx_keeps_adjacent_boundary_cents_as_distinct_exact_text(client):
    account_ids = []
    for suffix in ("90", "91"):
        account_ids.append(_create_account(client, f"边界账户{suffix}"))

    for index, amount in enumerate((MAX_SAFE_MINOR - 1, MAX_SAFE_MINOR)):
        created = client.post(
            "/api/transactions",
            headers=as_member("m-son"),
            json={
                **_expense(amount, account_id=account_ids[index], payee=f"精确{index}"),
                "date": f"2026-08-05T12:3{index}:00+08:00",
            },
        )
        assert created.status_code == 201, created.text

    report = client.get(
        "/api/reports/export",
        headers=as_member("m-son"),
        params={"tier": 5, "from": "2026-08-01", "to": "2026-08-31"},
    )
    assert report.status_code == 200, report.text
    workbook = load_workbook(BytesIO(report.content), read_only=False, data_only=False)
    amounts = [workbook["阅读账单"][f"E{row}"].value for row in (2, 3)]
    assert amounts == ["90071992547409.90", "90071992547409.91"]
    assert workbook["阅读账单"]["E2"].data_type == "s"
    assert workbook["阅读账单"]["E3"].data_type == "s"
    assert workbook["复式分录"]["E7"].value == "180143985094819.81"
    assert workbook["复式分录"]["E7"].data_type == "s"
