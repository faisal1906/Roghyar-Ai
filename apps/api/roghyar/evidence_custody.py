"""Private, process-local evidence custody for disposable server compositions."""
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from threading import Lock
from uuid import UUID

from .conversation import ConversationState, MAX_EVENTS
from .evidence_authority import EvidenceName
from .red_flags import canonical
from .scoped_session import Principal

FIELDS = ("age", "pregnancy", "allergies", "conditions", "current_medicines")
TERMINAL = ("completed", "expired", "cancelled")


def fingerprint(model) -> str:
    return sha256(canonical(model.model_dump(mode="json"))).hexdigest()


@dataclass(frozen=True, slots=True)
class CurrentField:
    name: EvidenceName
    event: UUID
    binding: str
    predecessor: UUID | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    owner: Principal
    conversation: UUID
    revision: int
    state_digest: str
    vocabulary: str
    expires_at: datetime
    fields: tuple[CurrentField, ...]
    history: tuple[CurrentField, ...] = ()

    def matches(self, state, evidence, now):
        return (
            now < self.expires_at
            and fingerprint(state) == self.state_digest
            and evidence.revision == self.revision
            and evidence.vocabulary == self.vocabulary
            and all(field.event == getattr(evidence, field.name).event
                    and field.binding == field_binding(evidence, field.name) for field in self.fields)
        )


def field_binding(evidence, name):
    return sha256(canonical({
        "value": getattr(evidence.values, name),
        "source": getattr(evidence, name).model_dump(mode="json"),
    })).hexdigest()


class EvidenceConflict(ValueError):
    """Safe rejection of stale or invalid state."""


class InMemoryEvidenceCustody:
    """One disposable conversation. No persistent or cross-process guarantees."""

    def __init__(self, owner: Principal, state: ConversationState, evidence, *, assessment_event: UUID, now: datetime):
        self._lock = Lock()
        if ((owner.kind, owner.subject) != (state.owner_kind, state.owner_subject)
                or owner.subject is None or not state.created_at <= now < state.expires_at
                or state.phase in TERMINAL or evidence.revision != len(state.events)):
            raise EvidenceConflict("invalid_initial_context")
        fields = tuple(CurrentField(name, getattr(evidence, name).event, field_binding(evidence, name))
                       for name in FIELDS)
        if any(field.event is None or field.event not in (*state.events, assessment_event)
               or getattr(evidence, field.name).superseded for field in fields):
            raise EvidenceConflict("invalid_initial_evidence")
        self._current = EvidenceSnapshot(owner, state.conversation_id, evidence.revision, fingerprint(state),
                                         evidence.vocabulary, state.expires_at, fields)
        self._assessment_event = assessment_event

    def current(self, owner: Principal, conversation: UUID) -> EvidenceSnapshot | None:
        with self._lock:
            current = self._current
            return current if (owner, conversation) == (current.owner, current.conversation) else None

    def correct(self, owner: Principal, state: ConversationState, *, expected_revision: int,
                field: EvidenceName, predecessor: UUID, event: UUID, evidence, now: datetime) -> ConversationState:
        """Commit one server-authorized field correction against the exact current snapshot."""
        with self._lock:
            current = self._current
            if ((owner, state.conversation_id, expected_revision) !=
                    (current.owner, current.conversation, current.revision)
                    or fingerprint(state) != current.state_digest
                    or not state.created_at <= now < current.expires_at
                    or state.phase in TERMINAL or current.revision >= MAX_EVENTS
                    or evidence.revision != expected_revision + 1 or evidence.vocabulary != current.vocabulary):
                raise EvidenceConflict("stale_context")
            if not isinstance(event, UUID) or event in (*state.events, self._assessment_event):
                raise EvidenceConflict("replayed_event")
            previous = next((entry for entry in current.fields if entry.name == field), None)
            if previous is None or previous.event != predecessor:
                raise EvidenceConflict("wrong_predecessor")
            source = getattr(evidence, field)
            if (source.event != event or source.superseded
                    or any(entry.name != field and entry.binding != field_binding(evidence, entry.name)
                           for entry in current.fields)):
                raise EvidenceConflict("invalid_correction")
            updated = ConversationState.model_validate(state.model_dump() | {"events": (*state.events, event)})
            replacement = CurrentField(field, event, field_binding(evidence, field), predecessor)
            self._current = replace(current, revision=evidence.revision, state_digest=fingerprint(updated),
                                    fields=tuple(replacement if item == previous else item for item in current.fields),
                                    history=(*current.history, previous))
            return updated
