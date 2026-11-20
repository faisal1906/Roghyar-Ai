"""SYNTHETICÃ¢â‚¬â€DEVELOPMENT ONLY. No clinical policy or patient data."""
import asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from roghyar.assessment import validate_assessment
from roghyar.formulary_safety import FormularyRecord, SafetyContext, signature_message
from roghyar.medication_safety import EvidenceField, MedicationContext, MedicationEvidence, MedicationPolicy, ProductIngredients, digest
from legacy_medication_fixture import HistoricalMedicationService as MedicationSafetyService
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
        bundle, repository, publish, replace_active, revoke, mutate_translation = request.getfixturevalue("published_chain_content")
        now = datetime.now(UTC)
        writer = ReplyWriterService(catalog=(item,), authorities={"sample_key": replace(
            authority, valid_until=now + timedelta(days=1))}, triage=triage)
    service = GuidanceFlowService(triage, vocabulary, writer=writer,
                                  policy_keys={"sample_key": replace(authority, revoked=mode == "revoked")},
                                  active_policies=frozenset({("synthetic", 1)}), home_care=repository)
    age = int(case.split(":")[1].split(".")[0]) if case and case.startswith(("age:", "age_change:")) else 30
    assessment = dict(text="Synthetic-only input", severity="mild", age=age, duration_hours=1)
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
        evidence = MedicationEvidence(values=SafetyContext(age=age, pregnancy="not_pregnant",
            allergies=[], conditions=[], current_medicines=[]), vocabulary="synthetic", revision=2,
            age=field, pregnancy=field, allergies=field, conditions=field, current_medicines=field)
        make_row, formulary_keys = sign_formulary
        row_options = {"children": bool(case and case.endswith(".child"))}
        if case and case.startswith("screening:"):
            from test_formulary_safety import COMPATIBLE, OTHER
            row_options.update(compatible=(COMPATIBLE, OTHER), coverage=case != "screening:be05_coverage_required",
                               pregnancy=(
                "any_known_status" if case == "screening:pregnant_applicable" else "not_pregnant_only"))
        if case and case.startswith("age_change:"):
            row_options.update(children=True, bands=((5, 7), (9, 11)))
        row = make_row(**row_options)
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
        if case and case.startswith("screening:"):
            from gd05_screening_cases import exercise
            asyncio.run(exercise(case.split(":")[1], flow, context, evidence, policy,
                                 row, formulary_keys, policy_authority, key, private))
            return
        if mode == "structured_postgres":
            assert screen() == {"reason": "unauthorized_guidance"}
            publish()
        if case and case.startswith("age:"):
            expected_age = ("pediatric_dose_unsupported" if age in (0, 4, 11) else
                            "age_not_covered" if age in (1, 17, 66, 120) else "synthetic_eligible")
            assert screen() == {"reason": expected_age}
            return
        if case and case.startswith("chain:"):
            mutation = case.split(":")[1]
            assert screen() == {"reason": "synthetic_eligible"}
            home = lower.structured_home
            binding = home.binding
            edits = {
                "structured_version": {"structured_home": home.model_copy(update={
                    "binding": binding.model_copy(update={"version": 2})})},
                "structured_id": {"structured_home": home.model_copy(update={
                    "binding": binding.model_copy(update={"content_id": "synthetic_other"})})},
                "structured_digest": {"structured_home": home.model_copy(update={
                    "binding": binding.model_copy(update={"digest": "a" * 64})})},
                "condition": {"structured_home": home.model_copy(update={"condition": "synthetic_other"})},
                "applicability": {"structured_home": home.model_copy(update={"applicability": "synthetic_other"})},
                "vocabulary": {"vocabulary_version": 2},
                "reviewer": {"reviewer": "synthetic_other"},
                "key": {"key_id": "synthetic_other"},
                "conversation": {"conversation": uuid4()},
                "reply_id": {"content_id": "synthetic_other"},
                "cross_binding": {"structured_home": home.model_copy(update={
                    "binding": binding.model_copy(update={"digest": item.digest})})},
            }
            changed = lower.model_copy(update=edits.get(mutation, {}))
            changed = changed.model_copy(update={"signature": private.sign(changed.signing_bytes()).hex()})
            ctx = context.model_copy(update={"approval": changed, **({"locale": "ur"} if mutation == "locale" else {})})
            signed_policy = policy.model_copy(update={"guidance_digest": digest(changed.signing_bytes())})
            signed_policy = signed_policy.model_copy(update={"signature": key.sign(signed_policy.signing_bytes()).hex()})
            outcome = asyncio.run(flow.evaluate(**ctx.guidance_arguments()))
            expected = ("invalid_policy" if mutation in ("reviewer", "key") else
                        "policy_mismatch" if mutation in ("conversation", "vocabulary") else "missing_content")
            assert outcome.status == expected and outcome.level is None
            assert screen(ctx, evidence, signed_policy) == {"reason": "unauthorized_guidance"}
            assert screen() == {"reason": "synthetic_eligible"}
            return
        def initial_age_reason(value):
            if value < 4 or value > 65:
                return "age_not_covered"
            if value < 18 and value not in (5, 6, 7, 9, 10, 11):
                return "pediatric_dose_unsupported"
            return "synthetic_eligible"
        rebind_age = case is not None and case.startswith("age_change:")
        assert screen() == {"reason": initial_age_reason(age) if rebind_age else "synthetic_eligible"}
        if mode == "structured_postgres" and case and case.startswith("translation_db:"):
            name = case.split(":")[1]
            for locale in ("en", "ur", "ps"):
                result = repository.resolve(selection(bundle, locale=locale, now=datetime.now(UTC)))
                assert result.status == "verified" and result.variant.locale == locale
            def check_chain(revision):
                bound = lower.structured_home.model_copy(update={"binding": selection(revision).binding})
                update = lower.model_copy(update={"structured_home": bound})
                update = update.model_copy(update={"signature": private.sign(update.signing_bytes()).hex()})
                ctx = context.model_copy(update={"approval": update})
                rebound = policy.model_copy(update={"guidance_digest": digest(update.signing_bytes())})
                rebound = rebound.model_copy(update={"signature": key.sign(rebound.signing_bytes()).hex()})
                outcome = asyncio.run(flow.evaluate(**ctx.guidance_arguments()))
                assert outcome.status == "missing_content" and outcome.structured_home is not None
                assert outcome.structured_home.variant is None
                assert screen(ctx, evidence, rebound) == {"reason": "unauthorized_guidance"}
            mutate_translation(name, check_chain)
            assert screen() == {"reason": "synthetic_eligible"}
            return
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
        if case and case.startswith(("custody:", "changed:", "race:", "transplant:", "age_change:")):
            from concurrent.futures import ThreadPoolExecutor
            from roghyar.evidence_custody import EvidenceConflict, InMemoryEvidenceCustody

            from roghyar.evidence_authority import EvidenceAttestation, EvidenceIssuer

            name = "age" if rebind_age else case.split(":")[1]
            signing_key = Ed25519PrivateKey.generate()
            issuer = EvidenceIssuer("synthetic_issuer", signing_key.public_key(), frozenset({name, "pregnancy"}) if case.endswith(":field") else frozenset({name}),
                                    now - timedelta(seconds=1), now + timedelta(minutes=5), "disposable")
            issuers = {"synthetic": issuer}

            def issued(source_event, revision, values=evidence.values, **identity):
                proof = EvidenceAttestation(owner=identity.get("owner", owner.subject), owner_kind=owner.kind,
                    conversation=identity.get("conversation", refreshed.conversation_id), event=source_event, revision=revision,
                    field=identity.get("field", name),
                    value_digest=identity.get("value_digest", digest(canonical(getattr(values, name)))), vocabulary="synthetic",
                    issuer=issuer.identity, key_id="synthetic", issued_at=now, expires_at=lower.expires_at,
                    signature="0"*128)
                return field.model_copy(update={"event": source_event, "status": "verified_complete",
                    "source": "clinical", "attestation": proof.model_copy(update={
                        "signature": signing_key.sign(proof.signing_bytes()).hex()})})

            evidence = evidence.model_copy(update={name: issued(event, 2)})
            policy = policy.model_copy(update={"evidence_digest": digest(canonical(evidence.model_dump(mode="json")))})
            policy = policy.model_copy(update={"signature": key.sign(policy.signing_bytes()).hex()})
            custody = InMemoryEvidenceCustody(owner, refreshed, evidence, assessment_event=event, now=now)
            guarded = MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
                formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
                active_policies=frozenset({("synthetic", 1)}), evidence_custody=custody, evidence_issuers=issuers)

            def check(ctx, data, signed_policy):
                return asyncio.run(guarded.screen(guidance_context=ctx, evidence=data,
                    policy=signed_policy, formulary=row)).model_dump()

            assert check(context, evidence, policy) == {"reason": initial_age_reason(age) if rebind_age else "synthetic_eligible"}
            occurrence = uuid4()
            corrected = evidence.model_copy(update={"revision": 3,
                name: issued(occurrence, 3)})
            if case.startswith(("changed:", "race:", "transplant:", "age_change:")):
                from test_formulary_safety import ALLERGY, BLOCKED, COMPATIBLE, CONTRA

                variant = case.split(":")[-1]
                changes = {"age": 66, "pregnancy": "pregnant", "allergies": [ALLERGY],
                           "conditions": [CONTRA], "current_medicines": [BLOCKED]}
                if variant == "allowed":
                    changes.update(age=31, current_medicines=[COMPATIBLE])
                elif variant == "unknown":
                    changes["current_medicines"] = ["synthetic_unknown"]
                if rebind_age:
                    changes["age"] = int(variant)
                values = SafetyContext.model_validate(evidence.values.model_dump() | {name: changes[name]})
                corrected = corrected.model_copy(update={"values": values, name: issued(occurrence, 3, values)})
                correction = dict(expected_revision=2, field=name, predecessor=event,
                                  event=occurrence, evidence=corrected, now=now)

                if case.startswith("race:"):
                    from roghyar import medication_safety
                    verifier = medication_safety.screen_verified_formulary
                    def interleave(*args):
                        custody.correct(owner, refreshed, **correction)
                        return verifier(*args)
                    monkeypatch.setattr(medication_safety, "screen_verified_formulary", interleave)
                    assert check(context, evidence, policy) == {"reason": "binding_mismatch"}
                    assert custody.current(owner, refreshed.conversation_id).revision == 3
                    return

                latest = custody.correct(owner, refreshed, **correction)

                def bind(current_state, current_evidence, principal=owner):
                    draft = validate_assessment(assessment | {"age": current_evidence.values.age})
                    assessment_digest = digest(canonical({
                        "assessment": draft.model_dump(mode="json"),
                        "owner": {"kind": principal.kind, "subject": str(principal.subject)},
                        "reports": [{"observation": {"fact": "synthetic_first", "provenance": "reported", "value": False},
                                     "events": [str(state.events[-1])], "supersedes": []}],
                    }))
                    new_lower = lower.model_copy(update={"revision": current_evidence.revision,
                                                        "conversation": current_state.conversation_id,
                                                        "evidence_digest": assessment_digest})
                    new_lower = new_lower.model_copy(update={"signature": private.sign(new_lower.signing_bytes()).hex()})
                    ctx = context.model_copy(update={"owner": principal, "state": current_state, "revision": current_evidence.revision,
                                                     "assessment": draft, "approval": new_lower})
                    return ctx, authorize(ctx, current_evidence)

                def authorize(ctx, data):
                    rebound = policy.model_copy(update={"products": policy.products + (
                        ProductIngredients(product=COMPATIBLE, ingredients=("synthetic_compatible_ingredient",)),
                    ), "guidance_digest": digest(ctx.approval.signing_bytes()),
                        "evidence_digest": digest(canonical(data.model_dump(mode="json")))})
                    return rebound.model_copy(update={"signature": key.sign(rebound.signing_bytes()).hex()})

                current, rebound = bind(latest, corrected)
                assert check(context, evidence, policy) == {"reason": "binding_mismatch"}
                stale = current.model_copy(update={"approval": lower})
                assert check(stale, corrected, authorize(stale, corrected)) == {"reason": "unauthorized_guidance"}
                assert check(current, corrected, policy) == {"reason": "binding_mismatch"}
                expected = ("synthetic_eligible" if variant == "allowed" else
                            "invalid_vocabulary" if variant == "unknown" else
                            "age_not_covered" if name in ("age", "pregnancy") else "known_contraindication")
                if rebind_age:
                    expected = initial_age_reason(changes["age"])
                assert check(current, corrected, rebound) == {"reason": expected}

                final_event = uuid4()
                final = evidence.model_copy(update={"revision": 4, name: issued(final_event, 4)})
                final_state = custody.correct(owner, latest, expected_revision=3, field=name, predecessor=occurrence,
                                              event=final_event, evidence=final, now=now)
                final_ctx, final_policy = bind(final_state, final)
                assert check(final_ctx, final, final_policy) == {"reason": initial_age_reason(age) if rebind_age else "synthetic_eligible"}
                historical = getattr(evidence, name).attestation
                issuer_registry = issuers
                if case.startswith("transplant:"):
                    assert historical.verified_by(issuer, now)
                    assert len({event, occurrence, final_event}) == 3
                    snapshot = custody.current(owner, final_state.conversation_id)
                    assert snapshot.matches(final_state, final, now) and snapshot.revision == 4
                    assert [entry.event for entry in snapshot.history if entry.name == name] == [event, occurrence]
                    with pytest.raises(EvidenceConflict, match="^wrong_predecessor$"):
                        custody.correct(owner, final_state, expected_revision=4, field=name,
                                        predecessor=event, event=uuid4(),
                                        evidence=final.model_copy(update={"revision": 5}), now=now)
                    original_transplant = final.model_copy(update={name: getattr(final, name).model_copy(
                        update={"attestation": historical})})
                    assert check(final_ctx, original_transplant, authorize(final_ctx, original_transplant)) == {
                        "reason": "binding_mismatch"}
                    if variant == "event":
                        historical = issued(event, 4).attestation
                    elif variant == "revision":
                        historical = issued(final_event, 2).attestation
                    elif variant == "value":
                        historical = issued(final_event, 4, values).attestation
                    elif variant in ("field", "owner", "conversation"):
                        source_owner = Principal(owner.kind, uuid4()) if variant == "owner" else owner
                        source_state = final_state.model_copy(update={
                            "owner_subject": source_owner.subject,
                            "conversation_id": uuid4() if variant == "conversation" else final_state.conversation_id,
                        })
                        source_name = "pregnancy" if variant == "field" else name
                        source_field = issued(final_event, 4, field=source_name, owner=source_owner.subject,
                            conversation=source_state.conversation_id,
                            value_digest=digest(canonical(getattr(final.values, source_name))))
                        source_evidence = final.model_copy(update={name: field, source_name: source_field})
                        source_ctx, source_policy = bind(source_state, source_evidence, source_owner)
                        source_custody = InMemoryEvidenceCustody(
                            source_owner, source_state, source_evidence, assessment_event=event, now=now)
                        source_service = MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
                            formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
                            active_policies=frozenset({("synthetic", 1)}), evidence_issuers=issuers,
                            evidence_custody=source_custody)
                        assert asyncio.run(source_service.screen(guidance_context=source_ctx, evidence=source_evidence,
                            policy=source_policy, formulary=row)).reason == "synthetic_eligible"
                        historical = source_field.attestation
                    elif variant in ("revoked", "expired", "scope"):
                        updates = {"revoked": True} if variant == "revoked" else (
                            {"valid_until": now} if variant == "expired" else {"fields": frozenset()})
                        issuer_registry = {"synthetic": replace(issuer, **updates)}
                        # Isolate authority denial on the otherwise current valid proof too.
                        authority_check = MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
                            formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
                            active_policies=frozenset({("synthetic", 1)}), evidence_issuers=issuer_registry,
                            evidence_custody=custody)
                        assert asyncio.run(authority_check.screen(guidance_context=final_ctx, evidence=final,
                            policy=final_policy, formulary=row)).reason == "incomplete_evidence"
                    elif variant == "second":
                        historical = getattr(corrected, name).attestation
                transplant = final.model_copy(update={name: getattr(final, name).model_copy(
                    update={"attestation": historical})})
                assert check(final_ctx, transplant, authorize(final_ctx, transplant)) == {"reason": "binding_mismatch"}
                # Also exercise the independent issuer verifier without masking it behind custody.
                bare = MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
                    formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
                    active_policies=frozenset({("synthetic", 1)}), evidence_issuers=issuer_registry)
                assert asyncio.run(bare.screen(guidance_context=final_ctx, evidence=transplant,
                    policy=authorize(final_ctx, transplant), formulary=row)).reason == "incomplete_evidence"
                return
            args = dict(expected_revision=2, field=name, predecessor=event,
                        event=occurrence, evidence=corrected, now=now)
            for principal, old, overrides in (
                (Principal("patient", uuid4()), refreshed, {}),
                (owner, refreshed.model_copy(update={"conversation_id": uuid4()}), {}),
                (owner, refreshed, {"now": refreshed.expires_at}),
                (owner, refreshed, {"predecessor": uuid4()}),
                (owner, refreshed, {"event": event}),
                (owner, refreshed, {"expected_revision": 1}),
            ):
                with pytest.raises(EvidenceConflict):
                    custody.correct(principal, old, **(args | overrides))

            def compete(_):
                try:
                    return custody.correct(owner, refreshed, **args)
                except EvidenceConflict:
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                winners = [state for state in pool.map(compete, range(2)) if state is not None]
            assert len(winners) == 1
            latest = winners[0]
            with pytest.raises(EvidenceConflict):
                custody.correct(owner, refreshed, **args)
            with pytest.raises(EvidenceConflict, match="replayed_event"):
                custody.correct(owner, latest, **(args | {"expected_revision": 3, "predecessor": occurrence,
                    "evidence": corrected.model_copy(update={"revision": 4})}))
            snapshot = custody.current(owner, latest.conversation_id)
            assert snapshot.revision == 3 and snapshot.history[-1].event == event
            assert next(f for f in snapshot.fields if f.name == name).predecessor == event
            assert check(context, evidence, policy) == {"reason": "binding_mismatch"}
            stale = context.model_copy(update={"state": latest, "revision": 3})
            assert check(stale, corrected, policy) == {"reason": "unauthorized_guidance"}
            new_lower = lower.model_copy(update={"revision": 3})
            new_lower = new_lower.model_copy(update={"signature": private.sign(new_lower.signing_bytes()).hex()})
            current_context = stale.model_copy(update={"approval": new_lower})
            assert check(current_context, corrected, policy) == {"reason": "binding_mismatch"}
            rebound = policy.model_copy(update={"guidance_digest": digest(new_lower.signing_bytes()),
                "evidence_digest": digest(canonical(corrected.model_dump(mode="json")))})
            rebound = rebound.model_copy(update={"signature": key.sign(rebound.signing_bytes()).hex()})
            assert check(current_context, corrected, rebound) == {"reason": "synthetic_eligible"}
            revoked_service = MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
                formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
                active_policies=frozenset({("synthetic", 1)}), evidence_custody=custody,
                evidence_issuers={"synthetic": replace(issuer, revoked=True)})
            assert asyncio.run(revoked_service.screen(guidance_context=current_context, evidence=corrected,
                       policy=rebound, formulary=row)).reason == "incomplete_evidence"
            rows[:] = [sign(include={"op": "eq", "fact": "synthetic_first", "value": False})]
            assert check(current_context, corrected, rebound) == {"reason": "emergency_or_urgent"}
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
                    "reported_complete": "synthetic_eligible", "verified": "incomplete_evidence",
                }.get(kind, denial)
                if kind == "corrected":
                    assert screen(info=changed) == {"reason": "binding_mismatch"}
                assert screen(info=changed, auth=policy if kind == "stale_digest" else rebound) == {
                    "reason": expected_reason}
            elif group == "issuer":
                from roghyar.evidence_authority import EvidenceAttestation, EvidenceIssuer

                proof = EvidenceAttestation(owner=owner.subject, owner_kind=owner.kind,
                    conversation=refreshed.conversation_id, event=event, revision=2, field="pregnancy",
                    value_digest=digest(canonical(evidence.values.pregnancy)), vocabulary="synthetic",
                    issuer="synthetic_issuer", key_id="issuer_key", issued_at=now,
                    expires_at=now + timedelta(minutes=1), signature="0"*128)
                issuer_key = Ed25519PrivateKey.generate()
                issuer = EvidenceIssuer("synthetic_issuer", issuer_key.public_key(), frozenset({"pregnancy"}),
                    now - timedelta(minutes=1), now + timedelta(minutes=2), "synthetic trust anchor")
                updates = {"owner": {"owner": uuid4()}, "conversation": {"conversation": uuid4()},
                           "event": {"event": uuid4()}, "revision": {"revision": 1},
                           "field": {"field": "age"}, "value": {"value_digest": "a"*64},
                           "vocabulary": {"vocabulary": "other"}, "expired": {"expires_at": now}}
                proof = resign(proof.model_copy(update=updates.get(mutation, {})), issuer_key)
                if mutation == "signature":
                    proof = proof.model_copy(update={"signature": "0"*128})
                if mutation == "revoked":
                    issuer = replace(issuer, revoked=True)
                if mutation == "scope":
                    issuer = replace(issuer, fields=frozenset({"age"}))
                changed = evidence.model_copy(update={"pregnancy": field.model_copy(update={
                    "status": "verified_complete", "source": "clinical", "attestation": proof,
                })})
                auth = resign(policy.model_copy(update={
                    "evidence_digest": digest(canonical(changed.model_dump(mode="json"))),
                }), key)
                verified = MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
                    formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
                    active_policies=frozenset({("synthetic", 1)}),
                    evidence_issuers={} if mutation == "unknown_key" else {"issuer_key": issuer})
                result = asyncio.run(verified.screen(guidance_context=context, evidence=changed,
                                                     policy=auth, formulary=row))
                assert result.model_dump() == {
                    "reason": "synthetic_eligible" if mutation == "valid" else "incomplete_evidence"}
            elif group == "authority":
                expired = now
                lower_keys = {"sample_key": fresh_authority}
                reply_keys = {"sample_key": fresh_authority}
                home_keys = {"sample_key": fresh_home}
                medication_keys = {"synthetic": policy_authority}
                form_keys = dict(formulary_keys)
                catalog, bundles = (item,), (bundle,)
                active = frozenset({("synthetic", 1)})
                guidance_active = active
                ctx = context
                target = {
                    "lower": lower_keys, "reply": reply_keys, "home": home_keys,
                    "policy": medication_keys, "formulary": form_keys,
                }
                boundary, change = mutation.split(".")
                if change in ("revoked", "expired", "scope"):
                    registry = target[boundary]
                    identity = next(iter(registry))
                    updates = {"revoked": True} if change == "revoked" else {"valid_until": expired}
                    if change == "scope":
                        updates = ({"authorized_medicines": frozenset()} if boundary == "formulary"
                                   else {"scopes": frozenset({"product"})})
                    registry[identity] = replace(registry[identity], **updates)
                elif mutation == "lower.untrusted":
                    alien = Ed25519PrivateKey.generate()
                    ctx = context.model_copy(update={"approval": resign(lower, alien)})
                elif mutation == "lower.inactive":
                    guidance_active = frozenset()
                elif mutation == "policy.inactive":
                    active = frozenset()
                elif mutation == "reply.removed":
                    catalog = ()
                elif mutation == "reply.tampered":
                    translated = item.body.translations[0].model_copy(update={"text": "Synthetic changed"})
                    catalog = (item.model_copy(update={"body": item.body.model_copy(update={"translations": (translated,)})}),)
                elif mutation == "home.tampered":
                    bundles = (bundle.model_copy(update={"signature": "0"*128}),)
                writer = ReplyWriterService(catalog=catalog, authorities=reply_keys, triage=triage)
                verifier = GuidanceFlowService(triage, vocabulary, writer=writer, policy_keys=lower_keys,
                    active_policies=guidance_active,
                    home_care=HomeCareContentRepository(bundles, authorities=home_keys))
                evaluated = asyncio.run(verifier.evaluate(**ctx.guidance_arguments()))
                expected = {
                    "lower.inactive": "no_lower_policy",
                    "reply.revoked": "invalid_authority", "reply.removed": "missing_content",
                    "reply.tampered": "digest_mismatch", "home.revoked": "revoked_authority",
                    "home.tampered": "invalid_signature", "policy.revoked": "revoked_authority",
                    "policy.inactive": "medication_policy_unavailable",
                }.get(mutation, "invalid_formulary" if boundary == "formulary" else "invalid_policy")
                medication_check = MedicationSafetyService(verifier, VOCABULARY, vocabulary_version="synthetic",
                    formulary_reviewers=form_keys, policy_reviewers=medication_keys, active_policies=active)
                screened = asyncio.run(medication_check.screen(guidance_context=ctx, evidence=evidence,
                                                               policy=policy, formulary=row)).model_dump()
                if boundary in ("policy", "formulary"):
                    assert evaluated.status == "authorized_lower"
                    assert screened == {"reason": expected}
                else:
                    assert evaluated.level is None and screened == {"reason": "unauthorized_guidance"}
                    assert evaluated.reply is None or evaluated.reply.text is None
                    actual = (evaluated.reply.reason if evaluated.reply is not None else
                              evaluated.structured_home.status if evaluated.structured_home is not None else evaluated.status)
                    assert actual == expected
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
                ({"status": "verified_complete", "source": "clinical"}, "incomplete_evidence"),
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
