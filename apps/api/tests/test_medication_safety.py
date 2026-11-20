"""SYNTHETIC—DEVELOPMENT ONLY. Isolated status-only screening verification."""
import asyncio
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from statistics import median
from time import perf_counter_ns
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from roghyar.assessment import validate_assessment
from roghyar.conversation import ConversationMemoryService
from roghyar.formulary_safety import FormularyRecord, SafetyContext, signature_message
from roghyar.guidance import GuidanceFlowService, GuidanceLevel, GuidanceResult, LowerApproval
from roghyar.medication_safety import (
    EvidenceField, MedicationContext, MedicationEvidence, MedicationPolicy, MedicationSafetyService,
    ProductIngredients, digest,
)
from roghyar.red_flags import canonical
from roghyar.reply_writer import ReviewerAuthority
from roghyar.scoped_session import Principal
from test_formulary_safety import ALLERGY, BLOCKED, COMPATIBLE, CONDITION, CONTRA, MEDICINE, VOCABULARY, sign_formulary


@pytest.mark.parametrize("mode,reason", [
    ("valid", "synthetic_eligible"), ("closed", "medication_policy_unavailable"),
    ("no_guidance", "unauthorized_guidance"), ("emergency", "emergency_or_urgent"),
    ("urgent", "emergency_or_urgent"), ("unresolved", "unresolved_risk"),
    ("expired", "invalid_context"), ("owner", "invalid_context"), ("revision", "invalid_context"),
    ("pregnancy", "unknown_pregnancy_status"), ("applicability", "unknown_pregnancy_status"),
    ("allergy_absent", "unknown_allergy_status"), ("allergy_default", "unknown_allergy_status"),
    ("allergy", "known_contraindication"), ("condition", "known_contraindication"),
    ("blocked", "known_contraindication"), ("medication_default", "incomplete_medication_history"),
    ("missing_ingredients", "interaction_coverage_missing"), ("duplicate", "duplicate_ingredient_risk"),
    ("compatible", "synthetic_eligible"), ("alias", "invalid_vocabulary"), ("forged_source", "binding_mismatch"),
    ("claimed_verified", "unknown_allergy_status"), ("superseded", "unknown_allergy_status"),
    ("tamper", "invalid_formulary"), ("wrong_domain", "invalid_policy"), ("revoked", "revoked_authority"),
    ("changed_policy", "invalid_policy"), ("changed_approval", "binding_mismatch"),
    ("wrong_product", "binding_mismatch"), ("new_vocabulary", "invalid_vocabulary"),
    ("outage", "evaluation_failure"), ("age", "age_not_covered"), ("child", "synthetic_eligible"),
    ("gap", "pediatric_dose_unsupported"),
    ("infant", "pediatric_dose_unsupported"), ("age_one", "age_not_covered"), ("minor", "age_not_covered"),
    ("adult_min", "synthetic_eligible"), ("adult_max", "synthetic_eligible"), ("elder", "age_not_covered"),
    ("pregnant", "age_not_covered"), ("contradictory", "unknown_pregnancy_status"),
    ("verified_pregnancy", "incomplete_evidence"), ("unknown_history", "incomplete_medication_history"),
])
def test_medication_authorities(mode, reason, sign_formulary):
    now = datetime.now(UTC)
    private = Ed25519PrivateKey.generate()
    reviewer = ReviewerAuthority("synthetic", private.public_key(), frozenset({"medication_policy"}),
                                 now - timedelta(days=1), now + timedelta(days=1), revoked=mode == "revoked")
    owner = Principal("patient", uuid4())
    state = ConversationMemoryService.start(owner, now)
    event = uuid4()
    age = {"age": 66, "child": 5, "gap": 4, "infant": 0, "age_one": 1, "minor": 17,
           "adult_min": 18, "adult_max": 65, "elder": 120}.get(mode, 30)
    assessment = validate_assessment(dict(text="Synthetic input", age=age, duration_hours=1, severity="mild"))
    approval = LowerApproval(schema_version=1, policy="synthetic", version=1, reviewer="synthetic",
        key_id="synthetic", reviewed_at=now, expires_at=state.expires_at, level=GuidanceLevel.HOME_CARE,
        conversation=state.conversation_id, event=event, revision=0, vocabulary_version=1, snapshot="a"*64,
        evidence_digest="b"*64, screening_review="synthetic", content_id="synthetic", content_version=1,
        content_digest="c"*64, signature="0"*128)

    class GuidanceDouble(GuidanceFlowService):
        def __init__(self):
            pass

        async def evaluate(self, **context):
            if mode == "outage":
                raise RuntimeError("synthetic")
            if mode in ("expired", "owner", "revision"):
                return GuidanceResult(status="invalid_context")
            if mode in ("emergency", "urgent", "unresolved"):
                return GuidanceResult(status="reviewed_emergency" if mode == "emergency" else "reviewed_urgent",
                    floor=GuidanceLevel.EMERGENCY if mode == "emergency" else GuidanceLevel.URGENT,
                    requires_assessment=mode == "unresolved")
            if mode == "no_guidance":
                return GuidanceResult(status="no_lower_policy")
            return GuidanceResult(status="authorized_lower", level=GuidanceLevel.HOME_CARE)

    field = EvidenceField(status="reported_complete", source="patient", event=event, answered=True)
    values = SafetyContext(age=age, pregnancy=None if mode == "pregnancy" else
                           "not_applicable" if mode == "applicability" else "pregnant" if mode == "pregnant" else "not_pregnant",
                           allergies=None if mode == "allergy_absent" else [ALLERGY] if mode == "allergy" else [],
                           conditions=[CONTRA] if mode == "condition" else [],
                           current_medicines=[BLOCKED] if mode == "blocked" else ["unknown"] if mode == "alias" else
                           [COMPATIBLE] if mode in ("compatible", "duplicate", "missing_ingredients") else [])
    evidence = MedicationEvidence(values=values, vocabulary="other" if mode == "new_vocabulary" else "synthetic",
        revision=0, age=field, pregnancy=field, allergies=field, conditions=field, current_medicines=field)
    changes = {
        "contradictory": {"pregnancy": field.model_copy(update={"status": "contradictory"})},
        "verified_pregnancy": {"pregnancy": field.model_copy(update={"status": "verified_complete", "source": "clinical"})},
        "unknown_history": {"current_medicines": field.model_copy(update={"status": "incomplete"})},
        "allergy_default": {"allergies": EvidenceField(status="not_asked", source="unknown")},
        "medication_default": {"current_medicines": EvidenceField(status="unknown", source="unknown")},
        "claimed_verified": {"allergies": field.model_copy(update={"status": "verified_complete"})},
        "superseded": {"allergies": field.model_copy(update={"superseded": True})},
        "forged_source": {"allergies": field.model_copy(update={"event": uuid4()})},
    }
    evidence = evidence.model_copy(update=changes.get(mode, {}))
    create_formulary, formulary_reviewers = sign_formulary
    row = create_formulary(children=mode in ("child", "gap"))
    products = (ProductIngredients(product=MEDICINE, ingredients=("synthetic_ingredient",)),)
    if mode != "missing_ingredients":
        products += (ProductIngredients(product=COMPATIBLE, ingredients=(
            "synthetic_ingredient" if mode == "duplicate" else "synthetic_other",)),)
    policy = MedicationPolicy(schema_version=1, policy="synthetic", version=1, reviewer="synthetic",
        key_id="synthetic", reviewed_at=now, expires_at=state.expires_at, condition=CONDITION,
        product="other" if mode == "wrong_product" else MEDICINE,
        formulary_digest=digest(signature_message(FormularyRecord.model_validate(row))),
        guidance_digest=digest(approval.signing_bytes()), evidence_digest=digest(canonical(evidence.model_dump(mode="json"))),
        vocabulary="synthetic", level="HOME CARE", products=products, signature="0"*128)
    signed = (b"wrong" if mode == "wrong_domain" else b"") + policy.signing_bytes()
    policy = policy.model_copy(update={"signature": private.sign(signed).hex()})
    if mode == "changed_policy":
        policy = policy.model_copy(update={"condition": "other"})
    if mode == "changed_approval":
        approval = approval.model_copy(update={"evidence_digest": "e"*64})
    if mode == "tamper":
        row["adult_dose"] = row["reviewed_payload"]["adult"]["instructions"] = "synthetic changed"
    context = MedicationContext(owner=Principal("patient", uuid4()) if mode == "owner" else owner,
        state=state, revision=1 if mode == "revision" else 0, now=state.expires_at if mode == "expired" else now,
        assessment=assessment, event_id=event, locale="en", approval=approval)
    service = MedicationSafetyService(GuidanceDouble(), VOCABULARY, vocabulary_version="synthetic",
        formulary_reviewers=formulary_reviewers, policy_reviewers={"synthetic": reviewer},
        active_policies=frozenset() if mode == "closed" else frozenset({("synthetic", 1)}))

    async def run():
        return await service.screen(guidance_context=context, evidence=evidence, policy=policy, formulary=row)

    assert asyncio.run(run()).model_dump() == {"reason": reason}
    if mode in ("valid", "closed"):
        samples = []
        for _ in range(105):
            start = perf_counter_ns()
            asyncio.run(run())
            samples.append((perf_counter_ns() - start) / 1e6)
        values = sorted(samples[5:])
        print(f"GD-03 synthetic benchmark {mode} n=100 p50={median(values):.4f} ms p95={values[94]:.4f} ms")
