"""Private exact-text rendering from independently authorized content."""

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from .classification import ClassificationResult, Classified, Language, StrictContract, Uncertain
from .conversation import ConversationState, Question
from .red_flags import Digest, Identifier, canonical
from .scoped_session import Principal
from .structured_triage import Outcome
from .triage import TriageService, TriageState, WarningSelection

Intent = Literal[
    "greeting", "off_topic", "clarification", "classification_unavailable", "follow_up",
    "navigation_unverified", "safety_review", "urgent", "emergency", "clinical_unavailable", "restricted",
]
Scope = Literal["product", "clinical"]
STATES = {
    TriageState.REVIEWED_EMERGENCY: "emergency",
    TriageState.REVIEWED_URGENT: "urgent",
    TriageState.URGENT_REVIEW_REQUIRED: "safety_review",
    TriageState.FURTHER_ASSESSMENT_REQUIRED: "safety_review",
    TriageState.RULES_UNAVAILABLE: "clinical_unavailable",
    TriageState.EVALUATION_FAILURE: "clinical_unavailable",
}
CLINICAL = frozenset({"follow_up", "safety_review", "urgent", "emergency", "clinical_unavailable"})
DOMAIN = b"roghyar:reply-content:v1\x00"
RENDERABLE = frozenset({
    "greeting", "off_topic", "clarification", "classification_unavailable", "follow_up",
    "navigation_unverified", "safety_review", "clinical_unavailable",
})


class Translation(StrictContract):
    language: Language
    text: Annotated[str, StringConstraints(min_length=1, max_length=1200)] = Field(repr=False)
    review_ref: Identifier
    options: Annotated[tuple[Identifier, ...], Field(max_length=8)] = ()

    @field_validator("text")
    @classmethod
    def safe_plain_text(cls, text: str) -> str:
        if (not text.strip() or any(c in "{}<>" for c in text) or any(
            unicodedata.category(c).startswith("C") and c not in "\n\u200c\u200d" for c in text
        )):
            raise ValueError("Unsupported content")
        return text


class ArtifactBody(StrictContract):
    schema_version: Literal[1]
    content_id: Identifier
    version: Annotated[int, Field(ge=1, le=2147483647)]
    intent: Intent
    reviewer: Identifier
    key_id: Identifier
    reviewed_at: AwareDatetime
    review_status: Literal["approved"]
    active: bool
    translations: Annotated[tuple[Translation, ...], Field(min_length=1, max_length=3)]
    permitted_states: Annotated[tuple[TriageState, ...], Field(max_length=7)] = ()
    question: Identifier | None = None

    @model_validator(mode="after")
    def binding(self) -> Self:
        if len({v.language for v in self.translations}) != len(self.translations):
            raise ValueError("Duplicate locale")
        if self.intent not in RENDERABLE or (self.intent == "follow_up") != (self.question is not None):
            raise ValueError("Unsupported catalog binding")
        if any(bool(v.options) != (self.question is not None) or len(v.options) != len(set(v.options))
               for v in self.translations):
            raise ValueError("Invalid question options")
        return self

    def signing_bytes(self) -> bytes:
        return DOMAIN + canonical(self.model_dump(mode="json"))


class SignedArtifact(StrictContract):
    body: ArtifactBody
    digest: Digest
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]


@dataclass(frozen=True, slots=True)
class ReviewerAuthority:
    reviewer: str
    key: Ed25519PublicKey
    scopes: frozenset[Scope]
    valid_from: datetime
    valid_until: datetime
    revoked: bool = False


