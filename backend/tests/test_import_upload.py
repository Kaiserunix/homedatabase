from __future__ import annotations

from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from conftest import as_member
from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from homedatabase_api.config import get_settings
from homedatabase_api.database import get_session_factory
from homedatabase_api.main import app
from homedatabase_api.models import ImportBatch, SourceFile, SourceRecord
from homedatabase_api.services import imports as import_service
from homedatabase_api.services.source_storage import decrypt_source_file


def _xlsx_bill() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "银行卡"
    sheet.append(["中国银行导出账单"])
    sheet.append(["交易日期", "交易对方", "支出金额", "收入金额", "币种", "分类"])
    sheet.append(["2026-08-04 09:15:00", "地铁公司", 3.5, None, "CNY", "交通"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _csv_bill() -> bytes:
    return (
        "交易时间,交易对方,收支类型,金额,交易分类\n"
        "2026-08-05 12:30:00,向XX超市付款,支出,12.34,买菜超市\n"
        "2026-08-05 18:00:00,工资发放,收入,5000.00,工资\n"
    ).encode("utf-8-sig")


def _single_expense_csv(*, occurred_at: str = "2026-08-05 12:30:00") -> bytes:
    return (
        "交易时间,交易对方,收支类型,金额,交易分类\n"
        f"{occurred_at},批次撤销测试支出,支出,12.34,买菜超市\n"
    ).encode("utf-8-sig")


def _upload_and_confirm_single_expense(client, *, occurred_at: str = "2026-08-05 12:30:00"):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[
            (
                "files",
                ("undo-guard.csv", _single_expense_csv(occurred_at=occurred_at), "text/csv"),
            )
        ],
    )
    assert uploaded.status_code == 201, uploaded.text
    batch = uploaded.json()
    candidate = batch["candidates"][0]
    confirmed = client.post(
        f"/api/import-batches/{batch['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [candidate["id"]]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "posted"
    transaction = next(
        item
        for item in client.get("/api/transactions", headers=as_member("m-son")).json()
        if item["batchId"] == batch["id"]
    )
    return batch, transaction


def test_multi_file_upload_persists_encrypted_sources_candidates_and_polling(client):
    response = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"source": "银行和微信", "targetAccountId": "a-son-wallet"},
        files=[
            ("files", ("微信账单.csv", _csv_bill(), "text/csv")),
            (
                "files",
                (
                    "银行卡.xlsx",
                    _xlsx_bill(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        ],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "reviewing"
    assert body["progress"] == 100
    assert body["fileCount"] == 2
    assert body["filesProcessed"] == 2
    assert body["candidateCount"] == 3
    assert {item["parseStatus"] for item in body["files"]} == {"parsed"}
    assert {item["payee"] for item in body["candidates"]} == {
        "向XX超市付款",
        "工资发放",
        "地铁公司",
    }
    assert (
        next(item for item in body["candidates"] if item["payee"] == "向XX超市付款")[
            "suggestedCategoryId"
        ]
        == "c-grocery"
    )

    status = client.get(f"/api/imports/{body['id']}/status", headers=as_member("m-son"))
    assert status.status_code == 200
    assert status.json()["candidateCount"] == 3
    assert status.json()["status"] == "reviewing"

    with get_session_factory()() as db:
        batch = db.scalar(select(ImportBatch).where(ImportBatch.id == body["id"]))
        assert batch is not None
        source_file = db.scalar(
            select(SourceFile).where(
                SourceFile.batch_id == body["id"], SourceFile.filename == "微信账单.csv"
            )
        )
        assert source_file is not None
        encrypted_path = get_settings().source_archive_dir / source_file.encrypted_path
        encrypted = encrypted_path.read_bytes()
        assert b"XX" not in encrypted
        decrypted = decrypt_source_file(
            encrypted_path,
            source_file_id=source_file.id,
            settings=get_settings(),
        )
        assert decrypted == _csv_bill()


def test_exact_file_hash_is_blocked_without_duplicate_candidates(client):
    upload = [("files", ("first.csv", _csv_bill(), "text/csv"))]
    first = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=upload,
    )
    assert first.status_code == 201

    second = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("renamed.csv", _csv_bill(), "text/csv"))],
    )
    assert second.status_code == 201
    body = second.json()
    assert body["status"] == "rejected"
    assert body["candidateCount"] == 0
    assert body["files"][0]["parseStatus"] == "duplicate"
    assert body["files"][0]["duplicateOfFileId"] == first.json()["files"][0]["id"]


