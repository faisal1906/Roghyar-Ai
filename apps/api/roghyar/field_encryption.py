"""Application-side authenticated encryption and keyed phone lookup."""

import base64
import binascii
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

FORMAT = b"\x01"
NONCE_BYTES = 12
PHONE = re.compile(r"\+[1-9][0-9]{7,14}\Z")


class PrivacyConfigurationError(ValueError):
    pass


def _key(secret: SecretStr | None) -> bytes:
    try:
        key = base64.b64decode(secret.get_secret_value(), validate=True) if secret is not None else b""
    except (ValueError, binascii.Error):
        key = b""
    if len(key) != 32:
        raise PrivacyConfigurationError("A valid privacy key is required")
    return key


@dataclass(frozen=True, slots=True)
class FieldEncryption:
    _encryption_key: bytes = field(repr=False)
    _lookup_key: bytes = field(repr=False)

    @classmethod
    def from_secrets(cls, encryption: SecretStr | None, lookup: SecretStr | None) -> "FieldEncryption":
        encryption_key, lookup_key = _key(encryption), _key(lookup)
        if hmac.compare_digest(encryption_key, lookup_key):
            raise PrivacyConfigurationError("Privacy keys must be distinct")
        return cls(encryption_key, lookup_key)

    def encrypt(self, value: str, *, record_id: UUID, field_name: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        purpose = FORMAT + record_id.bytes + field_name.encode()
        return FORMAT + nonce + AESGCM(self._encryption_key).encrypt(nonce, value.encode(), purpose)

    def decrypt(self, value: bytes, *, record_id: UUID, field_name: str) -> str:
        if not value.startswith(FORMAT) or len(value) < 1 + NONCE_BYTES + 16:
            raise ValueError("Invalid encrypted field")
        purpose = FORMAT + record_id.bytes + field_name.encode()
        try:
            return AESGCM(self._encryption_key).decrypt(
                value[1:1 + NONCE_BYTES], value[1 + NONCE_BYTES:], purpose,
            ).decode()
        except (InvalidTag, UnicodeError):
            raise ValueError("Invalid encrypted field") from None

    def phone_lookup(self, canonical_phone: str) -> bytes:
        if PHONE.fullmatch(canonical_phone) is None:
            raise ValueError("Phone lookup requires a canonical E.164 number")
        return hmac.digest(self._lookup_key, b"roghyar-phone-v1\0" + canonical_phone.encode(), hashlib.sha256)

