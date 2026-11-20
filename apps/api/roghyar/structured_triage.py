"""Private evidence bridge. Inference never supplies observations to signed rules."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import Field, StringConstraints, ValidationError, model_validator

from .assessment import validate_assessment
from .classification import ClassificationInput, StrictContract, _invalid_constant, _unique_object
from .conversation import ConversationState
from .red_flags import Evidence, Fact, Identifier, Observation
from .scoped_session import Principal
from .triage import TriageDecision, TriageService, TriageState

Level = Literal["EMERGENCY", "URGENT", "SEE_DOCTOR", "HOME_CARE"]
DISPLAY_LEVELS: dict[Level, str] = {
    "EMERGENCY": "EMERGENCY", "URGENT": "URGENT", "SEE_DOCTOR": "SEE A DOCTOR", "HOME_CARE": "HOME CARE",
}
MAX_RESPONSE = 8192


class Definition(StrictContract):
    fact: Identifier
    kind: Literal["boolean", "integer"]
    unit: Identifier | None = None
    meaning: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    minimum: Annotated[int, Field(ge=-1000000, le=1000000)] | None = None
    maximum: Annotated[int, Field(ge=-1000000, le=1000000)] | None = None

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.kind == "integer":
            if self.unit is None or self.minimum is None or self.maximum is None or self.minimum > self.maximum:
                raise ValueError("Invalid measurement definition")
        elif any(v is not None for v in (self.unit, self.minimum, self.maximum)):
            raise ValueError("Unexpected measurement metadata")
        return self

    def accepts(self, value: Fact, unit: str | None) -> bool:
        if unit != self.unit:
            return False
        if self.kind == "boolean":
            return type(value) is bool
        return type(value) is int and self.minimum <= value <= self.maximum


class Mapping(StrictContract):
    question: Identifier
    option: Identifier
    fact: Identifier
    value: Fact
    unit: Identifier | None = None


class Vocabulary(StrictContract):
    """Loaded only by trusted server composition after publication checks."""
    version: Annotated[int, Field(ge=1, le=2147483647)]
    review_ref: Identifier
    facts: Annotated[tuple[Definition, ...], Field(min_length=1, max_length=32)]
    mappings: Annotated[tuple[Mapping, ...], Field(max_length=128)] = ()
    conditions: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    specialties: Annotated[tuple[Identifier, ...], Field(max_length=128)] = ()
    questions: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    summaries: Annotated[tuple[Annotated[str, StringConstraints(min_length=1, max_length=160)], ...],
                         Field(max_length=16)] = ()

    @model_validator(mode="after")
    def mappings_are_defined(self) -> Self:
        definitions = {d.fact: d for d in self.facts}
        keys = {(m.question, m.option) for m in self.mappings}
        if len(definitions) != len(self.facts) or len(keys) != len(self.mappings):
            raise ValueError("Duplicate vocabulary entry")
        for mapping in self.mappings:
            definition = definitions.get(mapping.fact)
            if definition is None or not definition.accepts(mapping.value, mapping.unit):
                raise ValueError("Unsupported mapping")
        return self


class TextSource(ClassificationInput):
    event_id: UUID
    language_hint: Literal["en", "ur", "ps"] | None = None


class Candidate(StrictContract):
    fact: Identifier
    event_id: UUID
    support: Literal["asserted", "negated", "uncertain", "hypothetical", "quoted", "third_party"]
    value: Fact | None = None
    unit: Identifier | None = None
    span: tuple[Annotated[int, Field(ge=0, le=4000)], Annotated[int, Field(ge=0, le=4000)]] | None = None

    @model_validator(mode="after")
    def support_is_consistent(self) -> Self:
        if self.support == "asserted" and (self.value is None or self.value is False):
            raise ValueError("Missing asserted value")
        if self.support == "negated" and self.value is not False:
            raise ValueError("Negation requires explicit boolean false")
        if self.support not in ("asserted", "negated") and self.value is not None:
            raise ValueError("Unresolved candidate cannot carry a value")
        return self


class Proposal(StrictContract):
    condition_code: Identifier
    level: Level
    specialty: Identifier
    follow_up_questions: Annotated[tuple[Identifier, ...], Field(max_length=5)]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=160)] = Field(repr=False)


class Extraction(StrictContract):
    version: Literal[1]
    candidates: Annotated[tuple[Candidate, ...], Field(max_length=32)]
    proposal: Proposal | None = None


class Inference(Protocol):
    async def extract(self, source: TextSource) -> bytes: ...


@dataclass(frozen=True, slots=True)
class Report:
    observation: Observation
    events: tuple[UUID, ...]
    supersedes: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class Outcome:
    status: Literal[
        "invalid_input", "missing_vocabulary", "unsupported_mapping", "contradictory",
        "reviewed_emergency", "reviewed_urgent", "triage_review_required", "evaluation_unavailable",
        "provider_unavailable", "provider_timeout", "invalid_provider_response",
        "validated_candidates", "unresolved_extraction", "lower_urgency_unauthorized",
    ]
    decision: TriageDecision | None = None
    reports: tuple[Report, ...] = ()
    extraction: Extraction | None = None
    vocabulary_version: int | None = None


def parse_response(raw: bytes, source: TextSource, vocabulary: Vocabulary) -> Extraction:
    if type(raw) is not bytes or len(raw) > MAX_RESPONSE:
        raise ValueError("Invalid response size")
    decoded = raw.decode("utf-8")
    json.loads(decoded, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    result = Extraction.model_validate_json(decoded)
    definitions = {d.fact: d for d in vocabulary.facts}
    for candidate in result.candidates:
        definition = definitions.get(candidate.fact)
        if (definition is None or candidate.event_id != source.event_id or candidate.unit != definition.unit
                or (candidate.span is not None and not 0 <= candidate.span[0] < candidate.span[1] <= len(source.text))
                or (candidate.value is not None and not definition.accepts(candidate.value, candidate.unit))):
            raise ValueError("Unsupported source or candidate")
    proposal = result.proposal
    if proposal is not None and (
        proposal.condition_code not in vocabulary.conditions or proposal.specialty not in vocabulary.specialties
        or proposal.summary not in vocabulary.summaries or
        not set(proposal.follow_up_questions) <= set(vocabulary.questions)
        or len(set(proposal.follow_up_questions)) != len(proposal.follow_up_questions)
    ):
        raise ValueError("Unsupported proposal")
    return result


class StructuredTriageService:
    def __init__(self, triage: TriageService, vocabulary: Vocabulary | None = None,
                 inference: Inference | None = None):
        self._triage = triage
        self._vocabulary = Vocabulary.model_validate(vocabulary.model_dump()) if vocabulary is not None else None
        self._inference = inference

    async def evaluate(self, *, owner: Principal, state: ConversationState, revision: int,
                       now: datetime, assessment: object, event_id: UUID) -> Outcome:
        """State, revision and event ID must originate from authoritative server custody."""
        try:
            state = ConversationState.model_validate(state.model_dump())
            if (owner.kind, owner.subject) != (state.owner_kind, state.owner_subject):
                return Outcome("invalid_input")
            if (type(revision) is not int or revision != len(state.events) or now.utcoffset() is None
                    or not state.created_at <= now < state.expires_at
                    or state.phase in ("expired", "cancelled", "completed")):
                return Outcome("invalid_input")
            draft = validate_assessment(assessment)
            source = TextSource(text=draft.text, language_hint=draft.language, event_id=event_id)
        except (ValueError, TypeError, AttributeError):
            return Outcome("invalid_input")
        vocabulary = self._vocabulary
        if vocabulary is None:
            return Outcome("missing_vocabulary")
        mappings = {(m.question, m.option): m for m in vocabulary.mappings}
        grouped = {}
        unsupported = False
        for fact in state.reported_facts:
            mapping = mappings.get((fact.question, fact.option))
            if mapping is None:
                unsupported = True
                continue
            grouped.setdefault(mapping.fact, []).append((mapping.value, fact))
        reports = []
        contradictory = False
        for key, entries in sorted(grouped.items()):
            values = {value for value, _ in entries}
            conflict = len(values) > 1
            contradictory |= conflict
            observation = Observation(fact=key, provenance="unknown" if conflict else "reported",
                                      value=None if conflict else entries[0][0])
            reports.append(Report(observation, tuple(f.event_id for _, f in entries),
                                  tuple(f.supersedes for _, f in entries if f.supersedes is not None)))
        metadata = {"reports": tuple(reports), "vocabulary_version": vocabulary.version}
        try:
            decision = self._triage.evaluate(Evidence(observations=[r.observation for r in reports]).model_dump())
            state_code = decision.state
        except Exception:
            return Outcome("evaluation_unavailable", **metadata)
        definitive = {
            TriageState.REVIEWED_EMERGENCY: "reviewed_emergency",
            TriageState.REVIEWED_URGENT: "reviewed_urgent",
            TriageState.URGENT_REVIEW_REQUIRED: "triage_review_required",
        }
        if state_code in definitive:
            return Outcome(definitive[state_code], decision, **metadata)
        if state_code in (TriageState.RULES_UNAVAILABLE, TriageState.EVALUATION_FAILURE):
            return Outcome("evaluation_unavailable", decision, **metadata)
        if contradictory or unsupported:
            return Outcome("contradictory" if contradictory else "unsupported_mapping", decision, **metadata)
        if self._inference is None:
            return Outcome("provider_unavailable", decision, **metadata)
        try:
            async with asyncio.timeout(3) as deadline:
                raw = await self._inference.extract(source)
            if deadline.expired():
                return Outcome("provider_timeout", decision, **metadata)
            extraction = parse_response(raw, source, vocabulary)
        except TimeoutError:
            return Outcome("provider_timeout", decision, **metadata)
        except (ValueError, RecursionError, ValidationError):
            return Outcome("invalid_provider_response", decision, **metadata)
        except Exception:
            return Outcome("provider_unavailable", decision, **metadata)
        candidate_values = {}
        for candidate in extraction.candidates:
            if candidate.value is not None:
                candidate_values.setdefault(candidate.fact, set()).add(candidate.value)
        if any(len(values) > 1 for values in candidate_values.values()):
            return Outcome("contradictory", decision, **metadata)
        proposal = extraction.proposal
        if proposal and len(set(state.questions) | set(proposal.follow_up_questions)) > 5:
            return Outcome("invalid_provider_response", decision, **metadata)
        if proposal:
            status = "lower_urgency_unauthorized" if proposal.level in ("HOME_CARE", "SEE_DOCTOR") else "triage_review_required"
        elif not extraction.candidates or any(c.value is None for c in extraction.candidates):
            status = "unresolved_extraction"
        else:
            status = "validated_candidates"
        return Outcome(status, decision, extraction=extraction, **metadata)
