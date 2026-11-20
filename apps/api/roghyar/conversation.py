"""Private, storage-free follow-up transitions over authoritative server state."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, model_validator

from .classification import (
    Category, ClassificationInput, ClassificationService, Classified, Language,
    Rejected, StrictContract, Unavailable,
)
from .scoped_session import Principal
from .triage import TriageDecision, TriageState

MAX_QUESTIONS = 5
MAX_EVENTS = 20
MAX_MESSAGES = 4
MAX_MESSAGE = 600
MAX_CONTEXT = 1800
LIFETIME = timedelta(minutes=15)
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
Phase = Literal[
    "new", "awaiting_answer", "answer_received", "clarification_required",
    "completed", "expired", "cancelled",
]
Route = Literal["none", "in_scope", "off_topic", "uncertain", "restricted"]
TERMINAL = ("completed", "expired", "cancelled")


class Question(StrictContract):
    key: Code
    options: Annotated[tuple[Code, ...], Field(min_length=2, max_length=8)]

    @model_validator(mode="after")
    def unique_options(self) -> Self:
        if len(set(self.options)) != len(self.options):
            raise ValueError("Duplicate option")
        return self


class Fact(StrictContract):
    question: Code
    option: Code | None
    provenance: Literal["direct", "correction", "suggestion", "unknown"]
    event_id: UUID
    supersedes: UUID | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.option is None) != (self.provenance == "unknown"):
            raise ValueError("Invalid fact value")
        if (self.supersedes is not None) != (self.provenance == "correction"):
            raise ValueError("Invalid correction provenance")
        return self


class Message(ClassificationInput):
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE)] = Field(repr=False)


class MemoryMessage(Message):
    event_id: UUID
    languages: Annotated[tuple[Language, ...], Field(max_length=3)]
    category: Category | None


class ConversationState(StrictContract):
    version: Literal[1] = 1
    conversation_id: UUID
    owner_kind: Literal["patient", "anonymous"]
    owner_subject: UUID = Field(repr=False)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    phase: Phase = "new"
    routing: Route = "none"
    events: Annotated[tuple[UUID, ...], Field(max_length=MAX_EVENTS)] = ()
    questions: Annotated[tuple[Code, ...], Field(max_length=MAX_QUESTIONS)] = ()
    pending: Code | None = None
    facts: Annotated[tuple[Fact, ...], Field(max_length=MAX_QUESTIONS)] = Field(default=(), repr=False)
    messages: Annotated[tuple[MemoryMessage, ...], Field(max_length=MAX_MESSAGES)] = Field(default=(), repr=False)
    languages: Annotated[tuple[Language, ...], Field(max_length=3)] = ()
    topic_event: UUID | None = None

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if (self.expires_at - self.created_at != LIFETIME or
                self.created_at.utcoffset() != timedelta(0) or self.expires_at.utcoffset() != timedelta(0)):
            raise ValueError("Invalid lifetime")
        for values in (self.events, self.questions, tuple(f.question for f in self.facts),
                       tuple(m.event_id for m in self.messages), self.languages):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate state reference")
        if (self.phase == "awaiting_answer") != (self.pending is not None):
            raise ValueError("Invalid pending question")
        if self.pending is not None and (not self.questions or self.pending != self.questions[-1]):
            raise ValueError("Unknown pending question")
        if any(f.question not in self.questions or f.event_id not in self.events or
               (f.supersedes is not None and (f.supersedes not in self.events or
                self.events.index(f.supersedes) >= self.events.index(f.event_id))) for f in self.facts):
            raise ValueError("Invalid fact reference")
        if (any(m.event_id not in self.events for m in self.messages) or
                (self.topic_event is not None and not any(m.event_id == self.topic_event and m.category not in
                 (None, "greeting", "off_topic", "unsafe_request") for m in self.messages)) or
                sum(len(m.text) for m in self.messages) > MAX_CONTEXT):
            raise ValueError("Invalid message context")
        if self.phase in TERMINAL and (self.questions or self.facts or self.messages or self.languages or
                                       self.routing != "none" or self.topic_event is not None):
            raise ValueError("Terminal state must be cleared")
        return self

    @property
    def reported_facts(self) -> tuple[Fact, ...]:
        """Unverified user reports for future AI-03 validation, never BE-03 observations."""
        return tuple(f for f in self.facts if f.provenance in ("direct", "correction"))


class Turn(StrictContract):
    conversation_id: UUID
    event_id: UUID
    revision: Annotated[int, Field(ge=0, le=MAX_EVENTS)]
    action: Literal["message", "ask", "answer", "correct", "complete", "cancel", "reset"]
    message: Message | None = None
    question: Code | None = None
    option: Code | None = None
    reference: UUID | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if ((self.action == "message") != (self.message is not None) or
                (self.action in ("ask", "answer", "correct")) != (self.question is not None) or
                (self.action == "correct" and self.option is None) or
                (self.option is not None and self.action not in ("answer", "correct")) or
                (self.reference is not None and self.action != "message")):
            raise ValueError("Invalid turn fields")
        return self


class Transition(StrictContract):
    state: ConversationState | None = Field(default=None, repr=False)
    reason: Literal[
        "applied", "expired", "invalid_state", "invalid_turn", "owner_mismatch", "conversation_mismatch",
        "duplicate", "stale_revision", "terminal", "turn_limit", "question_limit", "unapproved_question",
        "pending_question", "no_pending_question", "stale_question", "invalid_option", "missing_answer",
        "contradictory_answer", "context_limit", "missing_reference", "safety_handoff", "inference_unavailable",
        "unexpected_failure",
    ]
    inference_failure: Unavailable | None = None


class ConversationMemoryService:
    def __init__(self, classifier: ClassificationService | None = None, *,
                 reviewed_questions: tuple[Question, ...] = ()):
        # Only trusted composition may inject the reviewed catalog. Empty is the shipped default.
        catalog = {q.key: Question.model_validate(q.model_dump()) for q in reviewed_questions}
        if len(catalog) != len(reviewed_questions):
            raise ValueError("Duplicate question key")
        self._questions = catalog
        self._classifier = classifier if classifier is not None else ClassificationService()

    @staticmethod
    def start(owner: Principal, now: datetime) -> ConversationState:
        if owner.kind not in ("patient", "anonymous") or not isinstance(owner.subject, UUID):
            raise ValueError("Verified conversation principal required")
        if now.utcoffset() is None:
            raise ValueError("Aware server time required")
        now = now.astimezone(UTC)
        return ConversationState(conversation_id=uuid4(), owner_kind=owner.kind, owner_subject=owner.subject,
                                 created_at=now, expires_at=now + LIFETIME)

    async def apply(self, prior: object, payload: object, owner: Principal, now: datetime, *,
                    clinical_decision: TriageDecision | None = None) -> Transition:
        """Use the latest server-owned snapshot. Callers must serialize the read/apply/replace cycle."""
        try:
            state = ConversationState.model_validate(prior.model_dump() if isinstance(prior, ConversationState) else prior)
        except ValidationError:
            return Transition(reason="invalid_state")
        if (owner.kind, owner.subject) != (state.owner_kind, state.owner_subject):
            return Transition(reason="owner_mismatch")
        if now.utcoffset() is None or now < state.created_at:
            return Transition(reason="invalid_state")
        if clinical_decision is not None and clinical_decision.state in (
            TriageState.REVIEWED_EMERGENCY, TriageState.REVIEWED_URGENT, TriageState.URGENT_REVIEW_REQUIRED,
        ):
            return Transition(reason="safety_handoff")
        if now >= state.expires_at:
            return Transition(reason="expired", state=self._cleared(state, "expired"))
        try:
            turn = Turn.model_validate(payload)
        except ValidationError:
            return Transition(reason="invalid_turn")
        if turn.conversation_id != state.conversation_id:
            return Transition(reason="conversation_mismatch")
        if turn.event_id in state.events:
            return Transition(reason="duplicate")
        if turn.revision != len(state.events):
            return Transition(reason="stale_revision")
        if turn.action == "reset":
            return Transition(reason="applied", state=self.start(owner, now))
        if state.phase in TERMINAL:
            return Transition(reason="terminal")
        if turn.action in ("cancel", "complete"):
            if turn.action == "complete" and state.routing == "restricted":
                return Transition(reason="safety_handoff")
            if turn.action == "complete" and state.pending is not None:
                return Transition(reason="pending_question")
            return Transition(reason="applied", state=self._cleared(
                state, "cancelled" if turn.action == "cancel" else "completed",
                turn.event_id if len(state.events) < MAX_EVENTS else None,
            ))
        if len(state.events) == MAX_EVENTS:
            return Transition(reason="turn_limit")
        try:
            if turn.action == "message":
                return await self._message(state, turn)
            return self._question(state, turn)
        except Exception:
            return Transition(reason="unexpected_failure")

    @staticmethod
    def _cleared(state: ConversationState, phase: Phase, event_id: UUID | None = None) -> ConversationState:
        return ConversationState(
            conversation_id=state.conversation_id, owner_kind=state.owner_kind, owner_subject=state.owner_subject,
            created_at=state.created_at, expires_at=state.expires_at, phase=phase,
            events=state.events + ((event_id,) if event_id is not None else ()),
        )

    @staticmethod
    def _advance(state: ConversationState, turn: Turn, **changes) -> Transition:
        data = state.model_dump()
        data.update(changes, events=state.events + (turn.event_id,))
        return Transition(reason="applied", state=ConversationState.model_validate(data))

    async def _message(self, state: ConversationState, turn: Turn) -> Transition:
        if turn.reference is not None and turn.reference != state.topic_event:
            return Transition(reason="missing_reference")
        result = await self._classifier.classify(turn.message.model_dump())
        if isinstance(result, Unavailable):
            return Transition(reason="inference_unavailable", inference_failure=result)
        if isinstance(result, Rejected):
            return Transition(reason="invalid_turn")
        category = result.relevance_category if isinstance(result, Classified) else None
        route: Route = "uncertain"
        topic = state.topic_event
        if category == "unsafe_request":
            route = "restricted"
        elif category == "off_topic" and turn.reference is None:
            route = "off_topic"
        elif category not in (None, "off_topic"):
            route = "in_scope"
            if category != "greeting" and not (state.pending and topic):
                topic = turn.event_id
        if state.routing == "restricted":
            route = "restricted"
        messages = state.messages + (MemoryMessage(
            event_id=turn.event_id, text=turn.message.text, languages=result.languages, category=category,
        ),)
        while len(messages) > MAX_MESSAGES or sum(len(m.text) for m in messages) > MAX_CONTEXT:
            if messages[0].event_id in (topic, turn.reference):
                return Transition(reason="context_limit")
            messages = messages[1:]
        return self._advance(
            state, turn, messages=tuple(m.model_dump() for m in messages), topic_event=topic,
            languages=result.languages, routing=route,
            phase="awaiting_answer" if state.pending else ("clarification_required" if route == "uncertain" else state.phase),
        )

    def _question(self, state: ConversationState, turn: Turn) -> Transition:
        if state.routing == "restricted":
            return Transition(reason="safety_handoff")
        question = self._questions.get(turn.question)
        if turn.action == "ask":
            if state.pending is not None:
                return Transition(reason="pending_question")
            if len(state.questions) == MAX_QUESTIONS:
                return Transition(reason="question_limit")
            if question is None or turn.question in state.questions:
                return Transition(reason="unapproved_question")
            return self._advance(state, turn, questions=state.questions + (turn.question,),
                                 pending=turn.question, phase="awaiting_answer")
        old = next((fact for fact in state.facts if fact.question == turn.question), None)
        if turn.action == "answer":
            if state.pending is None:
                return Transition(reason="contradictory_answer" if old and old.option != turn.option else "no_pending_question")
            if state.pending != turn.question:
                return Transition(reason="stale_question")
            if old is not None:
                return Transition(reason="contradictory_answer")
            if turn.option is None:
                return Transition(reason="missing_answer")
        elif old is None or old.provenance not in ("direct", "correction") or old.option == turn.option:
            return Transition(reason="contradictory_answer")
        if question is None or turn.option not in question.options:
            return Transition(reason="invalid_option")
        fact = Fact(question=turn.question, option=turn.option,
                    provenance="correction" if old else "direct", event_id=turn.event_id,
                    supersedes=old.event_id if old else None)
        facts = tuple(item.model_dump() for item in state.facts if item.question != turn.question) + (fact.model_dump(),)
        pending = None if turn.action == "answer" else state.pending
        return self._advance(state, turn, facts=facts, pending=pending,
                             phase="awaiting_answer" if pending else "answer_received")
