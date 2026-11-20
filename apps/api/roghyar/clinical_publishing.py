"""Private signed translation verification and transactional publication."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, TypeAdapter
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine

from .classification import Language, StrictContract
from .home_care import (
    ContentBinding, HomeCareContentRepository, HomeCareResult, HomeCareSelection, SignedHomeCare,
)
from .red_flags import Digest, Identifier, canonical
from .reply_writer import ReviewerAuthority

DOMAIN = b"roghyar:home-care-translation:v1\x00"


class TranslationApproval(StrictContract):
    schema_version: Literal[1]
    binding: ContentBinding
    locale: Language
    translation_version: Annotated[int, Field(ge=1, le=2147483647)]
    reviewer: Identifier
    key_id: Identifier
    review_ref: Identifier
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["approved"]
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")] = Field(repr=False)

    def signing_bytes(self):
        return DOMAIN + canonical(self.model_dump(mode="json", exclude={"signature"}))


@dataclass(frozen=True, slots=True)
class LanguageAuthority:
    reviewer: str
    public_key: Ed25519PublicKey
    locales: frozenset[Language]
    valid_from: datetime
    valid_until: datetime
    trust_ref: str
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class TrustSnapshot:
    clinical: Mapping[str, ReviewerAuthority]
    language: Mapping[str, LanguageAuthority]
    revoked_translations: frozenset[tuple[str, int, str, str]] = frozenset()


class PublicationDenied(ValueError):
    pass


APPROVALS = TypeAdapter(Annotated[tuple[TranslationApproval, ...], Field(max_length=3)])
INSERT = text("""
    INSERT INTO roghyar.home_care_revisions
      (content_id,version,condition,applicability,digest,artifact,translations)
    VALUES (:content_id,:version,:condition,:applicability,:digest,:artifact,:translations)
""").bindparams(bindparam("artifact", type_=JSONB), bindparam("translations", type_=JSONB))
ROW = """
    SELECT content_id, version, condition, applicability, digest, artifact, translations,
           state, generation, author
    FROM roghyar.home_care_revisions
"""
UPDATE = text("""
    UPDATE roghyar.home_care_revisions SET state=:state, generation=generation+1
    WHERE content_id=:content_id AND version=:version AND generation=:generation
