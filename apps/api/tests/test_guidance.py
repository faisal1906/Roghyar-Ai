"""SYNTHETICâ€”DEVELOPMENT ONLY. No clinical policy or patient data."""
import asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from roghyar.assessment import validate_assessment
from roghyar.formulary_safety import FormularyRecord, SafetyContext, signature_message
from roghyar.medication_safety import EvidenceField, MedicationContext, MedicationEvidence, MedicationPolicy, MedicationSafetyService, ProductIngredients, digest
from test_formulary_safety import CONDITION, MEDICINE, VOCABULARY, sign_formulary
import hashlib
from datetime import UTC, datetime, timedelta
from statistics import median
from time import perf_counter_ns
from uuid import uuid4

import pytest
from test_clinical_publishing import published_chain_content, database, publisher, author_login, reviewed

from roghyar.conversation import ConversationMemoryService, Question
from roghyar.guidance import GuidanceFlowService, GuidanceLevel, LowerApproval
from roghyar.red_flags import canonical
from roghyar.reply_writer import ReplyWriterService, Translation
from roghyar.scoped_session import Principal
from roghyar.structured_triage import Definition, Mapping, Vocabulary
from roghyar.triage import TriageService, TriageState
from test_red_flags import sign_rule
from test_home_care import home_fixture, selection
from roghyar.home_care import HomeCareContentRepository
from roghyar.guidance import StructuredHomeBinding
from test_reply_writer import signed, NOW
from dataclasses import replace


@pytest.mark.parametrize("mode,expected", [
    ("emergency", "reviewed_emergency"), ("urgent", "reviewed_urgent"),
    ("uncertain", "urgent_review_required"), ("missing", "rules_unavailable"),
    ("tampered", "invalid_rule_configuration"), ("outage", "evaluation_failure"),
    ("no_policy", "no_lower_policy"), ("doctor", "authorized_lower"),
    ("home", "authorized_lower"), ("revoked", "invalid_policy"),
    ("signature", "invalid_policy"), ("snapshot", "policy_mismatch"), ("version", "policy_mismatch"), ("translation", "missing_content"), ("structured", "authorized_lower"),
    ("structured_postgres", "authorized_lower"), ("structured_digest", "missing_content"), ("structured_outage", "missing_content"),
])
def test_signed_guidance_flow(mode, expected, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request):
    run_signed_chain(mode, expected, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request)