def test_pdf_is_explicitly_held_for_ocr_and_filename_is_sanitised(client):
    response = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"source": "银行 PDF", "targetAccountId": "a-son-wallet"},
        files=[("files", ("../../statement.pdf", b"%PDF-1.7\nminimal", "application/pdf"))],
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_ocr"
    assert body["needsOcrCount"] == 1
    assert body["files"][0]["filename"] == "statement.pdf"
    assert body["files"][0]["parseStatus"] == "needs_ocr"
    assert "OCR" in body["files"][0]["parseMessage"]


def test_mixed_batch_stays_needs_ocr_after_parsed_candidates_are_confirmed(client):
    response = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"source": "混合账单", "targetAccountId": "a-son-wallet"},
        files=[
            (
                "files",
                (
                    "parsed.csv",
                    _single_expense_csv(occurred_at="2026-08-24 12:30:00"),
                    "text/csv",
                ),
            ),
            ("files", ("pending.pdf", b"%PDF-1.7\nminimal", "application/pdf")),
        ],
    )
    assert response.status_code == 201, response.text
    batch = response.json()
    assert batch["status"] == "reviewing"
    assert batch["needsOcrCount"] == 1

    confirmed = client.post(
        f"/api/import-batches/{batch['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [batch["candidates"][0]["id"]]},
    )
    assert confirmed.status_code == 200, confirmed.text
    partially_processed = confirmed.json()
    assert partially_processed["status"] == "needs_ocr"
    assert partially_processed["needsOcrCount"] == 1
    assert "仍有 1 个文件等待 OCR" in partially_processed["message"]
    assert "全部处理并入账" not in partially_processed["message"]

    undone = client.post(
        f"/api/import-batches/{batch['id']}/undo",
        headers=as_member("m-son"),
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "undone"
    assert not any(
        transaction["batchId"] == batch["id"]
        for transaction in client.get("/api/transactions", headers=as_member("m-son")).json()
    )
    assert any(
        transaction["batchId"] == batch["id"]
        for transaction in client.get(
            "/api/transactions/recycle-bin", headers=as_member("m-son")
        ).json()
    )


def test_confirm_links_source_evidence_to_created_transaction(client):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("bill.csv", _csv_bill(), "text/csv"))],
    ).json()
    candidate = uploaded["candidates"][0]
    confirmed = client.post(
        f"/api/import-batches/{uploaded['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [candidate["id"]]},
    )
    assert confirmed.status_code == 200, confirmed.text
    with get_session_factory()() as db:
        record = db.scalar(
            select(SourceRecord).where(
                SourceRecord.file_id == uploaded["files"][0]["id"],
                SourceRecord.linked_transaction_id.is_not(None),
            )
        )
        assert record is not None
        assert record.linked_transaction_id is not None


def test_import_confirmation_preserves_the_timezone_aware_candidate_instant(client):
    batch, transaction = _upload_and_confirm_single_expense(
        client, occurred_at="2026-08-01 12:30:00"
    )
    candidate_date = datetime.fromisoformat(batch["candidates"][0]["date"])
    transaction_date = datetime.fromisoformat(transaction["date"])

    assert candidate_date.utcoffset() is not None
    assert transaction_date.utcoffset() is not None
    assert transaction_date == candidate_date
    assert (
        transaction_date.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
        == "2026-08-01 12:30"
    )


def test_naive_candidate_patch_is_interpreted_as_shanghai_time(client):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("patch-date.csv", _single_expense_csv(), "text/csv"))],
    ).json()
    candidate = uploaded["candidates"][0]
    patched = client.patch(
        f"/api/import-batches/{uploaded['id']}/candidates/{candidate['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": candidate["version"], "date": "2026-08-02T12:30:00"},
    )
    assert patched.status_code == 200, patched.text
    refreshed = client.get(
        f"/api/import-batches/{uploaded['id']}", headers=as_member("m-son")
    ).json()
    patched_date = datetime.fromisoformat(refreshed["candidates"][0]["date"])
    assert patched_date.utcoffset() is not None
    assert (
        patched_date.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
        == "2026-08-02 12:30"
    )