class Reply(StrictContract):
    status: Literal["rendered", "unavailable", "restricted"]
    intent: Intent
    reason: Literal[
        "verified", "missing_content", "invalid_signature", "digest_mismatch", "invalid_authority",
        "missing_locale", "invalid_locale", "invalid_conversation", "missing_question",
        "warning_unavailable", "restricted_request", "internal_failure",
    ]
    language: Language | None = None
    text: str | None = Field(default=None, repr=False, max_length=2000)
    content_id: Identifier | None = None
    content_version: int | None = Field(default=None, ge=1)
    content_digest: Digest | None = None
    options: Annotated[tuple[Identifier, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def output_shape(self) -> Self:
        fields = (self.language, self.text, self.content_id, self.content_version, self.content_digest)
        if self.status == "rendered":
            if self.reason != "verified" or any(value is None for value in fields):
                raise ValueError("Unbound content")
        elif any(value is not None for value in fields) or self.options or self.reason == "verified":
            raise ValueError("Unavailable result carries content")
        return self


class ReplyWriterService:
    def __init__(self, *, catalog: tuple[SignedArtifact, ...] = (),
                 authorities: dict[str, ReviewerAuthority] | None = None,
                 questions: tuple[Question, ...] = (), triage: TriageService | None = None):
        if len(catalog) > 128 or len(questions) > 32:
            raise ValueError("Reply configuration exceeds limits")
        self._catalog = tuple(SignedArtifact.model_validate(item.model_dump()) for item in catalog)
        self._authorities = dict(authorities or {})
        self._questions = {q.key: Question.model_validate(q.model_dump()) for q in questions}
        if len(self._questions) != len(questions):
            raise ValueError("Duplicate question")
        self._triage = triage

    def render(self, *, classification: ClassificationResult, outcome: Outcome | None = None,
               locale: Language | None = None, now: datetime, state: ConversationState | None = None,
               owner: Principal | None = None, revision: int | None = None) -> Reply:
        """Only trusted server composition supplies classification, outcome and current state."""
        decision = outcome.decision if outcome is not None else None
        intent: Intent = "clinical_unavailable"
        try:
            safety = decision.state if decision is not None else None
            if safety in STATES:
                intent = STATES[safety]
            elif isinstance(classification, Classified) and classification.relevance_category == "unsafe_request":
                intent = "restricted"
            elif outcome is not None:
                # Extraction, condition, specialty and summary never authorize a reply.
                intent = "safety_review"
            elif isinstance(classification, Classified):
                intent = {
                    "unsafe_request": "restricted", "off_topic": "off_topic", "greeting": "greeting",
                    "doctor_or_hospital": "navigation_unverified", "booking_or_app_help": "navigation_unverified",
                }.get(classification.relevance_category, "safety_review")
            elif isinstance(classification, Uncertain):
                intent = "clarification"
            else:
                intent = "classification_unavailable"

            if state is not None or owner is not None or revision is not None:
                if state is None or owner is None:
                    return Reply(status="unavailable", intent=intent, reason="invalid_conversation")
                state = ConversationState.model_validate(state.model_dump())
                if ((owner.kind, owner.subject) != (state.owner_kind, state.owner_subject)
                        or type(revision) is not int or revision != len(state.events)
                        or not state.created_at <= now < state.expires_at
                        or state.phase in ("expired", "cancelled", "completed")):
                    return Reply(status="unavailable", intent=intent, reason="invalid_conversation")
                if state.routing == "restricted" and safety not in STATES:
                    intent = "restricted"
            if intent == "restricted":
                return Reply(status="restricted", intent=intent, reason="restricted_request")
            question = None
            if state is not None and state.pending is not None and outcome is None and intent in (
                "greeting", "off_topic", "clarification",
            ):
                intent = "follow_up"
                question = self._questions.get(state.pending)
                if question is None:
                    return Reply(status="unavailable", intent=intent, reason="missing_question")
            if locale is None and isinstance(classification, Classified) and len(classification.languages) == 1:
                locale = classification.languages[0]
            if locale not in ("en", "ur", "ps"):
                return Reply(status="unavailable", intent=intent, reason="invalid_locale")
            if intent in ("emergency", "urgent"):
                return self._warning(outcome, intent, locale)
            return self._render_catalog(intent, locale, safety, question, now)
        except Exception:
            return Reply(status="unavailable", intent=intent, reason="internal_failure")

    def _warning(self, outcome: Outcome, intent: Intent, locale: Language) -> Reply:
        decision = outcome.decision
        if self._triage is not None:
            evidence = {"observations": [report.observation.model_dump() for report in outcome.reports]}
            for match in decision.matches:
                if match.priority != intent:
                    continue
                selection = WarningSelection(fingerprint=decision.fingerprint, rule_code=match.rule_code,
                                             version=match.version, language=locale)
                text = self._triage.resolve_warning(evidence, selection)
                if text is not None:
                    Translation.safe_plain_text(text)
                    return Reply(status="rendered", intent=intent, reason="verified", language=locale,
                                 text=text, content_id=match.warning_ref, content_version=match.version,
                                 content_digest=match.warning_sha256)
        return Reply(status="unavailable", intent=intent, reason="warning_unavailable")

    def _render_catalog(self, intent: Intent, locale: Language, safety: TriageState | None,
                        question: Question | None, now: datetime) -> Reply:
        matches = tuple(item for item in self._catalog if item.body.active and item.body.intent == intent
                        and item.body.question == (question.key if question else None)
                        and (safety in item.body.permitted_states if safety is not None
                             else not item.body.permitted_states))
        if len(matches) != 1:
            return Reply(status="unavailable", intent=intent, reason="missing_content")
        item = matches[0]
        body = item.body
        data = body.signing_bytes()
        if hashlib.sha256(data).hexdigest() != item.digest:
            return Reply(status="unavailable", intent=intent, reason="digest_mismatch")
        authority = self._authorities.get(body.key_id)
        scope = "clinical" if intent in CLINICAL else "product"
        if (authority is None or authority.revoked or authority.reviewer != body.reviewer
                or scope not in authority.scopes
                or not authority.valid_from <= body.reviewed_at <= now < authority.valid_until):
            return Reply(status="unavailable", intent=intent, reason="invalid_authority")
        try:
            authority.key.verify(bytes.fromhex(item.signature), data)
        except InvalidSignature:
            return Reply(status="unavailable", intent=intent, reason="invalid_signature")
        variant = next((v for v in body.translations if v.language == locale), None)
        if variant is None:
            return Reply(status="unavailable", intent=intent, reason="missing_locale")
        if question is not None and variant.options != question.options:
            return Reply(status="unavailable", intent=intent, reason="missing_question")
        return Reply(status="rendered", intent=intent, reason="verified", language=locale,
                     text=variant.text, content_id=body.content_id, content_version=body.version,
                     content_digest=item.digest, options=variant.options)
