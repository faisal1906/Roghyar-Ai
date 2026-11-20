"""Signed review and deterministic screening for the private formulary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, model_validator
from sqlalchemy import Engine, text

from .assessment import require_disposable
from .red_flags import Identifier, StrictModel, canonical
from .scoped_session import CredentialVerifier, scoped_session
from .settings import Settings
from .triage import TriageService, TriageState

DOMAIN = b"roghyar:formulary-review:v1\x00"
MAX_ROWS = 64
MAX_BYTES = 16384
Age = Annotated[int, Field(ge=0, le=120)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=512, pattern=r"\S")]
Labels = Annotated[list[Label], Field(max_length=16)]
Identifiers = Annotated[list[Identifier], Field(max_length=64)]


class Dose(StrictModel):
    form: Literal["tablet", "capsule", "oral_liquid"]
    unit: Literal["mg", "mL", "tablet", "capsule"]
    instructions: Label
    frequency: Label
    maximum_per_day: Label

    @model_validator(mode="after")
    def compatible_unit(self):
        allowed = {"tablet": ("mg", "tablet"), "capsule": ("mg", "capsule"),
                   "oral_liquid": ("mg", "mL")}
        if self.unit not in allowed[self.form]:
            raise ValueError("Incompatible dose representation")
        return self


class ChildBand(StrictModel):
    first_age: int = Field(ge=1, le=17)
    last_age: int = Field(ge=1, le=17)
    dose: Dose


class Screening(StrictModel):
    pregnancy: Literal["not_pregnant_only", "any_known_status"]
    allergy_ids: Annotated[list[Identifier], Field(min_length=1, max_length=64)]
    blocked_conditions: Identifiers
    interactions_reviewed: bool
    compatible_medicines: Identifiers
    blocked_medicines: Identifiers

    @model_validator(mode="after")
    def unique_and_consistent(self):
        for values in (self.allergy_ids, self.blocked_conditions, self.compatible_medicines,
                       self.blocked_medicines):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate screening criterion")
        if set(self.compatible_medicines) & set(self.blocked_medicines):
            raise ValueError("Conflicting interaction criteria")
        return self


class ReviewedPayload(StrictModel):
    schema_version: Literal[1]
    key_id: Identifier
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]
    adult: Dose | None
    screening: Screening


class FormularyRecord(StrictModel):
    id: UUID
    condition_code: Identifier
    medicine: Identifier
    version: int = Field(ge=1, le=2147483647)
    review_status: Literal["approved"]
    reviewed_by: UUID
    reviewed_at: AwareDatetime
    active: Literal[True]
    min_age: Age
    max_age: Age
    adult_dose: Label | None
    frequency: Label | None
    max_daily: Label | None
    child_dose_table: Annotated[list[ChildBand], Field(max_length=8)] | None
    contraindications: Labels
    home_steps: Labels | None
    see_doctor_if: Labels | None
    reviewed_payload: ReviewedPayload

    @model_validator(mode="after")
    def consistent_doses(self):
        adult = self.reviewed_payload.adult
        exact = (adult.instructions, adult.frequency, adult.maximum_per_day) if adult else (None, None, None)
        if exact != (self.adult_dose, self.frequency, self.max_daily):
            raise ValueError("Dose representations disagree")
        if self.min_age > self.max_age or (self.max_age >= 18) != (adult is not None):
            raise ValueError("Inconsistent age or adult dose coverage")
        bands = self.child_dose_table or []
        if adult is None and not bands:
            raise ValueError("Missing applicable dose")
        last = 0
        for band in bands:
            if not max(self.min_age, last + 1) <= band.first_age <= band.last_age <= self.max_age:
                raise ValueError("Invalid or overlapping child age bands")
            last = band.last_age
        return self


class SafetyContext(StrictModel):
    # A trusted adapter must encode unknown, incomplete, or unreliable fields as null.
    age: Age | None = None
    pregnancy: Literal["pregnant", "not_pregnant", "not_applicable"] | None = None
    allergies: Identifiers | None = None
    conditions: Identifiers | None = None
    current_medicines: Identifiers | None = None


@dataclass(frozen=True)
class Vocabulary:
    medicines: frozenset[str]
    conditions: frozenset[str]
    allergens: frozenset[str]


@dataclass(frozen=True)
class FormularyReviewer:
    identity: UUID
    public_key: Ed25519PublicKey
    authorized_medicines: frozenset[str]
    authorized_conditions: frozenset[str]
    valid_from: datetime
    valid_until: datetime


class SelectionReason(StrEnum):
    NO_APPROVED_CONTENT = "no_approved_content"
    INVALID_ATTESTATION = "invalid_attestation"
    INVALID_CONFIGURATION = "invalid_configuration"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    CONTRAINDICATED = "contraindicated"
    INTERACTIONS_UNVERIFIED = "interactions_unverified"
    AGE_OR_PREGNANCY_MISMATCH = "age_or_pregnancy_mismatch"
    UNSUPPORTED_PEDIATRIC_DOSE = "unsupported_pediatric_dose"
    NO_SELECTION_POLICY = "no_selection_policy"
    TRIAGE_NOT_AUTHORIZED = "triage_not_authorized"
    EVALUATION_FAILURE = "evaluation_failure"
    SYNTHETIC_ELIGIBLE = "synthetic_eligible"


class Selection(StrictModel):
    reason: SelectionReason
    triage_state: TriageState | None = None


class SyntheticSelection(StrictModel):
    reason: Literal[SelectionReason.SYNTHETIC_ELIGIBLE] = SelectionReason.SYNTHETIC_ELIGIBLE
    record_id: UUID
    version: int
    medicine: Identifier
    dose: Dose


class InvalidAttestation(ValueError):
    pass


def signature_message(record: FormularyRecord) -> bytes:
    data = record.model_dump(mode="json", exclude={
        "active": True, "review_status": True, "reviewed_payload": {"signature"},
    })
    data["reviewed_at"] = record.reviewed_at.astimezone(timezone.utc).isoformat()
    return DOMAIN + canonical(data)


def verify_candidate(raw: dict, reviewers: Mapping[str, FormularyReviewer],
                     vocabulary: Vocabulary, now: datetime) -> FormularyRecord:
    payload = raw.get("reviewed_payload")
    if not isinstance(payload, dict) or not payload.get("signature") or not payload.get("key_id"):
        raise InvalidAttestation()
    if type(payload.get("schema_version")) is not int:
        raise ValueError("Invalid schema version")
    row = FormularyRecord.model_validate(raw)
    screen = row.reviewed_payload.screening
    if (
        row.medicine not in vocabulary.medicines or row.condition_code not in vocabulary.conditions
        or not set(screen.allergy_ids) <= vocabulary.allergens
        or not set(screen.blocked_conditions) <= vocabulary.conditions
        or not (set(screen.compatible_medicines) | set(screen.blocked_medicines)) <= vocabulary.medicines
        or row.medicine in screen.compatible_medicines
    ):
        raise ValueError("Invalid review identifiers")
    authority = reviewers.get(row.reviewed_payload.key_id)
    if (
        authority is None or authority.identity != row.reviewed_by
        or row.medicine not in authority.authorized_medicines
        or row.condition_code not in authority.authorized_conditions
        or not authority.valid_from <= row.reviewed_at <= now < authority.valid_until
    ):
        raise InvalidAttestation()
    message = signature_message(row)
    if len(message) > MAX_BYTES:
        raise ValueError("Review exceeds size limits")
    try:
        authority.public_key.verify(bytes.fromhex(row.reviewed_payload.signature), message)
    except InvalidSignature:
        raise InvalidAttestation() from None
    return row


def _screen(row: FormularyRecord, context: SafetyContext, vocabulary: Vocabulary) -> Dose | SelectionReason:
    if context.age is None or context.pregnancy is None or context.allergies is None or context.conditions is None:
        return SelectionReason.INSUFFICIENT_INFORMATION
    if not set(context.allergies) <= vocabulary.allergens or not set(context.conditions) <= vocabulary.conditions:
        return SelectionReason.INSUFFICIENT_INFORMATION
    screen = row.reviewed_payload.screening
    # Duplicate ingredient screening is always required, independent of the signed interaction lists.
    medicines = set(context.current_medicines or ())
    if (set(context.allergies) & set(screen.allergy_ids)
        or set(context.conditions) & set(screen.blocked_conditions)
        or medicines & ({row.medicine} | set(screen.blocked_medicines))):
        return SelectionReason.CONTRAINDICATED
    if (not screen.interactions_reviewed or context.current_medicines is None
        or not medicines <= set(screen.compatible_medicines)):
        return SelectionReason.INTERACTIONS_UNVERIFIED
    if not row.min_age <= context.age <= row.max_age or (
        context.pregnancy == "pregnant" and screen.pregnancy == "not_pregnant_only"
    ):
        return SelectionReason.AGE_OR_PREGNANCY_MISMATCH
    if context.age >= 18 and row.reviewed_payload.adult is not None:
        return row.reviewed_payload.adult
    for band in row.child_dose_table or []:
        if band.first_age <= context.age <= band.last_age:
            return band.dose
    return SelectionReason.UNSUPPORTED_PEDIATRIC_DOSE


def _screen_snapshot(rows, context: object, reviewers, vocabulary: Vocabulary) -> Selection | SyntheticSelection:
    try:
        active = [row for row in rows if row["active"] is True and row["review_status"] == "approved"]
        if not active:
            return Selection(reason=SelectionReason.NO_APPROVED_CONTENT)
        if len(active) > MAX_ROWS:
            return Selection(reason=SelectionReason.INVALID_CONFIGURATION)
        now = datetime.now(timezone.utc)
        approved = [verify_candidate(row, reviewers, vocabulary, now) for row in active]
        identities = {(row.condition_code, row.medicine) for row in approved}
        if len(identities) != len(approved) or len({row.condition_code for row in approved}) != 1:
            return Selection(reason=SelectionReason.INVALID_CONFIGURATION)
    except InvalidAttestation:
        return Selection(reason=SelectionReason.INVALID_ATTESTATION)
    except (ValueError, TypeError, KeyError):
        return Selection(reason=SelectionReason.INVALID_CONFIGURATION)
    try:
        if isinstance(context, SafetyContext):
            context = context.model_dump()
        safety = SafetyContext.model_validate(context)
    except ValueError:
        return Selection(reason=SelectionReason.INSUFFICIENT_INFORMATION)
    screened = [(row, _screen(row, safety, vocabulary)) for row in approved]
    eligible = [(row, dose) for row, dose in screened if isinstance(dose, Dose)]
    if len(eligible) > 1:
        return Selection(reason=SelectionReason.NO_SELECTION_POLICY)
    if eligible:
        row, dose = eligible[0]
        return SyntheticSelection(record_id=row.id, version=row.version, medicine=row.medicine, dose=dose)
    ranking = (
        SelectionReason.CONTRAINDICATED, SelectionReason.INSUFFICIENT_INFORMATION,
        SelectionReason.INTERACTIONS_UNVERIFIED, SelectionReason.AGE_OR_PREGNANCY_MISMATCH,
        SelectionReason.UNSUPPORTED_PEDIATRIC_DOSE,
    )
    denied = {reason for _, reason in screened}
    return Selection(reason=next(reason for reason in ranking if reason in denied))


class PostgresFormularyRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def load(self, condition: str) -> list[dict]:
        with scoped_session(self.engine, CredentialVerifier(None), None) as session:
            rows = session.execute(text("""
                SELECT id, condition_code, medicine, version, review_status, reviewed_by, reviewed_at,
                       active, min_age, max_age, adult_dose, frequency, max_daily, child_dose_table,
                       contraindications, home_steps, see_doctor_if, reviewed_payload
                FROM roghyar.formulary
                WHERE active AND review_status = 'approved' AND condition_code = :condition
                ORDER BY medicine, version LIMIT :limit
            """), {"condition": condition, "limit": MAX_ROWS + 1})
            return [dict(row) for row in rows.mappings()]


class FormularyService:
    def __init__(self, engine: Engine, vocabulary: Vocabulary,
                 reviewers: Mapping[str, FormularyReviewer], triage: TriageService):
        self._repository = PostgresFormularyRepository(engine)
        self._vocabulary = vocabulary
        self._reviewers = dict(reviewers)
        self._triage = triage

    def evaluate(self, evidence: object) -> Selection:
        # Future GD-01 must supply an independently approved HOME CARE authorization
        # bound to this decision snapshot. BE-04 alone cannot authorize medication.
        try:
            decision = self._triage.evaluate(evidence)
            return Selection(reason=SelectionReason.TRIAGE_NOT_AUTHORIZED, triage_state=decision.state)
        except Exception:
            return Selection(reason=SelectionReason.EVALUATION_FAILURE)

    def probe_synthetic(self, condition: str, context: object, settings: Settings) -> Selection | SyntheticSelection:
        require_disposable(self._repository.engine, settings=settings)
        if condition not in self._vocabulary.conditions:
            return Selection(reason=SelectionReason.INVALID_CONFIGURATION)
        try:
            return _screen_snapshot(self._repository.load(condition), context, self._reviewers, self._vocabulary)
        except Exception:
            return Selection(reason=SelectionReason.EVALUATION_FAILURE)
