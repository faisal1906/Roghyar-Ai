import pytest
from pydantic import ValidationError

from roghyar.database import database_url
from roghyar.settings import Settings


def test_local_settings():
    settings = Settings(_env_file=None, environment="local", db_port=55432)
    assert database_url(settings).port == 55432


def test_invalid_port():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, db_port=65536)


def test_production_requires_dedicated_password():
    with pytest.raises(ValidationError, match="dedicated database password"):
        Settings(_env_file=None, environment="production", db_password="local-development-only")


def test_production_checks_tls_and_masks_password():
    settings = Settings(_env_file=None, environment="production", db_password="test-custom-secret")
    url = database_url(settings)
    assert url.query["sslmode"] == "verify-full"
    assert "test-custom-secret" not in str(url)
    assert "test-custom-secret" not in repr(settings)


def test_production_rejects_synthetic_fixtures():
    with pytest.raises(ValidationError, match="forbidden in production"):
        Settings(_env_file=None, environment="production", synthetic_fixtures=True)


@pytest.mark.parametrize(
    "flag", ["ROGHYAR_CLINICAL_GUIDANCE_ENABLED", "ROGHYAR_APPOINTMENT_BOOKING_ENABLED"]
)
def test_patient_feature_flags_cannot_bypass_release_gates(monkeypatch, flag):
    monkeypatch.setenv(flag, "true")
    with pytest.raises(ValidationError, match="require implementation and approval"):
        Settings(_env_file=None)
