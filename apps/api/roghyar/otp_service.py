"""Private BK-01 OTP workflow and purpose-scoped revocable credentials."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
import secrets
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

import jwt
from pydantic import Field, SecretStr, StringConstraints, field_validator
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .field_encryption import FieldEncryption
from .hospital_search import SearchModel
from .http_safety import APIError
from .otp_provider import SMSUnavailable
from .rate_limit import Counter, Policy, RateLimiter
from .scoped_session import AuthorizationDenied, _scoped_transaction, Principal

Purpose = Literal["booking", "appointments"]
AUDIENCE = "roghyar-verified-phone"
ISSUER = "roghyar-otp"
KEY_ID = "otp-v1"
OTP_TTL = timedelta(minutes=5)
TOKEN_TTL = timedelta(minutes=10)


def normalize_phone(value: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"03[0-9]{9}", value):
        return "+92" + value[1:]
    if isinstance(value, str) and re.fullmatch(r"\+923[0-9]{9}", value):
        return value
    raise ValueError("Invalid Pakistani mobile number")


class OTPRequest(SearchModel):
    phone: Annotated[str, Field(max_length=13)] = Field(repr=False)
    purpose: Purpose

    @field_validator("phone")
    @classmethod
    def canonical_phone(cls, value):
        return normalize_phone(value)


class OTPVerify(OTPRequest):
    code: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")] = Field(repr=False)


class SMSProvider(Protocol):
    def send(self, phone: str, code: str, challenge: UUID) -> bool: ...


class OTPDenied(ValueError):
    """Stable machine reason. Contains no submitted values."""


class VerifiedSession(SearchModel):
    subject: UUID
    session: UUID
    purpose: Purpose


REQUEST_PEER = Policy("otp_request_peer", 8, 600)
REQUEST_PHONE = Policy("otp_request_phone", 4, 900)
REQUEST_PURPOSE = Policy("otp_request_purpose", 3, 900)
RESEND = Policy("otp_resend", 1, 45)
SEND_BUDGET = Policy("otp_provider_budget", 100, 3600)
VERIFY_PEER = Policy("otp_verify_peer", 30, 600)
VERIFY_PHONE = Policy("otp_verify_phone", 10, 600)
VERIFY_PURPOSE = Policy("otp_verify_purpose", 8, 600)


@dataclass(frozen=True)
class OTPQuotas:
    request_peer: Policy = REQUEST_PEER
    request_phone: Policy = REQUEST_PHONE
    request_purpose: Policy = REQUEST_PURPOSE
    resend: Policy = RESEND
    send_budget: Policy = SEND_BUDGET
    verify_peer: Policy = VERIFY_PEER
    verify_phone: Policy = VERIFY_PHONE
    verify_purpose: Policy = VERIFY_PURPOSE


class OTPService:
    def __init__(self, engine: Engine, encryption: FieldEncryption, counter: Counter, *, otp_key: bytes,
                 signing_key: bytes, quota_key: bytes, provider: SMSProvider | None = None,
                 quotas: OTPQuotas = OTPQuotas(), signing_key_id: str = KEY_ID,
                 clock=lambda: datetime.now(UTC)):
        keys = (otp_key, signing_key, quota_key, encryption._lookup_key, encryption._encryption_key)
        if (not engine.hide_parameters or engine.echo or any(len(key) != 32 for key in keys)
                or len(set(keys)) != len(keys) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", signing_key_id)):
            raise OTPDenied("invalid_configuration")
        self.engine, self.encryption = engine, encryption
        self.otp_key, self.signing_key = otp_key, signing_key
        self.limits = RateLimiter(counter, SecretStr(quota_key.hex()))
        self.provider, self.clock = provider, clock
        self.quotas, self.signing_key_id = quotas, signing_key_id

    @contextmanager
    def transaction(self, lookup: bytes | None = None):
        try:
            with _scoped_transaction(self.engine, Principal("anonymous", None), "roghyar_otp_writer",
                                     readonly=False) as session:
                session.execute(text("SET LOCAL statement_timeout='2000ms'"))
                if lookup is not None:
                    session.execute(text("SELECT pg_advisory_xact_lock(:lock)"),
                                    {"lock": int.from_bytes(lookup[:8], "big", signed=True)})
                yield session
        except (AuthorizationDenied, SQLAlchemyError):
            raise OTPDenied("database_unavailable") from None

    def digest(self, challenge, lookup, purpose, code):
        return hmac.digest(self.otp_key, b"roghyar-otp-v1\0" + challenge.bytes + lookup
                           + purpose.encode() + b"\0" + code.encode(), hashlib.sha256)

    def request(self, query: OTPRequest, request):
        query = OTPRequest.model_validate(query.model_dump())
        if self.provider is None:
            raise OTPDenied("provider_unconfigured")
        lookup = self.encryption.phone_lookup(query.phone)
        ident = lookup.hex()
        self.limits.enforce_peer(request, self.quotas.request_peer)
        self.limits.enforce_identity(self.quotas.request_phone, "phone", ident)
        self.limits.enforce_identity(self.quotas.request_purpose, "purpose", ident + query.purpose)
        self.limits.enforce_identity(self.quotas.resend, "phone", ident)
        self.limits.enforce_identity(self.quotas.send_budget, "aggregate", "sms")
        challenge = uuid4()
        with self.transaction(lookup) as session:
            now = self.clock()
            scope = {"lookup": lookup, "purpose": query.purpose}
            old = session.execute(text("""
                SELECT id,verifier FROM roghyar.otp_challenges
                WHERE phone_lookup=:lookup AND purpose=:purpose ORDER BY created_at DESC,id LIMIT 1
            """), scope).mappings().one_or_none()
            while True:
                code = f"{secrets.randbelow(1_000_000):06d}"
                if old is None or not hmac.compare_digest(
                    bytes(old["verifier"]), self.digest(old["id"], lookup, query.purpose, code)
                ):
                    break
            session.execute(text("""
                UPDATE roghyar.otp_challenges SET state='superseded'
                WHERE phone_lookup=:lookup AND purpose=:purpose AND state IN ('pending','active')
            """), scope)
            session.execute(text("""
                INSERT INTO roghyar.otp_challenges(id,phone_lookup,purpose,verifier,state,created_at,expires_at)
                VALUES(:id,:lookup,:purpose,:verifier,'pending',:now,:expires)
            """), scope | {"id": challenge, "verifier": self.digest(challenge, lookup, query.purpose, code),
                          "now": now, "expires": now + OTP_TTL})
        try:
            acknowledged = self.provider.send(query.phone, code, challenge) is True
        except SMSUnavailable:
            acknowledged = False
        with self.transaction(lookup) as session:
            # A late or superseded provider acknowledgement cannot activate a challenge.
            acked = session.execute(text("""
                UPDATE roghyar.otp_challenges
                SET state=CASE WHEN expires_at<=:now THEN 'expired' WHEN :ack THEN 'active' ELSE 'invalidated' END
                WHERE id=:id AND state='pending' RETURNING state
            """), {"now": self.clock(), "ack": acknowledged, "id": challenge}).scalar_one_or_none()
        if acked != "active":
            raise OTPDenied("provider_unavailable")

    def verify(self, query: OTPVerify, request) -> str:
        query = OTPVerify.model_validate(query.model_dump())
        lookup = self.encryption.phone_lookup(query.phone)
        ident = lookup.hex()
        self.limits.enforce_peer(request, self.quotas.verify_peer)
        self.limits.enforce_identity(self.quotas.verify_phone, "phone", ident)
        self.limits.enforce_identity(self.quotas.verify_purpose, "purpose", ident + query.purpose)
        token = None
        with self.transaction(lookup) as session:
            row = session.execute(text("""
                SELECT id,verifier,expires_at,attempts FROM roghyar.otp_challenges
                WHERE phone_lookup=:lookup AND purpose=:purpose AND state='active' FOR UPDATE
            """), {"lookup": lookup, "purpose": query.purpose}).mappings().one_or_none()
            now = self.clock()
            if row is not None:
                id_params = {"id": row["id"]}
                if now >= row["expires_at"]:
                    session.execute(text("UPDATE roghyar.otp_challenges SET state='expired' WHERE id=:id"), id_params)
                elif not hmac.compare_digest(bytes(row["verifier"]), self.digest(
                        row["id"], lookup, query.purpose, query.code)):
                    session.execute(text("""UPDATE roghyar.otp_challenges SET attempts=attempts+1,
                        state=CASE WHEN attempts+1=5 THEN 'locked' ELSE 'active' END WHERE id=:id"""), id_params)
                else:
                    token = self.consume(session, lookup, query.purpose, row["id"], now)
        if token is None:
            raise OTPDenied("invalid_verification")
        return token

    def consume(self, session, lookup, purpose, verification, now):
        session.execute(text("""
            INSERT INTO roghyar.otp_identities(subject,phone_lookup,created_at)
            VALUES(:subject,:lookup,:issued) ON CONFLICT(phone_lookup) DO NOTHING
        """), {"subject": uuid4(), "lookup": lookup, "issued": now})
        subject = session.scalar(text("""
            SELECT subject FROM roghyar.otp_identities WHERE phone_lookup=:lookup AND revoked_at IS NULL
        """), {"lookup": lookup})
        if subject is None:
            return None
        issued = now.replace(microsecond=0)
        key = {"id": uuid4(), "subject": subject, "lookup": lookup, "verification": verification,
               "purpose": purpose, "issued": issued, "expires": issued + TOKEN_TTL, "key": self.signing_key_id}
        session.execute(text("UPDATE roghyar.otp_challenges SET state='consumed' WHERE id=:id"), {"id": verification})
        session.execute(text("""
            INSERT INTO roghyar.otp_sessions(id,subject,phone_lookup,verification,purpose,issued_at,expires_at,signing_key)
            VALUES(:id,:subject,:lookup,:verification,:purpose,:issued,:expires,:key)
        """), key)
        return jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": str(subject), "jti": str(key["id"]),
                           "purpose": purpose, "iat": issued, "exp": key["expires"]},
                          self.signing_key, algorithm="HS256", headers={"typ": "roghyar-otp+jwt", "kid": self.signing_key_id})

    def decode_credential(self, token: str, purpose: Purpose):
        now = self.clock()
        try:
            if type(token) is not str or len(token) > 2048 or purpose not in ("booking", "appointments"):
                raise ValueError()
            header = jwt.get_unverified_header(token)
            if header != {"alg": "HS256", "kid": self.signing_key_id, "typ": "roghyar-otp+jwt"}:
                raise ValueError()
            claims = jwt.decode(token, self.signing_key, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE,
                                options={"verify_exp": False, "verify_iat": False, "require": ["iss", "aud", "sub",
                                         "jti", "iat", "exp", "purpose"]})
            issued, expires = claims["iat"], claims["exp"]
            if (set(claims) != {"iss", "aud", "sub", "jti", "iat", "exp", "purpose"}
                    or claims["aud"] != AUDIENCE or claims["purpose"] != purpose
                    or type(issued) is not int or type(expires) is not int
                    or not 0 < expires-issued <= 900 or not issued <= now.timestamp() < expires):
                raise ValueError()
            subject, session_id = UUID(claims["sub"]), UUID(claims["jti"])
        except (ValueError, TypeError, AttributeError, jwt.InvalidTokenError):
            raise OTPDenied("invalid_session") from None
        return VerifiedSession(subject=subject, session=session_id, purpose=purpose), issued, expires

    def authorize(self, token: str, purpose: Purpose) -> VerifiedSession:
        credential, issued, expires = self.decode_credential(token, purpose)
        subject, session_id = credential.subject, credential.session
        now = self.clock()
        with self.transaction() as session:
            row = session.execute(text("""
                SELECT s.issued_at,s.expires_at FROM roghyar.otp_sessions s
                JOIN roghyar.otp_identities i ON i.subject=s.subject AND i.phone_lookup=s.phone_lookup
                JOIN roghyar.otp_challenges c ON c.id=s.verification
                WHERE s.id=:id AND s.subject=:subject AND s.purpose=:purpose AND s.signing_key=:key
                  AND s.revoked_at IS NULL AND i.revoked_at IS NULL AND c.state='consumed'
                  AND s.expires_at>:now
            """), {"id": session_id, "subject": subject, "purpose": purpose, "key": self.signing_key_id,
                   "now": now}).mappings().one_or_none()
        if (row is None or int(row["issued_at"].timestamp()) != issued
                or int(row["expires_at"].timestamp()) != expires):
            raise OTPDenied("invalid_session")
        return credential

    def revoke(self, token: str, purpose: Purpose):
        verified = self.authorize(token, purpose)
        with self.transaction() as session:
            session.execute(text("UPDATE roghyar.otp_sessions SET revoked_at=:now WHERE id=:id AND revoked_at IS NULL"),
                            {"now": self.clock(), "id": verified.session})
