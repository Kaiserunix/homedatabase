from __future__ import annotations

from conftest import _recreate_schema
from sqlalchemy import select

from homedatabase_api.database import get_engine, get_session_factory
from homedatabase_api.models import Member
from homedatabase_api.security import verify_password
from homedatabase_api.seed import seed_family_data


def test_family_bootstrap_uses_three_distinct_password_hashes(reset_database):
    _recreate_schema(get_engine())
    with get_session_factory()() as db:
        created = seed_family_data(
            db,
            passwords={
                "dad": "dad-password-strong",
                "mom": "mom-password-strong",
                "son": "son-password-strong",
            },
        )
        assert created is True
        members = db.scalars(select(Member).order_by(Member.identity)).all()
        assert len({item.password_hash for item in members}) == 3
        by_identity = {item.identity: item for item in members}
        assert verify_password("dad-password-strong", by_identity["dad"].password_hash)
        assert verify_password("mom-password-strong", by_identity["mom"].password_hash)
        assert verify_password("son-password-strong", by_identity["son"].password_hash)
