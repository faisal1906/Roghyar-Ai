"""SYNTHETIC—DEVELOPMENT ONLY. No clinical interpretation is tested."""

import asyncio
import json
from datetime import UTC, datetime
from statistics import median
from time import perf_counter_ns
from uuid import uuid4

import pytest

from roghyar.conversation import ConversationMemoryService
from roghyar.red_flags import Category, Match, Status
from roghyar.scoped_session import Principal
from roghyar.structured_triage import Definition, StructuredTriageService, TextSource, Vocabulary, parse_response
from roghyar.triage import TriageDecision

NOW = datetime(2026, 1, 1, tzinfo=UTC)
VOCAB = Vocabulary(version=1, review_ref="synthetic", facts=(Definition(fact="sample", kind="boolean", meaning="Synthetic flag"),),
                   conditions=("sample",), specialties=("sample",), summaries=("Review required.",))
DRAFT = {"text": "Synthetic report", "severity": "mild", "age": 30, "duration_hours": 1}
PROPOSAL = dict(condition_code="sample", specialty="sample", level="HOME_CARE",
                follow_up_questions=[], summary="Review required.")


@pytest.mark.parametrize("change", [
    {"level": "SAFE"}, {"condition_code": "other"}, {"specialty": "other"},
    {"dose": 1}, {"follow_up_questions": ["sample"] * 6}, {"summary": "Fabricated clinical claim"},
])
def test_rejects_invalid_proposals(change):
    source = TextSource(text="Synthetic report", event_id=uuid4())
    with pytest.raises(ValueError):
        parse_response(json.dumps(dict(version=1, candidates=[], proposal=PROPOSAL | change)).encode(), source, VOCAB)


@pytest.mark.parametrize("raw", [b"{", b'{"version":1,"version":1,"candidates":[]}', b"x" * 8193,
                                 b'{"version":NaN,"candidates":[]}', b'{"version":2,"candidates":[]}'])
def test_rejects_invalid_response(raw):
    with pytest.raises(ValueError):
        parse_response(raw, TextSource(text="Synthetic", event_id=uuid4()), VOCAB)


@pytest.mark.parametrize("text,support,value", [
    ("مجھے یقین نہیں", "uncertain", None), ("زما سر درد کوي today", "asserted", True),
    ("مجھے بخار ہے today", "asserted", True), ("Mujhe nahi pata", "uncertain", None),
    ("Zama sar dard kawi", "asserted", True), ("I do not report X", "negated", False),
    ("If I had X", "hypothetical", None), ("They said 'I have X'", "quoted", None),
    ("My relative reports X", "third_party", None),
])
def test_candidate_states_and_source_references(text, support, value):
    source = TextSource(text=text, event_id=uuid4())
    row = dict(fact="sample", event_id=str(source.event_id), support=support, value=value, span=[0, len(text)])
    def parse(item):
        return parse_response(json.dumps(dict(version=1, candidates=[item])).encode(), source, VOCAB)
    assert parse(row).candidates[0].value is value
    for change in ({"event_id": str(uuid4())}, {"span": [0, 4000]}, {"fact": "forged"},
                   {"unit": "unsupported"}, {"verified": True}, {"value": 1}):
        with pytest.raises(ValueError):
            parse(row | change)


@pytest.mark.parametrize("mode,expected", [
    ("emergency", "reviewed_emergency"), ("urgent", "reviewed_urgent"),
    ("unresolved", "triage_review_required"), ("missing", "evaluation_unavailable"),
    ("no_match", "lower_urgency_unauthorized"),
])
def test_safety_runs_before_inference(mode, expected):
    owner = Principal("patient", uuid4())
    state = ConversationMemoryService.start(owner, NOW)
    calls = []

    class Evaluator:
        def evaluate(self, evidence):
            assert evidence == {"observations": []}
            calls.append("evaluation")
            digest = "a" * 64
            if mode == "missing":
                return TriageDecision(Status.NO_APPROVED_RULES, NOW)
            if mode == "no_match":
                return TriageDecision(Status.NO_MATCH, NOW, digest)
            match = Match("synthetic", 1, next(iter(Category)), "emergency" if mode == "emergency" else "urgent",
                          (), "synthetic", digest)
            return TriageDecision(Status.MATCH, NOW, digest, (match,), ("sample",) if mode == "unresolved" else ())

    class Adapter:
        async def extract(self, source):
            calls.append("inference")
            return json.dumps(dict(version=1, candidates=[], proposal=PROPOSAL)).encode()

    service = StructuredTriageService(Evaluator(), VOCAB, Adapter())
    def evaluate(**overrides):
        return asyncio.run(service.evaluate(**(dict(owner=owner, state=state, revision=0, now=NOW,
                                                     assessment=DRAFT, event_id=uuid4()) | overrides)))
    assert evaluate(owner=Principal("patient", uuid4())).status == "invalid_input"
    assert evaluate(revision=1).status == "invalid_input"
    assert evaluate(now=state.expires_at).status == "invalid_input"
    assert evaluate().status == expected
    assert calls == (["evaluation", "inference"] if mode == "no_match" else ["evaluation"])


def test_synthetic_validation_benchmark():
    source = TextSource(text="Synthetic report", event_id=uuid4())
    raw = json.dumps(dict(version=1, candidates=[], proposal=PROPOSAL)).encode()
    samples = []
    for _ in range(200):
        start = perf_counter_ns()
        parse_response(raw, source, VOCAB)
        samples.append((perf_counter_ns() - start) / 1e6)
    print(f"AI-03 synthetic benchmark n=200 response validation p50={median(samples):.3f} ms "
          f"p95={sorted(samples)[189]:.3f} ms")
