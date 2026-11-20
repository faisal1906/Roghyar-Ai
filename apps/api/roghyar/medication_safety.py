"""Private GD-03 screening. Results never carry product or dosing text."""

import hashlib
from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from pydantic import AwareDatetime, Field, StringConstraints

from .assessment import AssessmentInput, validate_assessment
from .classification import Language, StrictContract
from .conversation import ConversationState
from .evidence_authority import EvidenceAttestation, EvidenceIssuer
from .evidence_custody import InMemoryEvidenceCustody
from .scoped_session import Principal
from .formulary_safety import (
    Dose, FormularyReviewer, InvalidAttestation, SafetyContext, Vocabulary,
    screen_verified_formulary, signature_message, verify_candidate,
)
from .guidance import GuidanceFlowService, GuidanceLevel, LowerApproval
from .red_flags import Digest, Identifier, canonical
from .reply_writer import ReviewerAuthority

DOMAIN = b"roghyar:medication-use:v1\x00"
Completeness = Literal["unknown", "not_asked", "incomplete", "reported_complete",
                       "verified_complete", "contradictory", "unsupported"]


class EvidenceField(StrictContract):
    status: Completeness
    source: Literal["patient", "clinical", "unknown"]
    event: UUID | None = None
    answered: bool = False
    superseded: bool = False
    attestation: EvidenceAttestation | None = Field(default=None, repr=False)


class MedicationEvidence(StrictContract):
    values: SafetyContext = Field(repr=False)
    vocabulary: Identifier
    revision: Annotated[int, Field(ge=0, le=20)]
    age: EvidenceField
    pregnancy: EvidenceField
    allergies: EvidenceField
    conditions: EvidenceField
    current_medicines: EvidenceField

    def missing(self, verified: tuple[str, ...]) -> str | None:
        for name in ("age", "pregnancy", "allergies", "conditions", "current_medicines"):
            field = getattr(self, name)
            if (getattr(self.values, name) is None or not field.answered or not field.event or field.superseded
                    or (field.status, field.source) not in (
                        ("reported_complete", "patient"), ("verified_complete", "clinical"),
                    ) or (name in verified and field.status != "verified_complete")
                    or (name == "pregnancy" and self.values.pregnancy == "not_applicable"
                        and field.status != "verified_complete")):
                return name
        return None


class ProductIngredients(StrictContract):
    product: Identifier
    ingredients: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]


class MedicationPolicy(StrictContract):
    schema_version: Literal[1]
    policy: Identifier
    version: Annotated[int, Field(ge=1)]
    reviewer: Identifier
    key_id: Identifier
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    condition: Identifier
    product: Identifier
    formulary_digest: Digest
    guidance_digest: Digest
    evidence_digest: Digest
    vocabulary: Identifier
    level: Literal["HOME CARE"]
    verified_fields: Annotated[tuple[Literal["age", "pregnancy", "allergies", "conditions", "current_medicines"], ...],
                              Field(max_length=5)] = ()
    products: Annotated[tuple[ProductIngredients, ...], Field(min_length=1, max_length=64)]
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")] = Field(repr=False)

    def signing_bytes(self) -> bytes:
        return DOMAIN + canonical(self.model_dump(mode="json", exclude={"signature"}))


class MedicationContext(StrictContract):
    owner: Principal
    state: ConversationState
    revision: Annotated[int, Field(ge=0, le=20)]
    now: AwareDatetime
    assessment: AssessmentInput = Field(repr=False)
    event_id: UUID
    locale: Language
    approval: LowerApproval | None = Field(default=None, repr=False)

    def guidance_arguments(self):
        return dict(owner=self.owner, state=self.state, revision=self.revision, now=self.now,
                    assessment=validate_assessment(self.assessment.model_dump()).model_dump(),
                    event_id=self.event_id, locale=self.locale, approval=self.approval)