def test_import_detects_an_existing_manual_transaction_at_the_same_local_instant(client):
    manual = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "expense",
            "payee": "批次撤销测试支出",
            "accountId": "a-son-wallet",
            "amountCents": 1234,
            "currency": "CNY",
            "date": "2026-08-05T12:30:00+08:00",
        },
    )
    assert manual.status_code == 201, manual.text

    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("existing.csv", _single_expense_csv(), "text/csv"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    candidate = uploaded.json()["candidates"][0]
    assert candidate["validationStatus"] == "exact_duplicate"
    assert candidate["duplicateOfTxnId"] == manual.json()["id"]


def test_import_exact_duplicate_detection_is_not_limited_to_twenty_similar_transactions(client):
    last_transaction_id = None
    for day in range(1, 22):
        created = client.post(
            "/api/transactions",
            headers=as_member("m-son"),
            json={
                "type": "expense",
                "payee": "批次撤销测试支出",
                "accountId": "a-son-wallet",
                "amountCents": 1234,
                "currency": "CNY",
                "date": f"2026-08-{day:02d}T12:30:00+08:00",
            },
        )
        assert created.status_code == 201, created.text
        last_transaction_id = created.json()["id"]

    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[
            (
                "files",
                (
                    "twenty-first-existing.csv",
                    _single_expense_csv(occurred_at="2026-08-21 12:30:00"),
                    "text/csv",
                ),
            )
        ],
    )
    assert uploaded.status_code == 201, uploaded.text
    candidate = uploaded.json()["candidates"][0]
    assert candidate["validationStatus"] == "exact_duplicate"
    assert candidate["duplicateOfTxnId"] == last_transaction_id


def test_confirmation_rechecks_for_a_duplicate_created_after_upload(client):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[
            (
                "files",
                (
                    "became-duplicate.csv",
                    _single_expense_csv(occurred_at="2026-08-23 12:30:00"),
                    "text/csv",
                ),
            )
        ],
    )
    assert uploaded.status_code == 201, uploaded.text
    batch = uploaded.json()
    candidate = batch["candidates"][0]
    assert candidate["validationStatus"] == "valid"

    manual = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "expense",
            "payee": "批次撤销测试支出",
            "accountId": "a-son-wallet",
            "amountCents": 1234,
            "currency": "CNY",
            "date": "2026-08-23T12:30:00+08:00",
        },
    )
    assert manual.status_code == 201, manual.text

    confirmed = client.post(
        f"/api/import-batches/{batch['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [candidate["id"]]},
    )
    assert confirmed.status_code == 409, confirmed.text
    assert confirmed.json()["detail"] == {
        "message": "确认前发现重复账目，请重新复核",
        "candidateIds": [candidate["id"]],
    }

    refreshed = client.get(f"/api/import-batches/{batch['id']}", headers=as_member("m-son")).json()
    refreshed_candidate = refreshed["candidates"][0]
    assert refreshed["status"] == "reviewing"
    assert refreshed_candidate["status"] == "pending"
    assert refreshed_candidate["validationStatus"] == "exact_duplicate"
    assert refreshed_candidate["duplicateOfTxnId"] == manual.json()["id"]
    matching = [
        transaction
        for transaction in client.get("/api/transactions", headers=as_member("m-son")).json()
        if transaction["payee"] == "批次撤销测试支出"
        and transaction["date"] == manual.json()["date"]
    ]
    assert [transaction["id"] for transaction in matching] == [manual.json()["id"]]


def test_import_undo_is_blocked_when_a_posted_transaction_has_an_active_refund(client):
    batch, expense = _upload_and_confirm_single_expense(client)
    refund = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "refund",
            "payee": "批次撤销测试退款",
            "accountId": "a-son-wallet",
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-06T09:00:00+08:00",
            "refundOf": expense["id"],
        },
    )
    assert refund.status_code == 201, refund.text

    undo = client.post(
        f"/api/import-batches/{batch['id']}/undo",
        headers=as_member("m-son"),
    )
    assert undo.status_code == 409
    assert expense["id"] in undo.json()["detail"]["transactionIds"]

    unchanged_batch = client.get(f"/api/import-batches/{batch['id']}", headers=as_member("m-son"))
    assert unchanged_batch.json()["status"] == "posted"
    active = client.get("/api/transactions", headers=as_member("m-son")).json()
    assert {expense["id"], refund.json()["id"]} <= {item["id"] for item in active}
    assert client.get("/api/transactions/recycle-bin", headers=as_member("m-son")).json() == []


def test_import_undo_respects_period_locks_without_changing_the_batch(client):
    batch, expense = _upload_and_confirm_single_expense(client, occurred_at="2026-08-01 00:30:00")
    period = client.post(
        "/api/period-locks",
        headers=as_member("m-son"),
        json={"startsOn": "2026-08-01", "endsOn": "2026-08-01", "reason": "日结"},
    )
    assert period.status_code == 201, period.text

    undo = client.post(
        f"/api/import-batches/{batch['id']}/undo",
        headers=as_member("m-son"),
    )
    assert undo.status_code == 409
    assert expense["id"] in undo.json()["detail"]["transactionIds"]

    unchanged_batch = client.get(f"/api/import-batches/{batch['id']}", headers=as_member("m-son"))
    assert unchanged_batch.json()["status"] == "posted"
    active_ids = {
        item["id"] for item in client.get("/api/transactions", headers=as_member("m-son")).json()
    }
    assert expense["id"] in active_ids
    assert client.get("/api/transactions/recycle-bin", headers=as_member("m-son")).json() == []


