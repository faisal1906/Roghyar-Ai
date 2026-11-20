"""Strict, encrypted assessment drafts for isolated integration use."""

import json
import unicodedata
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints,
    ValidationError, model_validator,
)
from sqlalchemy import Engine, text

from .field_encryption import FieldEncryption
from .scoped_session import AuthorizationDenied, CredentialVerifier, assessment_write_session, scoped_session
from .settings import Settings

ASSESSMENT_NAMESPACE = UUID("2ae3df79-d223-4a1c-a4f2-93f257be4666")


def normalize_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    value = unicodedata.normalize("NFC", value).strip()
    if not any(char.isalnum() for char in value):
        raise ValueError("Meaningful text is required")
    if any(unicodedata.category(char).startswith("C") and char not in "\n\r\t\u200c\u200d" for char in value):
        raise ValueError("Unsupported character")
    return value


Description = Annotated[str, StringConstraints(min_length=1, max_length=4000), BeforeValidator(normalize_text)]
SafetyItem = Annotated[str, StringConstraints(min_length=1, max_length=120), BeforeValidator(normalize_text)]
Choice = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
SafetyItems = Annotated[list[SafetyItem], Field(max_length=16)]
Answer = bool | Choice | Annotated[list[Choice], Field(min_length=1, max_length=8)]


class AssessmentDetails(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    language: Literal["en", "ur", "ps"] | None = None
    severity: Literal["mild", "moderate", "severe"]
    duration_hours: int | None = Field(default=None, ge=1, le=8760)
    more_than_one_year: bool = False
    age: int = Field(ge=0, le=120)
    sex: Literal["female", "male", "intersex", "prefer_not_to_say"] | None = None
    allergies: SafetyItems = Field(default_factory=list)
    conditions: SafetyItems = Field(default_factory=list)
    current_medicines: SafetyItems = Field(default_factory=list)

    @model_validator(mode="after")
    def check_duration(self):
        hours = self.duration_hours
        if (hours is None) != self.more_than_one_year:
            raise ValueError("Provide exact hours or select more than one year")
        if hours is not None and hours not in (1, 2, 12) and hours % 24 != 0:
            raise ValueError("Duration must match a preset or a whole number of days")
        return self


class AssessmentInput(AssessmentDetails):
    text: Description
    answers: dict[Choice, Answer] = Field(default_factory=dict, max_length=5)


class AssessmentDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    id: UUID
    created_at: AwareDatetime
    status: Literal["draft"] = "draft"
    assessment: AssessmentInput


class DraftUnavailable(ValueError):
    pass


class AssessmentValidationError(ValueError):
    def __init__(self, fields: tuple[str, ...]):
        super().__init__("Assessment input is invalid")
        self.fields = fields


def validate_assessment(payload: object) -> AssessmentInput:
    """Expose only known field names, including for nested validation failures."""
    try:
        return AssessmentInput.model_validate(payload)
    except ValidationError as error:
        details = error.errors(include_input=False, include_context=False, include_url=False)
        known = AssessmentInput.model_fields
        fields = {item["loc"][0] if item["loc"] and item["loc"][0] in known else "assessment"
                  for item in details}
        raise AssessmentValidationError(tuple(sorted(fields))) from None


class RetryConflict(ValueError):
    pass


def require_disposable(*engines: Engine, settings: Settings) -> None:
    if (
        settings.environment != "test" or not settings.synthetic_fixtures
        or any(engine.url.database != "roghyar_phase0_ci"
               or engine.url.host not in {"127.0.0.1", "::1", "localhost"} for engine in engines)
    ):
        raise DraftUnavailable("Assessment persistence is unavailable")


class AssessmentService:
    def __init__(self, reader: Engine, writer: Engine, settings: Settings):
        require_disposable(reader, writer, settings=settings)
        self.reader = reader
        self.writer = writer
        self.verifier = CredentialVerifier(settings.session_signing_key)
        self.encryption = FieldEncryption.from_secrets(settings.field_encryption_key, settings.phone_lookup_key)

    def _decode(self, row) -> AssessmentDraft:
        record = row.id
        try:
            values = self.encryption.decrypt(row.safety_details_ciphertext, record_id=record,
                                             field_name="safety_details")
            data = AssessmentDetails.model_validate_json(values)
            description = self.encryption.decrypt(row.text_ciphertext, record_id=record, field_name="text")
            answers = self.encryption.decrypt(row.answers_ciphertext, record_id=record, field_name="answers")
            data = AssessmentInput.model_validate({**data.model_dump(), "text": description,
                                                   "answers": json.loads(answers)})
        except (ValueError, TypeError, AttributeError):
            raise DraftUnavailable("Assessment draft is unavailable") from None
        return AssessmentDraft(id=record, created_at=row.created_at, assessment=data)

    def _find(self, session, record: UUID):
        return session.execute(text("""
            SELECT id, created_at, text_ciphertext, answers_ciphertext, safety_details_ciphertext
            FROM roghyar.assessments WHERE id = :id AND anonymized_at IS NULL
        """), {"id": record}).one_or_none()

    def create(self, draft: AssessmentInput, credential: str | None, retry_key: UUID) -> AssessmentDraft:
        with assessment_write_session(self.writer, self.verifier, credential) as (session, principal):
            record = uuid5(ASSESSMENT_NAMESPACE, f"{principal.kind}:{principal.subject}:{retry_key}")
            patient_id = None
            if principal.kind == "patient":
                patient_id = session.scalar(text("""
                    SELECT id FROM roghyar.patients WHERE auth_subject = :subject AND phone_verified_at IS NOT NULL
                """), {"subject": principal.subject})
                if patient_id is None:
                    raise AuthorizationDenied("Session authorization denied")
            def encrypt(value: str, name: str) -> bytes:
                return self.encryption.encrypt(value, record_id=record, field_name=name)

            session.execute(text("""
                INSERT INTO roghyar.assessments (
                    id, patient_id, session_id, text_ciphertext, answers_ciphertext, safety_details_ciphertext
                ) VALUES (:id, :patient, :owner_session, :description, :answers, :safety)
                ON CONFLICT (id) DO NOTHING
            """), {
                "id": record, "patient": patient_id,
                "owner_session": principal.subject if principal.kind == "anonymous" else None,
                "description": encrypt(draft.text, "text"),
                "answers": encrypt(json.dumps(draft.answers, ensure_ascii=False), "answers"),
                "safety": encrypt(draft.model_dump_json(exclude={"text", "answers"}), "safety_details"),
            })
            row = self._find(session, record)
            if row is None:
                raise DraftUnavailable("Assessment draft is unavailable")
            result = self._decode(row)
            if result.assessment != draft:
                raise RetryConflict("Retry key is already in use")
        return result

    def retrieve(self, record: UUID, credential: str | None) -> AssessmentDraft | None:
        principal = self.verifier.verify(credential)
        if principal.subject is None or principal.kind not in ("anonymous", "patient"):
            raise AuthorizationDenied("Session authorization denied")
        with scoped_session(self.reader, self.verifier, credential) as session:
            row = self._find(session, record)
            return None if row is None else self._decode(row)
