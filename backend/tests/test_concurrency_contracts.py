from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from homedatabase_api.schemas import NewSplitInput
from homedatabase_api.services import imports as import_service
from homedatabase_api.services import ledger as ledger_service
from homedatabase_api.services import locking as locking_service
from homedatabase_api.services import splits as split_service


def test_refund_category_sync_locks_rows_in_stable_order():
    captured = {}

    class EmptyResult:
        def all(self):
            return []

    class CaptureSession:
        def scalars(self, statement):
            captured["statement"] = statement
            return EmptyResult()

    ledger_service._active_refunds_for_category_sync(CaptureSession(), "txn-original")
    sql = str(captured["statement"].compile(dialect=postgresql.dialect()))
    assert "ORDER BY transactions.id" in sql
    assert "FOR UPDATE" in sql


def test_split_invalidation_locks_the_split_parent_row():
    captured = {}

    class CaptureSession:
        def scalar(self, statement):
            captured["statement"] = statement
            return None

    ledger_service._invalidate_transaction_split(
        CaptureSession(), SimpleNamespace(id="txn-with-split")
    )
    sql = str(captured["statement"].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


def test_share_update_helper_locks_transaction_before_split(monkeypatch):
    events = []
    transaction = SimpleNamespace(id="txn-1")
    split = SimpleNamespace(id="split-1", transaction_id="txn-1")

    class CaptureSession:
        def scalar(self, _statement):
            events.append("read-transaction-id")
            return "txn-1"

    def fake_get_transaction(_db, _member, transaction_id, *, for_update=False):
        assert transaction_id == "txn-1"
        assert for_update is True
        events.append("lock-transaction")
        return transaction

    def fake_load_split(_db, split_id, *, for_update=False):
        assert split_id == "split-1"
        assert for_update is True
        events.append("lock-split")
        return split

    monkeypatch.setattr(split_service, "get_transaction", fake_get_transaction)
    monkeypatch.setattr(split_service, "_load_split", fake_load_split)
    loaded_split, loaded_transaction = split_service._load_split_after_transaction_lock(
        CaptureSession(), SimpleNamespace(id="m-son"), "split-1"
    )
    assert loaded_split is split
    assert loaded_transaction is transaction
    assert events == ["read-transaction-id", "lock-transaction", "lock-split"]


def test_split_creation_requests_a_transaction_row_lock(monkeypatch):
    observed = {}

    def fake_get_transaction(_db, _member, transaction_id, *, for_update=False):
        observed["transaction_id"] = transaction_id
        observed["for_update"] = for_update
        raise HTTPException(status_code=404, detail="stop after lock contract")

    monkeypatch.setattr(split_service, "get_transaction", fake_get_transaction)
    payload = NewSplitInput.model_validate(
        {
            "txnId": "txn-create",
            "totalCents": 100,
            "shares": [{"memberId": "m-son", "shareCents": 100}],
        }
    )
    with pytest.raises(HTTPException):
        split_service.create_split(object(), SimpleNamespace(id="m-son"), payload)
    assert observed == {"transaction_id": "txn-create", "for_update": True}


def test_postgres_ledger_guard_uses_a_transaction_advisory_lock():
    captured = {}

    class CaptureSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement, params):
            captured["statement"] = str(statement)
            captured["params"] = params

    locking_service.acquire_ledger_mutation_guard(CaptureSession())
    assert captured == {
        "statement": "SELECT pg_advisory_xact_lock(:guard_key)",
        "params": {"guard_key": 0x48444C4544474552},
    }


def test_sqlite_ledger_guard_is_an_explicit_noop():
    class CaptureSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def execute(self, _statement, _params):
            raise AssertionError("SQLite must not execute PostgreSQL advisory-lock SQL")

    locking_service.acquire_ledger_mutation_guard(CaptureSession())


def test_confirmation_acquires_ledger_guard_before_the_batch_row_lock(monkeypatch):
    events = []

    def fake_guard(_db):
        events.append("ledger-guard")

    def fake_get_batch(_db, _member, _batch_id, *, lock_for_update=False):
        assert lock_for_update is True
        events.append("batch-row")
        raise HTTPException(status_code=404, detail="stop after lock-order probe")

    monkeypatch.setattr(import_service, "acquire_ledger_mutation_guard", fake_guard)
    monkeypatch.setattr(import_service, "get_batch", fake_get_batch)
    with pytest.raises(HTTPException):
        import_service.confirm_candidates(object(), SimpleNamespace(id="m-son"), "batch-1", [])
    assert events == ["ledger-guard", "batch-row"]


def test_ledger_update_acquires_guard_before_the_transaction_row_lock(monkeypatch):
    events = []

    def fake_guard(_db):
        events.append("ledger-guard")

    def fake_get_transaction(_db, _member, _transaction_id, *, for_update=False):
        assert for_update is True
        events.append("transaction-row")
        raise HTTPException(status_code=404, detail="stop after lock-order probe")

    monkeypatch.setattr(ledger_service, "acquire_ledger_mutation_guard", fake_guard)
    monkeypatch.setattr(ledger_service, "get_transaction", fake_get_transaction)
    with pytest.raises(HTTPException):
        ledger_service.update_transaction(
            object(), SimpleNamespace(id="m-son"), "txn-1", SimpleNamespace()
        )
    assert events == ["ledger-guard", "transaction-row"]
