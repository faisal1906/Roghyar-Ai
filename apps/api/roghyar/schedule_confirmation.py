"""Independently signed, date-specific operational evidence."""
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .doctor_schedules import LOCAL, SessionHours, nonoverlapping
from .hospital_search import Identifier, SearchModel
from .red_flags import canonical
from .scoped_session import AuthorizationDenied, Principal, _scoped_transaction

Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Signature = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{128}$")]
Version = Annotated[int, Field(ge=1, le=2147483647)]
State = Literal["working", "replacement", "closure", "leave", "holiday", "withdrawn", "unavailable"]
DOMAIN = b"roghyar:booking-schedule-confirmation:v1\0"


class Confirmation(SearchModel):
    schema_version: Literal[1]
    doctor_id: UUID
    hospital_id: UUID
    day: date
    sequence: Version
    doctor_revision: Version
    hospital_revision: Version
    schedule_digest: Digest
    state: State
    sessions: Annotated[tuple[SessionHours, ...], Field(max_length=32)] = ()
    organization: Identifier
    source_reference: Identifier
    source_version: Version
    evidence_digest: Digest
    confirmed_at: AwareDatetime
    effective_at: AwareDatetime
    expires_at: AwareDatetime
    reviewer: UUID
    signature: Signature = Field(repr=False)

    @model_validator(mode="after")
    def validate_scope(self):
        if (not self.confirmed_at <= self.effective_at < self.expires_at
                or self.expires_at - self.confirmed_at > timedelta(days=31)
                or not 0 <= (self.day - self.confirmed_at.astimezone(LOCAL).date()).days <= 30
                or bool(self.sessions) != (self.state in ("working", "replacement"))):
            raise ValueError("Invalid confirmation scope")
        nonoverlapping(self.sessions)
        if tuple(sorted(self.sessions, key=lambda item: item.start)) != self.sessions:
            raise ValueError("Invalid session ordering")
        return self

    def signing_bytes(self):
        return DOMAIN + canonical(self.model_dump(mode="json", exclude={"signature"}))


@dataclass(frozen=True)
class Authority:
    public_key: Ed25519PublicKey
    hospital_id: UUID
    organization: str
    valid_from: datetime
    valid_until: datetime


@dataclass(frozen=True)
class TrustSnapshot:
    authorities: dict[UUID, Authority]
    revoked: frozenset[UUID] = frozenset()
    withdrawn_sources: frozenset[tuple[UUID, str]] = frozenset()
    revoked_confirmations: frozenset[str] = frozenset()


class TrustRegistry(Protocol):
    def snapshot(self) -> TrustSnapshot: ...


class ConfirmationDenied(ValueError):
    pass


def authenticate(proof: Confirmation, trust: TrustSnapshot, now: datetime):
    authority = trust.authorities.get(proof.reviewer)
    if (authority is None or authority.hospital_id != proof.hospital_id
            or authority.organization != proof.organization or proof.reviewer in trust.revoked
            or (proof.hospital_id, proof.organization) in trust.withdrawn_sources
            or sha256(proof.signing_bytes()).hexdigest() in trust.revoked_confirmations
            or not authority.valid_from <= proof.confirmed_at <= now < authority.valid_until):
        raise ConfirmationDenied("untrusted_confirmation")
    try:
        authority.public_key.verify(bytes.fromhex(proof.signature), proof.signing_bytes())
    except InvalidSignature:
        raise ConfirmationDenied("untrusted_confirmation") from None
    return authority.valid_until


class SubmissionSource(Protocol):
    """Approved adapters return signed evidence once, within their configured deadline."""

    def retrieve(self) -> tuple[bytes, bytes]: ...


class ConfirmationWriter:
    def __init__(self, engine: Engine, trust: TrustRegistry, *, clock=lambda: datetime.now(UTC)):
        if engine.echo or not engine.hide_parameters:
            raise ValueError("Unsafe database configuration")
        self.engine, self.trust, self.clock = engine, trust, clock

    def synchronize(self, provider: SubmissionSource):
        try:
            document, evidence = provider.retrieve()
        except Exception:
            raise ConfirmationDenied("provider_unavailable") from None
        return self.submit(document, evidence)

    def submit(self, document: bytes, evidence: bytes):
        if type(document) is not bytes or type(evidence) is not bytes or not (
                0 < len(document) <= 8192 and 0 < len(evidence) <= 65536):
            raise ConfirmationDenied("invalid_confirmation")
        try:
            import json
            from .directory_governance import reject_duplicate_keys

            proof = Confirmation.model_validate_json(canonical(json.loads(document, object_pairs_hook=reject_duplicate_keys)))
        except (ValueError, RecursionError):
            raise ConfirmationDenied("invalid_confirmation") from None
        if sha256(evidence).hexdigest() != proof.evidence_digest:
            raise ConfirmationDenied("evidence_mismatch")
        scope = {name: getattr(proof, name) for name in ("doctor_id", "hospital_id", "day")}
        lock = canonical([str(proof.doctor_id), str(proof.hospital_id), str(proof.day)])
        try:
            with _scoped_transaction(self.engine, Principal("anonymous", None), "roghyar_slot_writer",
                                     readonly=False) as session:
                session.execute(text("SET LOCAL statement_timeout='1500ms'"))
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                {"key": int.from_bytes(sha256(lock).digest()[:8], "big", signed=True)})
                now = self.clock()
                authenticate(proof, self.trust.snapshot(), now)
                if now >= proof.expires_at:
                    raise ConfirmationDenied("schedule_expired")
                previous = session.execute(text("""
                    SELECT sequence,proof FROM roghyar.slot_confirmations
                    WHERE doctor_id=:doctor_id AND hospital_id=:hospital_id AND day=:day
                    ORDER BY sequence DESC LIMIT 1
                """), scope).one_or_none()
                if proof.sequence != (1 if previous is None else previous.sequence + 1):
                    raise ConfirmationDenied("stale_confirmation")
                if previous is not None:
                    old = Confirmation.model_validate_json(previous.proof)
                    if (proof.organization != old.organization or proof.source_version <= old.source_version
                            or proof.confirmed_at < old.confirmed_at or proof.doctor_revision < old.doctor_revision
                            or proof.hospital_revision < old.hospital_revision or old.state == "withdrawn"):
                        raise ConfirmationDenied("stale_confirmation")
                session.execute(text("""
                    INSERT INTO roghyar.slot_confirmations(doctor_id,hospital_id,day,sequence,
                        doctor_revision,hospital_revision,proof)
                    VALUES(:doctor_id,:hospital_id,:day,:sequence,:doctor_revision,:hospital_revision,:proof)
                """), scope | {"sequence": proof.sequence, "doctor_revision": proof.doctor_revision,
                               "hospital_revision": proof.hospital_revision, "proof": proof.model_dump_json()})
        except ConfirmationDenied:
            raise
        except (SQLAlchemyError, AuthorizationDenied, ValidationError):
            raise ConfirmationDenied("confirmation_storage_unavailable") from None
        except Exception:
            raise ConfirmationDenied("trust_unavailable") from None
        return proof.sequence
