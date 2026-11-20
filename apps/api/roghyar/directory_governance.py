"""Private, bounded directory import and independent review workflow."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Annotated, Literal
from unicodedata import category, normalize
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, field_validator
from sqlalchemy import Engine, text

from .hospital_search import SearchModel
from .red_flags import canonical

MAX_BATCH_BYTES = 256_000
MAX_AGE = timedelta(days=30)  # Conservative engineering ceiling, subject to provider governance approval.
FIELDS = (
    "name", "official_name", "source_name", "address", "city", "district", "province", "latitude",
    "longitude", "phone", "departments", "emergency", "aliases",
)
Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=180)]


class DirectoryDenied(ValueError):
    """Stable, non-sensitive denial."""


def fingerprint(value) -> str:
    return sha256(canonical(value)).hexdigest()


class Source(SearchModel):
    organization: Key
    record: Key
    version: Annotated[int, Field(gt=0, le=2147483647)]
    kind: Literal["provider_attestation", "official_register"]
    reference: Label
    observed_at: AwareDatetime
    evidence_digest: Digest

    @field_validator("reference")
    @classmethod
    def valid_reference(cls, value):
        # An opaque private credential-free evidence-store reference, not a URL or fetch target.
        if not value.isascii() or not all(c.isalnum() or c in "-_." for c in value):
            raise ValueError("Invalid evidence reference")
        return value


class HospitalRevision(SearchModel):
    schema_version: Literal[1]
    hospital_id: UUID
    revision: Annotated[int, Field(gt=0, le=2147483647)]
    name: Label
    official_name: Label | None = None
    source_name: Label
    address: Label
    city: Label
    district: Label
    province: Label
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
    phone: Annotated[str, StringConstraints(pattern=r"^\+[1-9][0-9]{6,14}$")] | None = None
    departments: Annotated[tuple[Label, ...], Field(max_length=20)] = ()
    emergency: bool | None = None
    aliases: Annotated[tuple[Label, ...], Field(max_length=16)] = ()
    source: Source

    @field_validator("name", "official_name", "source_name", "address", "city", "district", "province",
                     "aliases", "departments")
    @classmethod
    def safe_text(cls, value):
        def clean(item):
            if any(category(char).startswith("C") for char in item):
                raise ValueError("Invalid directory text")
            item = " ".join(normalize("NFC", item).split())
            if not item or item.startswith(("=", "+", "-", "@")) or any(char in "%_\\" for char in item):
                raise ValueError("Invalid directory text")
            return item
        if value is None:
            return None
        if isinstance(value, tuple):
            entries = tuple(clean(item) for item in value)
            if len({item.casefold() for item in entries}) != len(entries):
                raise ValueError("Duplicate directory text")
            return entries
        return clean(value)

    def digest(self):
        return fingerprint(self.model_dump(mode="json"))

    def field_digests(self):
        data = self.model_dump(mode="json")
        return {key: fingerprint(data[key]) for key in FIELDS}


class ImportBatch(SearchModel):
    schema_version: Literal[1]
    batch_id: UUID
    records: Annotated[tuple[HospitalRevision, ...], Field(min_length=1, max_length=100)]


def reject_duplicate_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise DirectoryDenied("duplicate_json_key")
    return result


def validate_batch(payload: bytes):
    if not isinstance(payload, bytes) or len(payload) > MAX_BATCH_BYTES:
        raise DirectoryDenied("batch_too_large")
    try:
        json.loads(payload, object_pairs_hook=reject_duplicate_keys)
        batch = ImportBatch.model_validate_json(payload)
    except (ValidationError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise DirectoryDenied("invalid_batch") from None
    if (len({(r.hospital_id, r.revision) for r in batch.records}) != len(batch.records)
            or len({(r.source.organization, r.source.record, r.source.version) for r in batch.records}) != len(batch.records)):
        raise DirectoryDenied("duplicate_batch_record")
    return batch


class Review(SearchModel):
    hospital_id: UUID
    revision: Annotated[int, Field(gt=0)]
    digest: Digest
    evidence_digest: Digest
    field_digests: Annotated[dict[Key, Digest], Field(max_length=len(FIELDS))]
    decision: Literal["approved", "rejected", "needs_correction"]
    reviewer_id: UUID
    credential: Key
    organization: Key
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")] = Field(repr=False)

    def signing_bytes(self):
        return b"roghyar:directory-review:v1\x00" + canonical(self.model_dump(mode="json", exclude={"signature"}))


@dataclass(frozen=True)
class DirectoryReviewer:
    login: str
    reviewer_id: UUID
    organization: str
    sources: frozenset[str]
    key: Ed25519PublicKey
    valid_from: datetime
    expires_at: datetime
    revoked: bool = False


LOCK = text("SELECT pg_advisory_xact_lock(79350919)")
REVISION = text("SELECT * FROM roghyar.directory_revisions WHERE hospital_id=:hospital AND revision=:revision")


class Directory:
    """Private operator methods. Separate authenticated DB logins are required per role."""

    def __init__(self, engine: Engine, *, reviewers: Mapping[str, DirectoryReviewer] | None = None,
                 sources: frozenset[str] = frozenset(), logins: frozenset[str] = frozenset(),
                 evidence_max_age: timedelta = MAX_AGE):
        if engine.echo or not engine.hide_parameters:
            raise DirectoryDenied("unsafe_connection")
        if not timedelta(0) < evidence_max_age <= MAX_AGE:
            raise DirectoryDenied("invalid_freshness_policy")
        self.evidence_max_age = evidence_max_age
        self.engine = engine
        self.reviewers = dict(reviewers or {})
        self.sources = sources
        self.logins = logins

    def authorize(self, connection, kind):
        role = f"roghyar_directory_{kind}"
        login, allowed = connection.execute(text("""
            SELECT session_user, pg_has_role(session_user,:role,'MEMBER')
                AND NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolinherit)
            FROM pg_roles WHERE rolname=session_user
        """), {"role": role}).one()
        if not allowed or login not in self.logins:
            raise DirectoryDenied("unauthorized_operator")
        connection.exec_driver_sql(f"SET LOCAL ROLE {role}")
        connection.execute(LOCK)
        return login

    def submit(self, payload: bytes, evidence: Mapping[str, bytes]):
        batch = validate_batch(payload)
        results = []
        with self.engine.begin() as conn:
            self.authorize(conn, "importer")
            now = conn.scalar(text("SELECT clock_timestamp()"))
            for record in batch.records:
                source = record.source
                blob = evidence.get(source.reference)
                if (source.organization not in self.sources or not isinstance(blob, bytes)
                        or not 0 < len(blob) <= 256_000 or sha256(blob).hexdigest() != source.evidence_digest
                        or not now - self.evidence_max_age < source.observed_at <= now):
                    raise DirectoryDenied("invalid_source_evidence")
                if self.revoked(conn, "source", f"{source.organization}:{source.record}"):
                    raise DirectoryDenied("source_withdrawn")
                identity = {"hospital": record.hospital_id, "revision": record.revision}
                existing = conn.execute(text("""
                    SELECT hospital_id,revision,digest,source_version FROM roghyar.directory_revisions
                    WHERE source_org=:org AND source_record=:source ORDER BY source_version DESC LIMIT 1
                """), {"org": source.organization, "source": source.record}).mappings().one_or_none()
                earlier = conn.execute(text("""
                    SELECT source_org,source_record,revision FROM roghyar.directory_revisions
                    WHERE hospital_id=:hospital ORDER BY revision DESC LIMIT 1
                """), {"hospital": record.hospital_id}).mappings().one_or_none()
                if existing and existing["digest"] == record.digest():
                    results.append("unchanged")
                    continue
                if (existing and (existing["hospital_id"] != record.hospital_id or
                                  existing["source_version"] >= source.version)
                        or earlier and ((earlier["source_org"], earlier["source_record"]) != (source.organization, source.record)
                                        or record.revision != earlier["revision"] + 1)
                        or not earlier and record.revision != 1):
                    raise DirectoryDenied("identity_conflict")
                conn.execute(text("""
                    INSERT INTO roghyar.directory_revisions
                        (hospital_id,revision,source_org,source_record,source_version,batch_id,body,evidence_digest,digest)
                    VALUES (:hospital,:revision,:org,:source,:version,:batch,CAST(:body AS jsonb),:evidence,:digest)
                """), identity | dict(org=source.organization, source=source.record, version=source.version,
                    batch=batch.batch_id, body=record.model_dump_json(), evidence=source.evidence_digest, digest=record.digest()))
                results.append("submitted")
        return tuple(results)

    @staticmethod
    def revoked(conn, kind, subject):
        return conn.scalar(text("SELECT EXISTS (SELECT 1 FROM roghyar.directory_revocations "
                                "WHERE kind=:kind AND subject=:subject)"), {"kind": kind, "subject": subject})

    def verify(self, conn, row, approval, now, login=None):
        authority = self.reviewers.get(approval.credential)
        candidate = HospitalRevision.model_validate_json(canonical(row["body"]))
        if (authority is None or authority.revoked or authority.login == row["submitter"]
                or login is not None and authority.login != login
                or (authority.reviewer_id, authority.organization) != (approval.reviewer_id, approval.organization)
                or row["source_org"] not in authority.sources
                or not authority.valid_from <= approval.reviewed_at <= now < approval.expires_at <= authority.expires_at
                or not candidate.source.observed_at <= approval.reviewed_at
                or approval.expires_at > candidate.source.observed_at + self.evidence_max_age
                or (row["digest"], row["evidence_digest"]) != (approval.digest, approval.evidence_digest)
                or candidate.digest() != approval.digest or candidate.field_digests() != approval.field_digests
                or self.revoked(conn, "key", approval.credential)
                or self.revoked(conn, "source", f"{row['source_org']}:{row['source_record']}")):
            raise DirectoryDenied("review_denied")
        try:
            authority.key.verify(bytes.fromhex(approval.signature), approval.signing_bytes())
        except InvalidSignature:
            raise DirectoryDenied("review_denied") from None
        return authority

    def candidate_for_review(self, hospital: UUID, revision: int) -> HospitalRevision:
        with self.engine.begin() as conn:
            self.authorize(conn, "reviewer")
            row = conn.execute(REVISION, {"hospital": hospital, "revision": revision}).mappings().one_or_none()
            if row is None:
                raise DirectoryDenied("unknown_revision")
            return HospitalRevision.model_validate_json(canonical(row["body"]))

    def review(self, approval: Review, *, evidence: bytes):
        approval = Review.model_validate(approval.model_dump())
        identity = {"hospital": approval.hospital_id, "revision": approval.revision}
        with self.engine.begin() as conn:
            login = self.authorize(conn, "reviewer")
            row = conn.execute(REVISION, identity).mappings().one_or_none()
            if row is None:
                raise DirectoryDenied("unknown_revision")
            if (not isinstance(evidence, bytes) or not 0 < len(evidence) <= 256_000
                    or sha256(evidence).hexdigest() != row["evidence_digest"]):
                raise DirectoryDenied("invalid_source_evidence")
            self.verify(conn, row, approval, conn.scalar(text("SELECT clock_timestamp()")), login)
            conn.execute(text("""
                INSERT INTO roghyar.directory_reviews(hospital_id,revision,digest,credential,approval,expires_at)
                VALUES (:hospital,:revision,:digest,:credential,CAST(:approval AS jsonb),:expires)
            """), identity | dict(digest=approval.digest, credential=approval.credential,
                approval=approval.model_dump_json(), expires=approval.expires_at))

    def publish(self, hospital: UUID, revision: int):
        identity = {"hospital": hospital, "revision": revision}
        with self.engine.begin() as conn:
            login = self.authorize(conn, "operator")
            row = conn.execute(REVISION, identity).mappings().one_or_none()
            review = conn.execute(text("""
                SELECT approval FROM roghyar.directory_reviews WHERE hospital_id=:hospital AND revision=:revision
            """), identity).scalar_one_or_none()
            if row is None or review is None:
                raise DirectoryDenied("no_review")
            approval = Review.model_validate_json(canonical(review))
            authority = self.verify(conn, row, approval, conn.scalar(text("SELECT clock_timestamp()")))
            if approval.decision != "approved" or login in (authority.login, row["submitter"]):
                raise DirectoryDenied("publication_denied")
            latest = conn.scalar(text("SELECT max(revision) FROM roghyar.directory_revisions WHERE hospital_id=:hospital"),
                                 identity)
            if latest != revision:
                raise DirectoryDenied("stale_revision")
            conn.execute(text("DELETE FROM roghyar.directory_current WHERE hospital_id=:hospital"), identity)
            conn.execute(text("""
                INSERT INTO roghyar.directory_current(hospital_id,revision,expires_at)
                VALUES (:hospital,:revision,:expires)
            """), identity | {"expires": approval.expires_at})

    def withdraw(self, hospital: UUID):
        with self.engine.begin() as conn:
            self.authorize(conn, "operator")
            active = conn.execute(text("""
                DELETE FROM roghyar.directory_current WHERE hospital_id=:hospital RETURNING revision
            """), {"hospital": hospital}).scalar_one_or_none()
            revision = conn.scalar(text("""
                SELECT max(revision) FROM roghyar.directory_revisions WHERE hospital_id=:hospital
            """), {"hospital": hospital})
            # An unpublished latest revision is explicitly fenced against a racing publication.
            if revision is not None and revision != active:
                subject = f"{hospital}:{revision}"
                self.add_revocation(conn, "revision", subject)
                return subject
            return None

    def withdraw_alias(self, hospital: UUID, revision: int, alias: str):
        if not isinstance(alias, str) or len(alias) > 180:
            raise DirectoryDenied("invalid_alias")
        with self.engine.begin() as conn:
            self.authorize(conn, "operator")
            identity = {"hospital": hospital, "revision": revision}
            row = conn.execute(REVISION, identity).mappings().one_or_none()
            if row is None or alias not in row["body"]["aliases"]:
                raise DirectoryDenied("unknown_alias")
            subject = f"{hospital}:{revision}:{sha256(alias.encode()).hexdigest()}"
            self.add_revocation(conn, "alias", subject)
            return subject

    @staticmethod
    def add_revocation(conn, kind, subject):
        conn.execute(text("""
            INSERT INTO roghyar.directory_revocations(kind,subject) VALUES (:kind,:subject) ON CONFLICT DO NOTHING
        """), {"kind": kind, "subject": subject})

    def revoke(self, kind: Literal["source", "key"], subject: str):
        if kind not in ("source", "key") or not isinstance(subject, str) or not 0 < len(subject) <= 129:
            raise DirectoryDenied("invalid_revocation")
        with self.engine.begin() as conn:
            self.authorize(conn, "operator")
            self.add_revocation(conn, kind, subject)

    def due_for_revalidation(self):
        with self.engine.begin() as conn:
            self.authorize(conn, "operator")
            return tuple(dict(row) for row in conn.execute(text("""
                SELECT hospital_id,revision,expires_at,
                   CASE WHEN expires_at <= statement_timestamp() THEN 'expired' ELSE 'due' END AS status
                FROM roghyar.directory_current
                WHERE expires_at <= statement_timestamp() + interval '7 days'
                ORDER BY expires_at,hospital_id LIMIT 100
            """)).mappings())

    def inspection(self):
        """Sanitized immutable publication events."""
        with self.engine.begin() as conn:
            self.authorize(conn, "operator")
            return tuple(dict(row) for row in conn.execute(text("""
                SELECT hospital_id,revision,action,occurred_at FROM roghyar.directory_events
                ORDER BY occurred_at DESC,id LIMIT 100
            """)).mappings())
