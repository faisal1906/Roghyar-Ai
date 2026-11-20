"""Private guidance orchestration over deterministic safety and signed content."""

import hashlib
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from pydantic import AwareDatetime, Field, StringConstraints

from .classification import Language, StrictContract, Unavailable
from .conversation import ConversationState
from .red_flags import Digest, Identifier, Status, canonical
from .reply_writer import Reply, ReplyWriterService, ReviewerAuthority
from .scoped_session import Principal
from .structured_triage import StructuredTriageService, Vocabulary
from .triage import GuidanceLevel, TriageDecision, TriageService, TriageState

DOMAIN = b"roghyar:lower-guidance:v1\x00"


class LowerApproval(StrictContract):
    schema_version: Literal[1]
    policy: Identifier
    version: Annotated[int, Field(ge=1)]
    reviewer: Identifier
    key_id: Identifier
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    level: Literal[GuidanceLevel.SEE_A_DOCTOR, GuidanceLevel.HOME_CARE]
    conversation: UUID
    event: UUID
    revision: Annotated[int, Field(ge=0)]
    vocabulary_version: Annotated[int, Field(ge=1)]
    snapshot: Digest
    evidence_digest: Digest
    screening_review: Identifier
    content_id: Identifier
    content_version: Annotated[int, Field(ge=1)]
    content_digest: Digest
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]

    def signing_bytes(self) -> bytes:
        return DOMAIN + canonical(self.model_dump(mode="json", exclude={"signature"}))


class GuidanceResult(StrictContract):
    status: Literal[
        "reviewed_emergency", "reviewed_urgent", "urgent_review_required", "further_assessment_required",
        "rules_unavailable", "invalid_rule_configuration", "evaluation_failure", "missing_vocabulary", "invalid_context",
        "no_lower_policy", "invalid_policy", "policy_mismatch", "insufficient_evidence",
        "missing_content", "authorized_lower",
    ]
    level: GuidanceLevel | None = None
    floor: GuidanceLevel | None = None
    requires_assessment: bool = True
    reply: Reply | None = None
    decision: TriageDecision | None = Field(default=None, repr=False)
    medication_status: Literal["unavailable"] = "unavailable"


class GuidanceFlowService:
    def __init__(self, triage: TriageService, vocabulary: Vocabulary | None = None, *,
                 writer: ReplyWriterService | None = None,
                 policy_keys: dict[str, ReviewerAuthority] | None = None,
                 active_policies: frozenset[tuple[str, int]] = frozenset()):
        self._bridge = StructuredTriageService(triage, vocabulary)
        self._writer = writer if writer is not None else ReplyWriterService(triage=triage)
        self._policy_keys = dict(policy_keys or {})
        self._active_policies = frozenset(active_policies)

    async def evaluate(self, *, owner: Principal, state: ConversationState, revision: int,
                       now: datetime, assessment: object, event_id: UUID, locale: Language,
                       approval: object = None) -> GuidanceResult:
        """Assessment and state come from owner-checked server custody, never model output."""
        if now.utcoffset() != timedelta(0) or locale not in ("en", "ur", "ps"):
            return GuidanceResult(status="invalid_context")
        outcome = await self._bridge.evaluate(owner=owner, state=state, revision=revision, now=now,
                                              assessment=assessment, event_id=event_id)
        decision = outcome.decision
        if decision is None:
            status = {"invalid_input": "invalid_context", "missing_vocabulary": "missing_vocabulary"}
            return GuidanceResult(status=status.get(outcome.status, "evaluation_failure"))
        safety = decision.state
        if decision.urgency_floor is not None:
            reply = self._writer.render(classification=Unavailable(reason_code="provider_unconfigured"),
                                        outcome=outcome, locale=locale, now=now)
            unresolved = safety == TriageState.URGENT_REVIEW_REQUIRED
            return GuidanceResult(status=safety.value, floor=decision.urgency_floor,
                                  level=None if unresolved else decision.urgency_floor,
                                  requires_assessment=unresolved, reply=reply, decision=decision)
        if decision.reason == Status.INVALID_CONFIGURATION:
            return GuidanceResult(status="invalid_rule_configuration", decision=decision)
        if safety != TriageState.NO_REVIEWED_MATCH:
            return GuidanceResult(status=safety.value, decision=decision)
        if outcome.status != "provider_unavailable" or any(r.observation.value is None for r in outcome.reports):
            return GuidanceResult(status="insufficient_evidence", decision=decision)
        if approval is None or not self._active_policies:
            return GuidanceResult(status="no_lower_policy", decision=decision)
        try:
            policy = LowerApproval.model_validate(approval.model_dump() if isinstance(approval, LowerApproval) else approval)
            authority = self._policy_keys.get(policy.key_id)
            if (authority is None or authority.revoked or authority.reviewer != policy.reviewer
                    or "clinical" not in authority.scopes
                    or not authority.valid_from <= policy.reviewed_at <= now < min(authority.valid_until, policy.expires_at)):
                return GuidanceResult(status="invalid_policy", decision=decision)
            authority.key.verify(bytes.fromhex(policy.signature), policy.signing_bytes())
        except (ValueError, InvalidSignature):
            return GuidanceResult(status="invalid_policy", decision=decision)
        if (policy.policy, policy.version) not in self._active_policies:
            return GuidanceResult(status="policy_mismatch", decision=decision)
        if not outcome.reports:
            return GuidanceResult(status="insufficient_evidence", decision=decision)
        digest = hashlib.sha256(canonical({
            "assessment": self._assessment_data(assessment),
            "owner": {"kind": owner.kind, "subject": str(owner.subject)},
            "reports": [{"observation": r.observation.model_dump(mode="json"),
                         "events": [str(e) for e in r.events], "supersedes": [str(e) for e in r.supersedes]}
                        for r in outcome.reports],
        })).hexdigest()
        if (policy.conversation, policy.event, policy.revision, policy.vocabulary_version, policy.snapshot,
                policy.evidence_digest) != (state.conversation_id, event_id, revision, outcome.vocabulary_version,
                                            decision.fingerprint, digest):
            return GuidanceResult(status="policy_mismatch", decision=decision)
        intent = "home_care" if policy.level == GuidanceLevel.HOME_CARE else "see_doctor"
        reply = self._writer.render_lower_artifact(intent, locale, now, policy.content_id,
                                                   policy.content_version, policy.content_digest)
        if reply.status != "rendered":
            return GuidanceResult(status="missing_content", reply=reply, decision=decision)
        return GuidanceResult(status="authorized_lower", level=policy.level, requires_assessment=False,
                              reply=reply, decision=decision)

    @staticmethod
    def _assessment_data(assessment: object) -> dict:
        from .assessment import validate_assessment

        return validate_assessment(assessment).model_dump(mode="json")
