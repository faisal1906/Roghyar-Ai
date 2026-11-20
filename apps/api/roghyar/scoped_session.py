"""Verify server-issued session credentials and scope transactions to their subject."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import jwt
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

PrincipalKind = Literal["anonymous", "patient", "hospital_admin"]
ISSUER = "roghyar-api"
AUDIENCE = "roghyar-session"
MAX_TOKEN_SECONDS = 900


class AuthorizationDenied(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    subject: UUID | None


class CredentialVerifier:
    """An internal verifier only. No API issues tokens or accepts identities in BE-01."""

    def __init__(self, key: SecretStr | None):
        self._key = key

    def verify(self, token: str | None) -> Principal:
        if token is None:
            return Principal("anonymous", None)
        if self._key is None or len(self._key.get_secret_value().encode()) < 32 or len(token) > 4096:
            raise AuthorizationDenied("Session authorization denied")
        try:
            claims = jwt.decode(
                token, self._key.get_secret_value(), algorithms=["HS256"], issuer=ISSUER,
                audience=AUDIENCE, options={"require": ["exp", "iat", "iss", "aud", "sub", "kind"]},
            )
            kind = claims["kind"]
            issued, expires = claims["iat"], claims["exp"]
            if (
                kind not in ("anonymous", "patient", "hospital_admin")
                or type(issued) is not int or type(expires) is not int
                or not 0 < expires - issued <= MAX_TOKEN_SECONDS
            ):
                raise ValueError()
            return Principal(kind, UUID(claims["sub"]))
        except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError, OverflowError):
            raise AuthorizationDenied("Session authorization denied") from None


@contextmanager
def scoped_session(
    engine: Engine, verifier: CredentialVerifier, token: str | None,
    *, required: PrincipalKind | None = None,
) -> Iterator[Session]:
    principal = verifier.verify(token)
    if required is not None and (principal.kind != required or principal.subject is None):
        raise AuthorizationDenied("Session authorization denied")
    with _scoped_transaction(engine, principal, "roghyar_runtime", readonly=True) as session:
        yield session


@contextmanager
def assessment_write_session(engine: Engine, verifier: CredentialVerifier, token: str | None):
    principal = verifier.verify(token)
    if principal.subject is None or principal.kind not in ("anonymous", "patient"):
        raise AuthorizationDenied("Session authorization denied")
    with _scoped_transaction(engine, principal, "roghyar_assessment_writer", readonly=False) as session:
        yield session, principal


@contextmanager
def _scoped_transaction(engine: Engine, principal: Principal, role: str, *, readonly: bool):
    with Session(engine) as session, session.begin():
        if readonly:
            session.execute(text("SET TRANSACTION READ ONLY"))
        runtime = session.execute(text("""
            SELECT session_user = :role AND current_user = session_user
              AND NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication OR rolinherit)
              AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid)
            FROM pg_roles WHERE rolname = current_user
        """), {"role": role}).scalar_one()
        if not runtime:
            raise AuthorizationDenied("Untrusted database connection")
        session.execute(text("""
            SELECT set_config('roghyar.kind', :kind, true),
                   set_config('roghyar.subject', :subject, true)
        """), {"kind": principal.kind, "subject": str(principal.subject) if principal.subject else ""})
        yield session

