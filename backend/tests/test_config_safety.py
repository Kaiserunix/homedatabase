from __future__ import annotations

import pytest
from pydantic import ValidationError

from homedatabase_api.config import Settings


def test_production_rejects_development_and_unencrypted_defaults():
    with pytest.raises(ValidationError) as captured:
        Settings(
            environment="production",
            source_encryption_key=None,
            backup_encryption_key=None,
        )
    message = str(captured.value.errors()[0]["ctx"]["error"])
    assert "production requires PostgreSQL" in message
    assert "Secure session cookies" in message
    assert "SOURCE_ENCRYPTION_KEY" in message
    assert "BACKUP_ENCRYPTION_KEY" in message


def test_production_accepts_explicit_private_deployment_settings():
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://ledger:secret@postgres/homedatabase",
        dev_member_id=None,
        session_cookie_secure=True,
        auto_create_schema=False,
        webauthn_rp_id="finance.example.test",
        webauthn_origins=["https://finance.example.test"],
        allowed_hosts=["finance.example.test"],
        source_encryption_key="A" * 43,
        backup_encryption_key="B" * 43,
    )
    assert settings.environment == "production"


def test_v1_rejects_changing_the_household_base_currency_at_startup():
    with pytest.raises(ValidationError):
        Settings(base_currency="USD")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("seed_demo", True, "forbids demo seeding"),
        ("bootstrap_password", "one-password-for-everyone", "BOOTSTRAP_PASSWORD"),
    ],
)
def test_production_rejects_demo_family_password_paths(field, value, message):
    values = {
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
        field: value,
    }
    with pytest.raises(ValidationError) as captured:
        Settings(**values)
    assert message in str(captured.value.errors()[0]["ctx"]["error"])