def run_signed_chain(mode, expected, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request, case=None):
    sign, keys = sign_rule
    owner = Principal("patient", uuid4())
    question = Question(key="sample_question", options=("yes", "no"))
    memory = ConversationMemoryService(reviewed_questions=(question,))
    state = memory.start(owner, NOW)
    def apply(state, action, **fields):
        return asyncio.run(memory.apply(state, dict(action=action, conversation_id=state.conversation_id,
                           revision=len(state.events), event_id=uuid4(), question=question.key, **fields), owner, NOW)).state
    state = apply(apply(state, "ask"), "answer", option="yes")
    truth = mode in ("emergency", "urgent", "uncertain")
    vocabulary = Vocabulary(version=1, review_ref="synthetic", facts=(
        Definition(fact="synthetic_first", kind="boolean", meaning="Disposable sample"),),
        mappings=(Mapping(question=question.key, option="yes", fact="synthetic_first", value=truth),))
    rule = sign(priority="urgent" if mode in ("urgent", "uncertain") else "emergency")
    rows = [rule]
    if mode == "uncertain":
        rows.append(sign(code="synthetic_other", include={"op": "eq", "fact": "synthetic_unknown", "value": True}))
    if mode == "tampered":
        rule.pattern["attestation"]["signature"] = "0" * 128
    def repository(_):
        if mode == "outage":
            raise RuntimeError("Synthetic private detail")
        return [] if mode == "missing" else rows
    monkeypatch.setattr("roghyar.red_flags.PostgresRuleRepository.__call__", repository)
    triage = TriageService(None, keys)
    private, authority, artifact = signed
    authority = replace(authority, scopes=frozenset({"clinical"}))
    intent = "see_doctor" if mode == "doctor" else "home_care"
    item = artifact(intent, permitted_states=(TriageState.NO_REVIEWED_MATCH,),
                    translations=(Translation(language="en", text="Synthetic only", review_ref="synthetic"),))
    writer = ReplyWriterService(catalog=(item,), authorities={"sample_key": authority}, triage=triage)
    _, home_authority, create_home = home_fixture
    bundle = create_home()
    class UnavailableRepository(HomeCareContentRepository):
        def resolve(self, selection):
            raise RuntimeError("Synthetic outage")
    repository = (UnavailableRepository() if mode == "structured_outage" else
                  HomeCareContentRepository((bundle,), authorities={"sample_key": home_authority}))
    if mode == "structured_postgres":
        bundle, repository, publish, replace_active, revoke = request.getfixturevalue("published_chain_content")
        now = datetime.now(UTC)
        writer = ReplyWriterService(catalog=(item,), authorities={"sample_key": replace(
            authority, valid_until=now + timedelta(days=1))}, triage=triage)
    service = GuidanceFlowService(triage, vocabulary, writer=writer,
                                  policy_keys={"sample_key": replace(authority, revoked=mode == "revoked")},
                                  active_policies=frozenset({("synthetic", 1)}), home_care=repository)
    assessment = dict(text="Synthetic-only input", severity="mild", age=30, duration_hours=1)
    event = uuid4()
    fields = dict(owner=owner, state=state, revision=2, now=NOW, assessment=assessment, event_id=event,
                  locale="ur" if mode == "translation" else "en")
    def evaluate(**extra):
        return asyncio.run(service.evaluate(**(fields | extra)))
    assert evaluate(owner=Principal("patient", uuid4())).status == "invalid_context"
    assert evaluate(now=state.expires_at).status == "invalid_context"
    base = evaluate()
    approval = None
    if mode not in ("emergency", "urgent", "uncertain", "missing", "tampered", "outage", "no_policy"):
        assessment_digest = hashlib.sha256(canonical({
            "assessment": service._assessment_data(assessment),
            "owner": {"kind": owner.kind, "subject": str(owner.subject)},
            "reports": [{"observation": {"fact": "synthetic_first", "provenance": "reported", "value": False},
                         "events": [str(state.events[-1])], "supersedes": []}],
        })).hexdigest()
        approval = LowerApproval(schema_version=1, policy="synthetic", version=2 if mode == "version" else 1, reviewer="synthetic",
                                 key_id="sample_key", reviewed_at=NOW, expires_at=NOW + timedelta(minutes=1),
                                 level=GuidanceLevel.SEE_A_DOCTOR if mode == "doctor" else GuidanceLevel.HOME_CARE,
                                 conversation=state.conversation_id, event=event, revision=2, vocabulary_version=1,
                                 snapshot="a"*64 if mode == "snapshot" else base.decision.fingerprint,
                                 evidence_digest=assessment_digest, screening_review="synthetic_screen",
                                 content_id=item.body.content_id, content_version=item.body.version,
                                 content_digest=item.digest, signature="0"*128)
        if mode.startswith("structured"):
            requested = selection(bundle)
            binding = requested.binding
            if mode == "structured_digest":
                binding = binding.model_copy(update={"digest": "0" * 64})
            approval = approval.model_copy(update={"structured_home": StructuredHomeBinding(
                binding=binding, condition=requested.condition, applicability=requested.applicability,
            )})
        if mode != "signature":
            approval = approval.model_copy(update={"signature": private.sign(approval.signing_bytes()).hex()})
    if mode == "structured_postgres":
        assert repository.resolve(selection(bundle, now=datetime.now(UTC))).status == "empty"
    result = evaluate(approval=approval) if mode != "structured_postgres" else None
    if result is not None:
        assert result.status == expected
        assert result.medication_status == "unavailable"
    if truth:
        assert result.floor == (GuidanceLevel.EMERGENCY if mode == "emergency" else GuidanceLevel.URGENT)
        assert result.reply.reason != "verified"
        assert evaluate(approval={"level": "HOME CARE"}).floor == result.floor
    if mode == "structured":
        assert base.status == "no_lower_policy" and base.structured_home is None
        assert result.structured_home.variant.steps == ("Synthetic step A", "Synthetic step B")
    if mode.startswith("structured") and expected != "authorized_lower":
        assert result.reply is None
    if expected == "authorized_lower" and mode != "structured_postgres":
        assert result.level == approval.level and result.reply.text == "Synthetic only"
        assert evaluate(approval=approval, event_id=uuid4()).status == "policy_mismatch"
    if mode in ("structured", "structured_postgres"):
        # Keep the historical guidance fixture separate from the live-clock formulary signature.
        now = datetime.now(UTC)
        refreshed = state.model_copy(update={"created_at": now, "expires_at": now + timedelta(minutes=15)})
        lower = approval.model_copy(update={"reviewed_at": NOW, "expires_at": now + timedelta(minutes=5)})
        lower = lower.model_copy(update={"signature": private.sign(lower.signing_bytes()).hex()})
        fresh_authority = replace(authority, valid_until=now + timedelta(days=1))
        fresh_home = replace(home_authority, valid_until=now + timedelta(days=1))
        fresh_writer = ReplyWriterService(catalog=(item,), authorities={"sample_key": fresh_authority}, triage=triage)
        flow = GuidanceFlowService(triage, vocabulary, writer=fresh_writer,
            policy_keys={"sample_key": fresh_authority}, active_policies=frozenset({("synthetic", 1)}),
            home_care=(repository if mode == "structured_postgres" else
                       HomeCareContentRepository((bundle,), authorities={"sample_key": fresh_home})))
        context = MedicationContext(owner=owner, state=refreshed, revision=2, now=now, event_id=event,
                                    assessment=validate_assessment(assessment), locale="en", approval=lower)
        field = EvidenceField(status="reported_complete", source="patient", event=event, answered=True)
        evidence = MedicationEvidence(values=SafetyContext(age=30, pregnancy="not_pregnant",
            allergies=[], conditions=[], current_medicines=[]), vocabulary="synthetic", revision=2,
            age=field, pregnancy=field, allergies=field, conditions=field, current_medicines=field)
        make_row, formulary_keys = sign_formulary
        row = make_row()
        key = Ed25519PrivateKey.generate()
        policy_authority = replace(fresh_authority, key=key.public_key(), scopes=frozenset({"medication_policy"}))
        policy = MedicationPolicy(schema_version=1, policy="synthetic", version=1, reviewer="synthetic",
            key_id="synthetic", reviewed_at=now, expires_at=lower.expires_at, condition=CONDITION, product=MEDICINE,
            formulary_digest=digest(signature_message(FormularyRecord.model_validate(row))),
            guidance_digest=digest(lower.signing_bytes()), evidence_digest=digest(canonical(evidence.model_dump(mode="json"))),
            vocabulary="synthetic", level="HOME CARE",
            products=(ProductIngredients(product=MEDICINE, ingredients=("synthetic_ingredient",)),), signature="0"*128)
        policy = policy.model_copy(update={"signature": key.sign(policy.signing_bytes()).hex()})
        medication = MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
            formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
            active_policies=frozenset({("synthetic", 1)}))
        def screen(ctx=context, info=evidence, auth=policy, candidate=row):
            return asyncio.run(medication.screen(guidance_context=ctx, evidence=info, policy=auth,
                                                 formulary=candidate)).model_dump()
        if mode == "structured_postgres":
            assert screen() == {"reason": "unauthorized_guidance"}
            publish()
        assert screen() == {"reason": "synthetic_eligible"}
        if mode == "structured_postgres":
            replacement = replace_active()
            assert screen() == {"reason": "unauthorized_guidance"}
            bound = lower.structured_home.model_copy(update={"binding": selection(replacement).binding})
            updated = lower.model_copy(update={"structured_home": bound})
            updated = updated.model_copy(update={"signature": private.sign(updated.signing_bytes()).hex()})
            updated_context = context.model_copy(update={"approval": updated})
            assert screen(ctx=updated_context) == {"reason": "binding_mismatch"}
            updated_policy = policy.model_copy(update={"guidance_digest": digest(updated.signing_bytes())})
            updated_policy = updated_policy.model_copy(update={"signature": key.sign(updated_policy.signing_bytes()).hex()})
            assert screen(ctx=updated_context, auth=updated_policy) == {"reason": "synthetic_eligible"}
            def denied():
                return screen(ctx=updated_context, auth=updated_policy) == {"reason": "unauthorized_guidance"}
            revoke(replacement, denied)
            assert denied()
            return
        if case is not None:
            def resign(body, signer):
                return body.model_copy(update={"signature": signer.sign(body.signing_bytes()).hex()})

            group, mutation = case.split(":", 1)
            if group == "provenance":
                name, kind = mutation.split(".")
                denial = {"pregnancy": "unknown_pregnancy_status", "allergies": "unknown_allergy_status",
                          "current_medicines": "incomplete_medication_history"}.get(name, "incomplete_evidence")
                changes = {
                    "stale_revision": {},
                    "missing_event": {"event": None},
                    "foreign_event": {"event": uuid4()},
                    "superseded": {"superseded": True},
                    "verified": {"status": "verified_complete", "source": "clinical"},
                    "forged_verification": {"status": "verified_complete"},
                    "corrected": {"event": refreshed.events[-1]},
                    "stale_digest": {"event": refreshed.events[-1]},
                }
                update = changes.get(kind, {"status": kind})
                changed = evidence.model_copy(update={name: field.model_copy(update=update)})
                if kind == "stale_revision":
                    changed = changed.model_copy(update={"revision": 1})
                rebound = resign(policy.model_copy(update={
                    "evidence_digest": digest(canonical(changed.model_dump(mode="json"))),
                }), key)
                expected_reason = {
                    "foreign_event": "binding_mismatch", "stale_revision": "binding_mismatch",
                    "stale_digest": "binding_mismatch", "corrected": "synthetic_eligible",
                    "reported_complete": "synthetic_eligible", "verified": "synthetic_eligible",
                }.get(kind, denial)
                if kind == "corrected":
                    assert screen(info=changed) == {"reason": "binding_mismatch"}
                assert screen(info=changed, auth=policy if kind == "stale_digest" else rebound) == {
                    "reason": expected_reason}
            elif group == "lower":
                changes, expected_status = {
                    "expiry": ({"expires_at": now}, "invalid_policy"),
                    "signature": ({"signature": "0"*128}, "invalid_policy"),
                    "version": ({"version": 2}, "policy_mismatch"),
                    "snapshot": ({"snapshot": "a"*64}, "policy_mismatch"),
                    "evidence": ({"evidence_digest": "a"*64}, "policy_mismatch"),
                    "reply_version": ({"content_version": 2}, "missing_content"),
                    "reply_digest": ({"content_digest": "a"*64}, "missing_content"),
                }[mutation]
                changed = lower.model_copy(update=changes)
                if mutation != "signature":
                    changed = resign(changed, private)
                ctx = context.model_copy(update={"approval": changed})
                outcome = asyncio.run(flow.evaluate(**ctx.guidance_arguments()))
                assert outcome.status == expected_status and outcome.level is None
                assert screen(ctx=ctx) == {"reason": "unauthorized_guidance"}
            else:
                changes, expected_reason = {
                    "version": ({"version": 2}, "invalid_policy"),
                    "condition": ({"condition": "synthetic_wrong"}, "binding_mismatch"),
                    "product": ({"product": "synthetic_wrong"}, "binding_mismatch"),
                    "formulary": ({"formulary_digest": "a"*64}, "binding_mismatch"),
                    "ingredients": ({"products": (ProductIngredients(product="synthetic_wrong",
                                     ingredients=("synthetic_ingredient",)),)}, "interaction_coverage_missing"),
                    "vocabulary": ({"vocabulary": "synthetic_wrong"}, "invalid_vocabulary"),
                }[mutation]
                assert screen(auth=resign(policy.model_copy(update=changes), key)) == {"reason": expected_reason}
            return
        for updates, reason in (
            ({"approval": None}, "unauthorized_guidance"),
            ({"owner": Principal("patient", uuid4())}, "invalid_context"),
            ({"revision": 1}, "invalid_context"),
            ({"event_id": uuid4()}, "unauthorized_guidance"),
            ({"assessment": validate_assessment(assessment | {"age": 31})}, "unauthorized_guidance"),
            ({"locale": "ur"}, "unauthorized_guidance"),
        ):
            assert screen(ctx=context.model_copy(update=updates)) == {"reason": reason}
        for change in (
            {"expires_at": now}, {"snapshot": "a" * 64}, {"version": 2},
            {"content_digest": "a" * 64},
        ):
            changed = lower.model_copy(update=change)
            changed = changed.model_copy(update={"signature": private.sign(changed.signing_bytes()).hex()})
            assert screen(ctx=context.model_copy(update={"approval": changed})) == {"reason": "unauthorized_guidance"}
        for name in ("age", "pregnancy", "allergies", "conditions", "current_medicines"):
            for status in ("unknown", "not_asked", "incomplete", "contradictory", "unsupported"):
                changed = evidence.model_copy(update={name: field.model_copy(update={"status": status})})
                fresh = policy.model_copy(update={"evidence_digest": digest(canonical(changed.model_dump(mode="json")))})
                fresh = fresh.model_copy(update={"signature": key.sign(fresh.signing_bytes()).hex()})
                reason = {"pregnancy": "unknown_pregnancy_status", "allergies": "unknown_allergy_status",
                          "current_medicines": "incomplete_medication_history"}.get(name, "incomplete_evidence")
                assert screen(info=changed, auth=fresh) == {"reason": reason}, (name, status)
        for changes, expected_reason in (
            ({"values": evidence.values.model_copy(update={"age": 31})}, "binding_mismatch"),
            ({"values": evidence.values.model_copy(update={"pregnancy": "pregnant"})}, "age_not_covered"),
            ({"values": evidence.values.model_copy(update={"current_medicines": ["synthetic_unknown"]})}, "invalid_vocabulary"),
            ({"allergies": field.model_copy(update={"status": "verified_complete", "source": "patient"})},
                "unknown_allergy_status"),
        ):
            changed = evidence.model_copy(update=changes)
            fresh = policy.model_copy(update={"evidence_digest": digest(canonical(changed.model_dump(mode="json")))})
            fresh = fresh.model_copy(update={"signature": key.sign(fresh.signing_bytes()).hex()})
            assert screen(info=changed, auth=fresh) == {"reason": expected_reason}
        for name in ("age", "pregnancy", "allergies", "conditions", "current_medicines"):
            for updates, expected_reason in (
                ({"event": None}, {"pregnancy": "unknown_pregnancy_status",
                    "allergies": "unknown_allergy_status", "current_medicines": "incomplete_medication_history"}.get(name, "incomplete_evidence")),
                ({"event": uuid4()}, "binding_mismatch"),
                ({"status": "verified_complete", "source": "clinical"}, "synthetic_eligible"),
            ):
                changed = evidence.model_copy(update={name: field.model_copy(update=updates)})
                rebound = policy.model_copy(update={"evidence_digest": digest(canonical(changed.model_dump(mode="json")))})
                rebound = rebound.model_copy(update={"signature": key.sign(rebound.signing_bytes()).hex()})
                assert screen(info=changed, auth=rebound) == {"reason": expected_reason}, (name, updates)
        assert screen(auth=None) == {"reason": "medication_policy_unavailable"}
        assert screen(auth=policy.model_copy(update={"signature": "0"*128})) == {"reason": "invalid_policy"}
        assert screen(info=evidence.model_copy(update={"pregnancy": field.model_copy(update={"superseded": True})})) == {
            "reason": "binding_mismatch"}
        assert screen(candidate=make_row()) == {"reason": "binding_mismatch"}
        original_signature = rows[0].pattern["attestation"]["signature"]
        rows[0].pattern["attestation"]["signature"] = "0" * 128
        assert screen() == {"reason": "unauthorized_guidance"}
        rows[0].pattern["attestation"]["signature"] = original_signature
        assert screen() == {"reason": "synthetic_eligible"}
        for priority in ("emergency", "urgent"):
            rows[:] = [sign(priority=priority, include={"op": "eq", "fact": "synthetic_first", "value": False})]
            assert screen() == {"reason": "emergency_or_urgent"}
        rows.append(sign(code="synthetic_other", include={"op": "eq", "fact": "synthetic_unknown", "value": True}))
        assert screen() == {"reason": "unresolved_risk"}
        rows[:] = []
        assert screen() == {"reason": "unauthorized_guidance"}

    if mode in ("home", "emergency", "no_policy"):
        samples = []
        for _ in range(55):
            start = perf_counter_ns()
            evaluate(approval=approval)
            samples.append((perf_counter_ns() - start) / 1e6)
        selected = samples[5:]
        print(f"GD-01 synthetic benchmark {mode} n=50 p50={median(selected):.3f} ms p95={sorted(selected)[47]:.3f} ms")


@pytest.mark.parametrize("invalid", [None, True, "18", 18.5, -1, 121],
                         ids=["null", "boolean", "string", "fractional", "negative", "above-maximum"])
def test_gd05_assessment_age_rejects_invalid_input(invalid):
    with pytest.raises(ValueError):
        validate_assessment(dict(text="Synthetic only", severity="mild", duration_hours=1, age=invalid))


def test_gd05_assessment_age_required():
    with pytest.raises(ValueError):
        validate_assessment(dict(text="Synthetic only", severity="mild", duration_hours=1))


@pytest.mark.parametrize("age", [0, 1, 17, 18, 120])
def test_gd05_assessment_age_preserves_valid_input(age):
    assert validate_assessment(dict(text="Synthetic only", severity="mild", duration_hours=1, age=age)).age == age
