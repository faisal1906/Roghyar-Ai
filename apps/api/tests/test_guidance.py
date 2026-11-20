"""SYNTHETIC—DEVELOPMENT ONLY. No clinical policy or patient data."""
import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from statistics import median
from time import perf_counter_ns
from uuid import uuid4

import pytest

from roghyar.conversation import ConversationMemoryService, Question
from roghyar.guidance import GuidanceFlowService, GuidanceLevel, LowerApproval
from roghyar.red_flags import canonical
from roghyar.reply_writer import ReplyWriterService, Translation
from roghyar.scoped_session import Principal
from roghyar.structured_triage import Definition, Mapping, Vocabulary
from roghyar.triage import TriageService, TriageState
from test_red_flags import sign_rule
from test_reply_writer import signed, NOW
from dataclasses import replace


@pytest.mark.parametrize("mode,expected", [
    ("emergency", "reviewed_emergency"), ("urgent", "reviewed_urgent"),
    ("uncertain", "urgent_review_required"), ("missing", "rules_unavailable"),
    ("tampered", "invalid_rule_configuration"), ("outage", "evaluation_failure"),
    ("no_policy", "no_lower_policy"), ("doctor", "authorized_lower"),
    ("home", "authorized_lower"), ("revoked", "invalid_policy"),
    ("signature", "invalid_policy"), ("snapshot", "policy_mismatch"), ("version", "policy_mismatch"), ("translation", "missing_content"),
])
def test_signed_guidance_flow(mode, expected, monkeypatch, sign_rule, signed):
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
    service = GuidanceFlowService(triage, vocabulary, writer=writer,
                                  policy_keys={"sample_key": replace(authority, revoked=mode == "revoked")},
                                  active_policies=frozenset({("synthetic", 1)}))
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
        digest = hashlib.sha256(canonical({
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
                                 evidence_digest=digest, screening_review="synthetic_screen",
                                 content_id=item.body.content_id, content_version=item.body.version,
                                 content_digest=item.digest, signature="0"*128)
        if mode != "signature":
            approval = approval.model_copy(update={"signature": private.sign(approval.signing_bytes()).hex()})
    result = evaluate(approval=approval)
    assert result.status == expected
    assert result.medication_status == "unavailable"
    if truth:
        assert result.floor == (GuidanceLevel.EMERGENCY if mode == "emergency" else GuidanceLevel.URGENT)
        assert result.reply.reason != "verified"
        assert evaluate(approval={"level": "HOME CARE"}).floor == result.floor
    if expected == "authorized_lower":
        assert result.level == approval.level and result.reply.text == "Synthetic only"
        assert evaluate(approval=approval, event_id=uuid4()).status == "policy_mismatch"
    if mode in ("home", "emergency", "no_policy"):
        samples = []
        for _ in range(55):
            start = perf_counter_ns()
            evaluate(approval=approval)
            samples.append((perf_counter_ns() - start) / 1e6)
        selected = samples[5:]
        print(f"GD-01 synthetic benchmark {mode} n=50 p50={median(selected):.3f} ms p95={sorted(selected)[47]:.3f} ms")
