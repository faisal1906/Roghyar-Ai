"""SYNTHETIC—DEVELOPMENT ONLY. Controlled labels cannot measure language quality."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from roghyar.classification import ClassificationService, Classified
from roghyar.conversation import ConversationMemoryService, Question
from roghyar.reply_writer import ReplyWriterService
from roghyar.scoped_session import Principal
from roghyar.structured_triage import Definition, Mapping, StructuredTriageService, Vocabulary
from roghyar.triage import TriageService, WarningArtifact
from test_red_flags import sign_rule  # noqa: F401

CASES = json.loads(Path(__file__).with_name("ai06_corpus.json").read_text(encoding="utf-8-sig"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_corpus_signed_emergency_is_independent(case, sign_rule, monkeypatch, caplog):
    now = datetime.now(UTC)
    owner = Principal("patient", uuid4())
    question = Question(key="synthetic_question", options=("yes", "no"))
    warning = WarningArtifact(reference="synthetic_warning", translations={"en": "Synthetic reviewed fixture."})
    sign, keys = sign_rule
    monkeypatch.setattr("roghyar.red_flags.PostgresRuleRepository.__call__",
                        lambda _: [sign(warning_sha256=warning.sha256())])
    triage = TriageService(None, keys, lambda *_: warning)

    class Classifier:
        async def infer(self, message):
            assert message.text == case["text"]
            return json.dumps({"languages": case["languages"], "relevance_categories": case["labels"],
                               "relevance_confidence": 0.9 if case["labels"] else None}).encode()

    class Extractor:
        async def extract(self, source):
            pytest.fail("An independent reviewed emergency must skip inference")

    classifier = ClassificationService(Classifier())
    memory = ConversationMemoryService(classifier, reviewed_questions=(question,))
    vocabulary = Vocabulary(
        version=1, review_ref="synthetic",
        facts=(Definition(fact="synthetic_first", kind="boolean", meaning="Synthetic value"),),
        mappings=(Mapping(question=question.key, option="yes", fact="synthetic_first", value=True),),
    )

    async def scenario():
        state = memory.start(owner, now)

        async def apply(action, **fields):
            nonlocal state
            result = await memory.apply(state, dict(action=action, conversation_id=state.conversation_id,
                                      event_id=uuid4(), revision=len(state.events), **fields), owner, now)
            assert result.reason == "applied"
            state = result.state

        await apply("ask", question=question.key)
        await apply("answer", question=question.key, option="yes")
        await apply("message", message={"text": case["text"], "language_hint": "en"})
        label = await classifier.classify({"text": case["text"]})
        assert (label.relevance_category if isinstance(label, Classified) else None) == case["category"]
        assert state.messages[-1].text == case["text"]
        outcome = await StructuredTriageService(triage, vocabulary, Extractor()).evaluate(
            owner=owner, state=state, revision=len(state.events), now=now, event_id=uuid4(),
            assessment={"text": case["text"], "severity": "mild", "age": 30, "duration_hours": 1},
        )
        assert outcome.status == "reviewed_emergency"
        assert outcome.reports[0].observation.provenance == "reported"
        writer = ReplyWriterService(triage=triage)
        result = writer.render(classification=label, outcome=outcome, now=now, locale="en")
        assert (result.intent, result.text, result.content_digest) == ("emergency", warning.translations["en"], warning.sha256())
        missing = writer.render(classification=label, outcome=outcome, now=now, locale="ps")
        assert (missing.intent, missing.text, missing.reason) == ("emergency", None, "warning_unavailable")

    asyncio.run(scenario())
    assert case["text"] not in caplog.text
