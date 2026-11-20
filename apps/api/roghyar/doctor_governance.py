"""Private doctor profile, affiliation and schedule custody over LOC-04 publication."""
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal
from unicodedata import category, normalize
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, field_validator
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .directory_governance import Digest, DirectoryDenied, Key, Label, LOCK, Source, fingerprint
from .doctor_schedules import Schedule
from .doctor_specialties import SpecialtyVocabulary
from .hospital_search import SearchModel
from .red_flags import canonical

ReviewKind = Literal["credential", "schedule"]
Registration = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")]
MAX_EVIDENCE_AGE = timedelta(days=30)


class DoctorRevision(SearchModel):
    schema_version: Literal[1]
    doctor_id: UUID
    revision: Annotated[int, Field(ge=1, le=2147483647)]
    hospital_id: UUID
    hospital_revision: Annotated[int, Field(ge=1, le=2147483647)]
    name: Label
    registry: Literal["pmdc"]
    registration: Registration = Field(repr=False)
    qualifications: Annotated[tuple[Label, ...], Field(max_length=12)] = ()
    specialties: Annotated[tuple[Key, ...], Field(min_length=1, max_length=8)]
    taxonomy_version: Annotated[int, Field(ge=1)]
    taxonomy_digest: Digest
    professional: Source = Field(repr=False)
    affiliation: Source = Field(repr=False)
    schedule_source: Source = Field(repr=False)
    schedule: Schedule

    @field_validator("name", "qualifications")
    @classmethod
    def safe_display(cls, value):
        def clean(item):
            item = normalize("NFC", item).strip()
            if (not item or item.startswith(("=", "+", "-", "@"))
                    or any(category(char).startswith("C") for char in item)):
                raise ValueError("Invalid professional display text")
            return item
        return clean(value) if isinstance(value, str) else tuple(clean(item) for item in value)

    def evidence_for(self, kind: ReviewKind):
        return (self.professional, self.affiliation) if kind == "credential" else (self.schedule_source,)

    def digest(self):
        return fingerprint(self.model_dump(mode="json"))


class DoctorReview(SearchModel):
    doctor_id: UUID
    hospital_id: UUID
    revision: Annotated[int, Field(ge=1)]
    digest: Digest
    kind: ReviewKind
    evidence_digests: Annotated[tuple[Digest, ...], Field(min_length=1, max_length=2)]
    decision: Literal["approved", "rejected", "needs_correction"]
    reviewer_id: UUID
    credential: Key
    organization: Key
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")] = Field(repr=False)

    def signing_bytes(self):
        domain = f"roghyar:doctor-{self.kind}-review:v1".encode() + b"\x00"
        return domain + canonical(self.model_dump(mode="json", exclude={"signature"}))


@dataclass(frozen=True)
class DoctorReviewer:
    login: str
    reviewer_id: UUID
    organization: str
    kind: ReviewKind
    hospitals: frozenset[UUID]
    sources: frozenset[str]
    specialties: frozenset[str]
    key: Ed25519PublicKey
    valid_from: datetime
    expires_at: datetime
    revoked: bool = False


REVISION = text("""
    SELECT body,digest,submitter FROM roghyar.doctor_revisions
    WHERE doctor_id=:doctor AND hospital_id=:hospital AND revision=:revision
""")