def test_upload_requires_manage_permission_for_target_account(client):
    response = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-dad-bank"},
        files=[("files", ("private.csv", _csv_bill(), "text/csv"))],
    )
    assert response.status_code == 403


def _raw_multipart(payload: bytes, *, boundary: str = "limit-boundary") -> bytes:
    return (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="large.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        + payload
        + f"\r\n--{boundary}--\r\n".encode()
    )


def test_upload_authentication_precedes_multipart_parsing_and_limits_actual_stream(client):
    malformed = client.post(
        "/api/imports",
        content=b"not-a-multipart-body",
        headers={
            "Content-Type": "multipart/form-data; boundary=missing",
            "Content-Length": str(10**9),
        },
    )
    assert malformed.status_code == 401

    limited = get_settings().model_copy(update={"source_max_total_bytes": 256})
    app.dependency_overrides[get_settings] = lambda: limited
    try:
        body = _raw_multipart(b"x" * 512)
        actual_overflow = client.post(
            "/api/imports",
            content=body,
            headers={
                **as_member("m-son"),
                "Content-Type": "multipart/form-data; boundary=limit-boundary",
                # A dishonest or chunked client cannot bypass the receive wrapper.
                "Content-Length": "10",
            },
        )
        assert actual_overflow.status_code == 413
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_import_openapi_keeps_explicit_multi_file_multipart_contract():
    operation = app.openapi()["paths"]["/api/imports"]["post"]
    assert operation["security"] == [
        {"SessionCookie": []},
        {"BearerToken": []},
    ]
    multipart = operation["requestBody"]["content"]["multipart/form-data"]
    schema_ref = multipart["schema"]["$ref"].rsplit("/", 1)[-1]
    schema = app.openapi()["components"]["schemas"][schema_ref]
    assert schema["properties"]["files"]["type"] == "array"
    assert schema["properties"]["files"]["items"]["contentMediaType"] == "application/octet-stream"


def test_foreign_currency_candidate_requires_review_then_confirms_with_fx_rate(client):
    foreign_csv = (
        "交易时间,交易对方,收支类型,金额,币种\n2026-08-05 12:30:00,海外商店,支出,10.00,USD\n"
    ).encode()
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", ("foreign.csv", foreign_csv, "text/csv"))],
    )
    assert uploaded.status_code == 201
    candidate = uploaded.json()["candidates"][0]
    assert candidate["validationStatus"] == "needs_fx_review"
    assert candidate["fxRate"] is None
    assert candidate["baseAmountCents"] is None

    blocked = client.post(
        f"/api/import-batches/{uploaded.json()['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [candidate["id"]]},
    )
    assert blocked.status_code == 422

    patched = client.patch(
        f"/api/import-batches/{uploaded.json()['id']}/candidates/{candidate['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": candidate["version"], "fxRate": "7.2"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["validationStatus"] == "valid"

    confirmed = client.post(
        f"/api/import-batches/{uploaded.json()['id']}/confirm",
        headers=as_member("m-son"),
        json={"candidateIds": [candidate["id"]]},
    )
    assert confirmed.status_code == 200, confirmed.text
    transactions = client.get("/api/transactions", headers=as_member("m-son")).json()
    imported = next(item for item in transactions if item["payee"] == "海外商店")
    assert imported["baseAmountCents"] == 7_200


def test_mutating_import_batch_query_takes_a_postgres_row_lock():
    captured = {}

    class CaptureSession:
        def scalar(self, statement):
            captured["statement"] = statement
            return None

    with pytest.raises(HTTPException) as error:
        import_service.get_batch(
            CaptureSession(),
            SimpleNamespace(id="m-son"),
            "batch-race",
            lock_for_update=True,
        )
    assert error.value.status_code == 404
    sql = str(captured["statement"].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


def test_batch_confirmation_prelocks_every_account_in_global_id_order():
    captured = {}

    class CaptureResult:
        def all(self):
            return [SimpleNamespace(id="a-account"), SimpleNamespace(id="z-account")]

    class CaptureSession:
        def scalars(self, statement):
            captured["statement"] = statement
            return CaptureResult()

    import_service._lock_confirmation_accounts(CaptureSession(), {"z-account", "a-account"})
    compiled = captured["statement"].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ORDER BY accounts.id" in sql
    assert "FOR UPDATE" in sql
    assert compiled.params["id_1"] == ["a-account", "z-account"]