class MedicationResult(StrictContract):
    reason: Literal[
        "invalid_context", "unauthorized_guidance", "emergency_or_urgent", "unresolved_risk",
        "medication_policy_unavailable", "invalid_policy", "revoked_authority", "binding_mismatch",
        "incomplete_evidence", "unknown_pregnancy_status", "unknown_allergy_status",
        "incomplete_medication_history", "invalid_vocabulary", "invalid_formulary", "known_contraindication",
        "interaction_coverage_missing", "duplicate_ingredient_risk", "age_not_covered",
        "pediatric_dose_unsupported", "synthetic_eligible", "evaluation_failure",
    ]


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class MedicationSafetyService:
    def __init__(self, guidance: GuidanceFlowService, vocabulary: Vocabulary, *,
                 vocabulary_version: str, formulary_reviewers: dict[str, FormularyReviewer] | None = None,
                 policy_reviewers: dict[str, ReviewerAuthority] | None = None,
                 active_policies: frozenset[tuple[str, int]] = frozenset(),
                 evidence_issuers: dict[str, EvidenceIssuer] | None = None,
                 evidence_custody: InMemoryEvidenceCustody | None = None):
        self._guidance = guidance
        self._vocabulary = vocabulary
        self._version = vocabulary_version
        self._formulary_reviewers = dict(formulary_reviewers or {})
        self._policy_reviewers = dict(policy_reviewers or {})
        self._active = frozenset(active_policies)
        self._evidence_issuers = dict(evidence_issuers or {})
        self._custody = evidence_custody

    async def screen(self, *, guidance_context: MedicationContext, evidence: object, policy: object = None,
                     formulary: object = None) -> MedicationResult:
        """Accept only server-custodied inputs. No production selection or dose channel."""
        if not isinstance(guidance_context, MedicationContext):
            return MedicationResult(reason="invalid_context")
        try:
            return MedicationResult(reason=await self._reason(guidance_context.guidance_arguments(), evidence, policy, formulary))
        except Exception:
            return MedicationResult(reason="evaluation_failure")

    async def _reason(self, context, evidence, policy, formulary):
        guidance = await self._guidance.evaluate(**context)
        if guidance.status == "invalid_context":
            return "invalid_context"
        if guidance.floor is not None:
            return "unresolved_risk" if guidance.requires_assessment else "emergency_or_urgent"
        if guidance.status != "authorized_lower" or guidance.level != GuidanceLevel.HOME_CARE:
            return "unauthorized_guidance"
        if policy is None or not self._active:
            return "medication_policy_unavailable"
        try:
            policy = MedicationPolicy.model_validate(policy.model_dump() if isinstance(policy, MedicationPolicy) else policy)
            now = context["now"]
            authority = self._policy_reviewers.get(policy.key_id)
            if authority is not None and authority.revoked:
                return "revoked_authority"
            if (authority is None or authority.reviewer != policy.reviewer or "medication_policy" not in authority.scopes
                    or (policy.policy, policy.version) not in self._active
                    or now.utcoffset() != timedelta(0)
                    or not authority.valid_from <= policy.reviewed_at <= now < min(authority.valid_until, policy.expires_at)):
                return "invalid_policy"
            authority.key.verify(bytes.fromhex(policy.signature), policy.signing_bytes())
        except (InvalidSignature, ValueError):
            return "invalid_policy"
        try:
            evidence = MedicationEvidence.model_validate(evidence.model_dump() if isinstance(evidence, MedicationEvidence) else evidence)
        except ValueError:
            return "incomplete_evidence"
        if evidence.vocabulary != self._version or policy.vocabulary != self._version:
            return "invalid_vocabulary"
        approval = context["approval"]
        approval = LowerApproval.model_validate(approval.model_dump() if isinstance(approval, LowerApproval) else approval)
        if (evidence.revision != context["revision"] or policy.guidance_digest != digest(approval.signing_bytes())
                or policy.evidence_digest != digest(canonical(evidence.model_dump(mode="json")))):
            return "binding_mismatch"
        if self._custody is not None:
            snapshot = self._custody.current(context["owner"], context["state"].conversation_id)
            if snapshot is None or not snapshot.matches(context["state"], evidence, now):
                return "binding_mismatch"
        current_events = set(context["state"].events) | {context["event_id"]}
        if any(field.event not in current_events for field in
               (evidence.age, evidence.pregnancy, evidence.allergies, evidence.conditions, evidence.current_medicines)
               if field.event is not None):
            return "binding_mismatch"
        if evidence.values.age is not None and evidence.values.age != context["assessment"]["age"]:
            return "binding_mismatch"
        missing = evidence.missing(policy.verified_fields)
        if missing:
            return {"pregnancy": "unknown_pregnancy_status", "allergies": "unknown_allergy_status",
                    "current_medicines": "incomplete_medication_history"}.get(missing, "incomplete_evidence")
        owner = context["owner"]
        for name in ("age", "pregnancy", "allergies", "conditions", "current_medicines"):
            field = getattr(evidence, name)
            if field.status != "verified_complete":
                continue
            proof = field.attestation
            if proof is None or (proof.owner, proof.owner_kind, proof.conversation, proof.event, proof.revision,
                    proof.field, proof.value_digest, proof.vocabulary) != (
                    owner.subject, owner.kind, context["state"].conversation_id, field.event, evidence.revision,
                    name, digest(canonical(getattr(evidence.values, name))), evidence.vocabulary
            ) or not proof.verified_by(self._evidence_issuers.get(proof.key_id), now):
                return "incomplete_evidence"
        try:
            if not isinstance(formulary, dict) or formulary.get("active") is not True or formulary.get("review_status") != "approved":
                return "invalid_formulary"
            row = verify_candidate(formulary, self._formulary_reviewers, self._vocabulary, now)
        except (InvalidAttestation, ValueError, TypeError):
            return "invalid_formulary"
        if (row.condition_code, row.medicine, digest(signature_message(row))) != (
                policy.condition, policy.product, policy.formulary_digest):
            return "binding_mismatch"
        values = evidence.values
        if (not set(values.allergies) <= self._vocabulary.allergens
                or not set(values.conditions) <= self._vocabulary.conditions
                or not set(values.current_medicines) <= self._vocabulary.medicines):
            return "invalid_vocabulary"
        screened = screen_verified_formulary(row, values, self._vocabulary)
        if screened == "contraindicated":
            return "known_contraindication"
        if len(set(values.current_medicines)) != len(values.current_medicines):
            return "duplicate_ingredient_risk"
        ingredients = {entry.product: set(entry.ingredients) for entry in policy.products}
        products = {row.medicine, *values.current_medicines}
        if len(ingredients) != len(policy.products) or not products <= ingredients.keys():
            return "interaction_coverage_missing"
        if any(ingredients[row.medicine] & ingredients[other] for other in values.current_medicines):
            return "duplicate_ingredient_risk"
        if len(values.current_medicines) > 1:
            return "interaction_coverage_missing"
        if values.age == 0:
            return "pediatric_dose_unsupported"
        if isinstance(screened, Dose):
            # The final locked read is the eligibility decision's linearization point.
            if self._custody is not None and self._custody.current(
                    context["owner"], context["state"].conversation_id) is not snapshot:
                return "binding_mismatch"
            return "synthetic_eligible"
        return {"interactions_unverified": "interaction_coverage_missing",
                "age_or_pregnancy_mismatch": "age_not_covered",
                "unsupported_pediatric_dose": "pediatric_dose_unsupported"}.get(screened, "incomplete_evidence")