""")


def verify_translations(item: SignedHomeCare, approvals: tuple[TranslationApproval, ...],
                        trust: TrustSnapshot, now: datetime) -> None:
    body = item.body
    binding = ContentBinding(content_id=body.content_id, version=body.version, digest=item.digest)
    by_locale = {a.locale: a for a in approvals}
    if len(by_locale) != len(approvals) or set(by_locale) != {v.locale for v in body.variants}:
        raise PublicationDenied("translation_coverage")
    for variant in body.variants:
        review = by_locale[variant.locale]
        authority = trust.language.get(review.key_id)
        if (review.binding != binding or review.review_ref != variant.review_ref
                or (item.digest, review.translation_version, review.locale, review.key_id) in trust.revoked_translations
                or authority is None or authority.revoked or not authority.trust_ref
                or authority.reviewer != review.reviewer or review.reviewer == body.reviewer
                or review.key_id in trust.clinical or variant.locale not in authority.locales
                or review.reviewed_at.utcoffset() != timedelta(0) or review.expires_at.utcoffset() != timedelta(0)
                or not authority.valid_from <= review.reviewed_at <= now < min(review.expires_at, authority.valid_until)
                or review.reviewed_at < body.reviewed_at):
            raise PublicationDenied("translation_authority")
        try:
            authority.public_key.verify(bytes.fromhex(review.signature), review.signing_bytes())
        except InvalidSignature:
            raise PublicationDenied("translation_signature") from None


def read_artifact(row):
    item = SignedHomeCare.model_validate_json(canonical(row["artifact"]))
    if (item.body.content_id, item.body.version, item.body.condition, item.body.applicability, item.digest) != (
            row["content_id"], row["version"], row["condition"], row["applicability"], row["digest"]):
        raise PublicationDenied("artifact_binding")
    return item


class PostgresHomeCareRepository:
    """Exact runtime lookup with a fresh, independently administered trust snapshot per read."""
    def __init__(self, engine: Engine, trust: Callable[[], TrustSnapshot]):
        self._engine = engine
        self._trust = trust

    def resolve(self, selection: HomeCareSelection) -> HomeCareResult:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(text(ROW + """
                    WHERE condition=:condition AND applicability=:scope AND state='active' LIMIT 2
                """), {"condition": selection.condition, "scope": selection.applicability}).mappings().all()
            if not rows:
                return HomeCareResult(status="empty")
            if len(rows) != 1:
                return HomeCareResult(status="conflicting_active_versions")
            row = rows[0]
            item = read_artifact(row)
            trust = self._trust()
            result = HomeCareContentRepository((item,), authorities=dict(trust.clinical)).resolve(selection)
            if result.status == "verified":
                verify_translations(item, APPROVALS.validate_json(canonical(row["translations"])), trust, selection.now)
            return result
        except Exception:
            return HomeCareResult(status="invalid_artifact")


class HomeCarePublisher:
    """Use a dedicated authenticated PostgreSQL login with an explicit operator allowlist."""
    def __init__(self, engine: Engine, trust: Callable[[], TrustSnapshot], *,
                 operators: frozenset[str] = frozenset()):
        self._engine = engine
        self._trust = trust
        self._operators = frozenset(operators)

    def _authorize(self, connection: Connection):
        login, allowed = connection.execute(text("""
            SELECT session_user, pg_has_role(session_user,'roghyar_publisher','MEMBER')
                AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
            FROM pg_roles WHERE rolname=session_user
        """)).one()
        if not allowed or login not in self._operators:
            raise PublicationDenied("publisher_identity")
        connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
        return login

    def stage(self, item: SignedHomeCare, approvals: tuple[TranslationApproval, ...]) -> None:
        item = SignedHomeCare.model_validate(item.model_dump())
        approvals = APPROVALS.validate_python(tuple(a.model_dump() for a in approvals))
        body = item.body
        with self._engine.begin() as connection:
            self._authorize(connection)
            connection.execute(INSERT, dict(content_id=body.content_id, version=body.version,
                condition=body.condition, applicability=body.applicability, digest=item.digest,
                artifact=item.model_dump(mode="json"), translations=[a.model_dump(mode="json") for a in approvals]))

    def transition(self, binding: ContentBinding, state: Literal[
            "submitted", "reviewed", "ready", "active", "inactive", "revoked", "rejected"],
            *, generation: int, expected_active: tuple[str, int, int] | None = None) -> None:
        with self._engine.begin() as connection:
            login = self._authorize(connection)
            args = binding.model_dump()
            row = connection.execute(text(ROW + " WHERE content_id=:content_id AND version=:version"),
                                     args).mappings().one_or_none()
            if row is None:
                raise PublicationDenied("unknown_revision")
            scope = {"condition": row["condition"], "scope": row["applicability"]}
            connection.execute(text("""
                SELECT pg_advisory_xact_lock(hashtextextended('home_care:' || :condition || ':' || :scope,0))
            """), scope)
            row = connection.execute(text(ROW + " WHERE content_id=:content_id AND version=:version FOR UPDATE"),
                                     args).mappings().one()
            if row["generation"] != generation or row["digest"] != binding.digest:
                raise PublicationDenied("stale_revision")
            if state in ("reviewed", "ready", "active"):
                if login in {row["author"]} and state == "reviewed":
                    raise PublicationDenied("author_review")
                item = read_artifact(row)
                now = connection.scalar(text("SELECT clock_timestamp()")).astimezone(UTC)
                trust = self._trust()
                repo = HomeCareContentRepository((item,), authorities=dict(trust.clinical))
                selected = repo.resolve(HomeCareSelection(binding=binding, condition=row["condition"],
                    applicability=row["applicability"], locale=item.body.variants[0].locale, now=now))
                if selected.status != "verified":
                    raise PublicationDenied(selected.status)
                if state in ("ready", "active"):
                    verify_translations(item, APPROVALS.validate_json(canonical(row["translations"])), trust, now)
            if state == "active":
                active = connection.execute(text(ROW + """
                    WHERE condition=:condition AND applicability=:scope AND state='active' FOR UPDATE
                """), scope).mappings().one_or_none()
                actual = (active["content_id"], active["version"], active["generation"]) if active else None
                if actual != expected_active:
                    raise PublicationDenied("stale_active")
                if active:
                    connection.execute(UPDATE, {**active, "state": "superseded"})
            connection.execute(UPDATE, {**args, "state": state, "generation": generation})