class DoctorDirectory:
    """Private operator workflow. Empty authority registries cannot authorize review or publication."""

    def __init__(self, engine: Engine, *, logins: frozenset[str] = frozenset(),
                 reviewers: Mapping[str, DoctorReviewer] | None = None, sources: frozenset[str] = frozenset(),
                 vocabulary: SpecialtyVocabulary | None = None):
        if engine.echo or not engine.hide_parameters:
            raise DirectoryDenied("unsafe_connection")
        self.engine = engine
        self.logins = logins
        self.reviewers = dict(reviewers or {})
        self.sources = sources
        self.vocabulary = vocabulary

    @contextmanager
    def transaction(self, kind, hospital):
        if kind not in ("importer", "credential", "schedule", "operator"):
            raise DirectoryDenied("unauthorized_operator")
        role = f"roghyar_doc_{kind}"
        try:
            with self.engine.begin() as conn:
                login, allowed = conn.execute(text("""
                    SELECT session_user, pg_has_role(session_user,:role,'MEMBER')
                       AND NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolinherit)
                    FROM pg_roles WHERE rolname=session_user
                """), {"role": role}).one()
                if not allowed or login not in self.logins:
                    raise DirectoryDenied("unauthorized_operator")
                conn.exec_driver_sql(f"SET LOCAL ROLE {role}")
                conn.execute(text("SET LOCAL statement_timeout = '3000ms'"))
                conn.execute(LOCK)
                if not conn.scalar(text("""
                    SELECT EXISTS(SELECT 1 FROM roghyar.doctor_operator_scope
                     WHERE login=session_user AND hospital_id=:hospital AND kind=:kind)
                """), {"hospital": hospital, "kind": kind}):
                    raise DirectoryDenied("unauthorized_facility")
                yield conn, login, conn.scalar(text("SELECT clock_timestamp()"))
        except IntegrityError:
            raise DirectoryDenied("transaction_conflict") from None
        except SQLAlchemyError:
            raise DirectoryDenied("doctor_directory_unavailable") from None

    def verify_evidence(self, candidate, evidence, sources, now):
        for source in sources:
            data = evidence.get(source.reference)
            if (source.organization not in self.sources or not isinstance(data, bytes) or not 0 < len(data) <= 256_000
                    or sha256(data).hexdigest() != source.evidence_digest
                    or not now - MAX_EVIDENCE_AGE < source.observed_at <= now):
                raise DirectoryDenied("evidence_denied")

    def active_taxonomy(self, candidate, now):
        taxonomy = self.vocabulary
        if (taxonomy is None or not taxonomy.active(now) or taxonomy.version != candidate.taxonomy_version
                or fingerprint(taxonomy.model_dump(mode="json")) != candidate.taxonomy_digest
                or len(set(candidate.specialties)) != len(candidate.specialties)
                or any(taxonomy.resolve(key) != key for key in candidate.specialties)):
            raise DirectoryDenied("taxonomy_denied")
        return taxonomy

    @staticmethod
    def identity(candidate):
        return {"doctor": candidate.doctor_id, "hospital": candidate.hospital_id, "revision": candidate.revision}

    def submit(self, candidate: DoctorRevision, evidence: Mapping[str, bytes]):
        candidate = DoctorRevision.model_validate(candidate.model_dump())
        identity = self.identity(candidate)
        sources = (*candidate.evidence_for("credential"), candidate.schedule_source)
        with self.transaction("importer", candidate.hospital_id) as (conn, _, now):
            self.active_taxonomy(candidate, now)
            if (candidate.professional.kind != "official_register" or candidate.affiliation.kind != "provider_attestation"
                    or candidate.schedule_source.kind != "provider_attestation"):
                raise DirectoryDenied("evidence_denied")
            self.verify_evidence(candidate, evidence, sources, now)
            self.assert_unrevoked(conn, candidate, ())
            previous = conn.execute(text("""
                SELECT revision,digest,registry,registration FROM roghyar.doctor_revisions
                WHERE doctor_id=:doctor ORDER BY revision DESC LIMIT 1
            """), identity).mappings().one_or_none()
            if previous and previous["digest"] == candidate.digest():
                return "unchanged"
            if (candidate.revision != (previous["revision"] + 1 if previous else 1)
                   or previous and (previous["registry"], previous["registration"]) !=
                       (candidate.registry, candidate.registration)):
                raise DirectoryDenied("identity_conflict")
            other = conn.scalar(text("""
                SELECT EXISTS(SELECT 1 FROM roghyar.doctor_revisions
                    WHERE registry=:registry AND registration=:registration AND doctor_id<>:doctor)
            """), identity | {"registry": candidate.registry, "registration": candidate.registration})
            if other:
                raise DirectoryDenied("identity_conflict")
            conn.execute(text("""
                INSERT INTO roghyar.doctor_revisions(doctor_id,revision,hospital_id,hospital_revision,
                                                     registry,registration,digest,body)
                VALUES(:doctor,:revision,:hospital,:hospital_revision,:registry,:registration,:digest,CAST(:body AS jsonb))
            """), identity | {"hospital_revision": candidate.hospital_revision, "registry": candidate.registry,
                              "registration": candidate.registration, "digest": candidate.digest(),
                              "body": candidate.model_dump_json()})
        return "submitted"

    @staticmethod
    def assert_unrevoked(conn, candidate, proofs):
        subjects = [item.reference for item in (*candidate.evidence_for("credential"), candidate.schedule_source)]
        if conn.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM roghyar.doctor_revocations WHERE hospital_id=:hospital AND
                 ((kind='source' AND subject=ANY(:sources)) OR (kind='key' AND subject=ANY(:keys))
                   OR (kind='revision' AND subject=:revision)))
        """), {"hospital": candidate.hospital_id, "sources": subjects, "keys": [item.credential for item in proofs],
               "revision": f"{candidate.doctor_id}:{candidate.revision}"}):
            raise DirectoryDenied("revoked")

    def verify_proof(self, proof, candidate, submitter, now, login=None):
        authority = self.reviewers.get(proof.credential)
        sources = candidate.evidence_for(proof.kind)
        if (authority is None or authority.revoked or authority.login == submitter
                or login is not None and authority.login != login
                or (proof.reviewer_id, proof.organization, proof.kind) !=
                   (authority.reviewer_id, authority.organization, authority.kind)
                or candidate.hospital_id not in authority.hospitals
                or not set(candidate.specialties) <= authority.specialties
                or not {item.organization for item in sources} <= authority.sources
                or self.identity(proof) != self.identity(candidate) or proof.digest != candidate.digest()
                or proof.evidence_digests != tuple(item.evidence_digest for item in sources)
                or not authority.valid_from <= proof.reviewed_at <= now < proof.expires_at <= authority.expires_at
                or any(not item.observed_at <= proof.reviewed_at
                       or proof.expires_at > item.observed_at + MAX_EVIDENCE_AGE for item in sources)):
            raise DirectoryDenied("review_denied")
        try:
            authority.key.verify(bytes.fromhex(proof.signature), proof.signing_bytes())
        except InvalidSignature:
            raise DirectoryDenied("review_denied") from None
        return authority

    def load(self, conn, identity):
        row = conn.execute(REVISION, identity).mappings().one_or_none()
        if row is None:
            raise DirectoryDenied("unknown_revision")
        candidate = DoctorRevision.model_validate_json(canonical(row["body"]))
        if candidate.digest() != row["digest"] or self.identity(candidate) != identity:
            raise DirectoryDenied("revision_mismatch")
        return candidate, row["submitter"]

    def review(self, proof: DoctorReview, evidence: Mapping[str, bytes]):
        proof = DoctorReview.model_validate(proof.model_dump())
        identity = self.identity(proof)
        with self.transaction(proof.kind, proof.hospital_id) as (conn, login, now):
            candidate, submitter = self.load(conn, identity)
            self.active_taxonomy(candidate, now)
            self.verify_evidence(candidate, evidence, candidate.evidence_for(proof.kind), now)
            self.verify_proof(proof, candidate, submitter, now, login)
            self.assert_unrevoked(conn, candidate, (proof,))
            conn.execute(text("""
                INSERT INTO roghyar.doctor_reviews(doctor_id,revision,hospital_id,digest,kind,credential,proof,expires_at)
                VALUES(:doctor,:revision,:hospital,:digest,:kind,:credential,CAST(:proof AS jsonb),:expires)
            """), identity | dict(digest=proof.digest, kind=proof.kind, credential=proof.credential,
                                   proof=proof.model_dump_json(), expires=proof.expires_at))

    def publish(self, doctor: UUID, hospital: UUID, revision: int):
        identity = {"doctor": doctor, "hospital": hospital, "revision": revision}
        with self.transaction("operator", hospital) as (conn, login, now):
            candidate, submitter = self.load(conn, identity)
            self.active_taxonomy(candidate, now)
            proofs = tuple(DoctorReview.model_validate_json(canonical(value)) for value in conn.scalars(text("""
                SELECT proof FROM roghyar.doctor_reviews WHERE doctor_id=:doctor
                  AND hospital_id=:hospital AND revision=:revision
            """), identity))
            if {proof.kind for proof in proofs} != {"credential", "schedule"}:
                raise DirectoryDenied("reviews_required")
            reviewers = [self.verify_proof(proof, candidate, submitter, now) for proof in proofs]
            if (len({login, submitter, *(item.login for item in reviewers)}) != 4
                    or reviewers[0].reviewer_id == reviewers[1].reviewer_id or reviewers[0].key == reviewers[1].key
                    or any(proof.decision != "approved" for proof in proofs)):
                raise DirectoryDenied("publication_denied")
            self.assert_unrevoked(conn, candidate, proofs)
            conn.execute(text("DELETE FROM roghyar.doctor_current WHERE doctor_id=:doctor AND hospital_id=:hospital"),
                         identity)
            conn.execute(text("""
                INSERT INTO roghyar.doctor_current(doctor_id,hospital_id,revision)
                VALUES(:doctor,:hospital,:revision)
            """), identity)

    def revoke(self, hospital: UUID, kind: Literal["key", "source", "revision"], subject: str):
        if kind not in ("key", "source", "revision") or not isinstance(subject, str) or not 0 < len(subject) <= 128:
            raise DirectoryDenied("invalid_revocation")
        with self.transaction("operator", hospital) as (conn, _, _):
            conn.execute(text("""
                INSERT INTO roghyar.doctor_revocations(hospital_id,kind,subject)
                VALUES(:hospital,:kind,:subject) ON CONFLICT DO NOTHING
            """), {"hospital": hospital, "kind": kind, "subject": subject})

    def withdraw(self, doctor: UUID, hospital: UUID, revision: int):
        self.revoke(hospital, "revision", f"{doctor}:{revision}")
