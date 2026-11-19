import base64
import logging
import time
from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr

from roghyar.field_encryption import FieldEncryption, PrivacyConfigurationError
from roghyar.scoped_session import (
    AUDIENCE, ISSUER, AuthorizationDenied, CredentialVerifier, scoped_session,
)

KEY = "SYNTHETIC-DEVELOPMENT-ONLY-signing-key"


def test_session_verification_fails_closed(caplog):
    caplog.set_level(logging.DEBUG)
    now = int(time.time())
    subject = uuid4()
    claims = dict(sub=str(subject), kind="patient", exp=now + 60, iat=now, aud=AUDIENCE, iss=ISSUER)
    verifier = CredentialVerifier(SecretStr(KEY))
    assert verifier.verify(None).subject is None
    good = jwt.encode(claims, KEY, algorithm="HS256")
    assert verifier.verify(good).subject == subject
    rejected = [
        jwt.encode(claims, "different-SYNTHETIC-development-key", algorithm="HS256"),
        jwt.encode({**claims, "iat": now - 120, "exp": now - 60}, KEY, algorithm="HS256"),
        jwt.encode({**claims, "iat": now + 60}, KEY, algorithm="HS256"),
        jwt.encode({**claims, "exp": now + 901}, KEY, algorithm="HS256"),
        jwt.encode({**claims, "kind": "maintenance"}, KEY, algorithm="HS256"),
        jwt.encode({**claims, "aud": "unexpected"}, KEY, algorithm="HS256"),
        jwt.encode({**claims, "sub": "untrusted-id"}, KEY, algorithm="HS256"),
        jwt.encode(claims, KEY, algorithm="HS384"),
        "invalid-SYNTHETIC-token",
    ]
    for token in rejected:
        with pytest.raises(AuthorizationDenied, match="Session authorization denied"):
            verifier.verify(token)
        assert token not in caplog.text
    with pytest.raises(AuthorizationDenied):
        CredentialVerifier(None).verify(good)
    with pytest.raises(AuthorizationDenied):
        with scoped_session(None, verifier, None, required="patient"):
            pytest.fail("Missing credentials reached database logic")
    assert good not in caplog.text


def test_encryption_and_phone_lookup_fail_closed(caplog):
    caplog.set_level(logging.DEBUG)
    first, second = (SecretStr(base64.b64encode(bytes([index]) * 32).decode()) for index in (1, 2))
    for pair in ((None, second), (first, None), (SecretStr("invalid"), second), (first, first)):
        with pytest.raises(PrivacyConfigurationError):
            FieldEncryption.from_secrets(*pair)
    encryption = FieldEncryption.from_secrets(first, second)
    record = uuid4()
    marker = "SYNTHETIC—DEVELOPMENT ONLY"
    for name, value in (("full_name", marker), ("phone", "+12025550101"), ("text", marker)):
        protected = encryption.encrypt(value, record_id=record, field_name=name)
        assert value.encode() not in protected
        assert protected != encryption.encrypt(value, record_id=record, field_name=name)
        assert encryption.decrypt(protected, record_id=record, field_name=name) == value
        for changed, identifier, field in (
            (protected, uuid4(), name), (protected, record, "wrong"),
            (protected[:-1] + bytes([protected[-1] ^ 1]), record, name),
        ):
            with pytest.raises(ValueError, match="Invalid encrypted field"):
                encryption.decrypt(changed, record_id=identifier, field_name=field)
        assert value not in caplog.text
    lookup = encryption.phone_lookup("+12025550101")
    assert len(lookup) == 32
    assert lookup == encryption.phone_lookup("+12025550101")
    assert lookup != encryption.phone_lookup("+12025550102")
    with pytest.raises(ValueError, match="canonical"):
        encryption.phone_lookup("202 555 0101")

