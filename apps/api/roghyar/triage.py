"""Private triage precedence over the signed BE-03 rule snapshot."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints
from sqlalchemy import Engine

from .red_flags import (
    IDENTIFIER_ADAPTER, MAX_RULES, Category, Digest, Evaluation, Identifier, Match, PostgresRuleRepository,
    RedFlagRuleEngine, ReviewerKey, Status, StrictModel, canonical,
)

DIGEST_RE = re.compile(r"[0-9a-f]{64}")


class GuidanceLevel(StrEnum):
    EMERGENCY = "EMERGENCY"
    URGENT = "URGENT"
    SEE_A_DOCTOR = "SEE A DOCTOR"
    HOME_CARE = "HOME CARE"


class TriageState(StrEnum):
    REVIEWED_EMERGENCY = "reviewed_emergency"
    REVIEWED_URGENT = "reviewed_urgent"
    URGENT_REVIEW_REQUIRED = "urgent_review_required"
    NO_REVIEWED_MATCH = "no_reviewed_match"
    FURTHER_ASSESSMENT_REQUIRED = "further_assessment_required"
    RULES_UNAVAILABLE = "rules_unavailable"
    EVALUATION_FAILURE = "evaluation_failure"


FALLBACK_STATES = {
    Status.NO_MATCH: TriageState.NO_REVIEWED_MATCH,
    Status.INCOMPLETE_EVIDENCE: TriageState.FURTHER_ASSESSMENT_REQUIRED,
    Status.NO_APPROVED_RULES: TriageState.RULES_UNAVAILABLE,
    Status.INVALID_CONFIGURATION: TriageState.RULES_UNAVAILABLE,
    Status.EVALUATION_FAILURE: TriageState.EVALUATION_FAILURE,
}


@dataclass(frozen=True, slots=True)
class TriageDecision:
    # The BE-03 status doubles as the machine-readable reason code.
    reason: Status
    evaluated_at: datetime
    fingerprint: str | None = None
    matches: tuple[Match, ...] = ()
    unresolved_evidence: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.reason) is not Status or self.evaluated_at.utcoffset() != timedelta(0):
            raise ValueError("Invalid triage metadata")
        if self.fingerprint is not None and not DIGEST_RE.fullmatch(self.fingerprint):
            raise ValueError("Invalid rule snapshot fingerprint")
        if self.reason in (Status.MATCH, Status.NO_MATCH, Status.INCOMPLETE_EVIDENCE):
            if self.fingerprint is None:
                raise ValueError("A verified rule snapshot is required")
        elif self.fingerprint is not None or self.unresolved_evidence:
            raise ValueError("Unavailable evaluations cannot carry decision evidence")
        if self.reason == Status.NO_MATCH and self.unresolved_evidence:
            raise ValueError("Unresolved evidence requires further assessment")
        if bool(self.matches) != (self.reason == Status.MATCH):
            raise ValueError("Evaluation status and matches disagree")
        if len(self.matches) > MAX_RULES or len({match.rule_code for match in self.matches}) != len(self.matches):
            raise ValueError("Conflicting matched rule versions")
        for match in self.matches:
            if (
                not isinstance(match.category, Category) or match.priority not in ("emergency", "urgent")
                or type(match.version) is not int or not 1 <= match.version <= 2147483647
                or not DIGEST_RE.fullmatch(match.warning_sha256)
            ):
                raise ValueError("Invalid matched rule")
            for identifier in (match.rule_code, match.warning_ref):
                IDENTIFIER_ADAPTER.validate_python(identifier, strict=True)

    @property
    def state(self) -> TriageState:
        if self.reason != Status.MATCH:
            return FALLBACK_STATES[self.reason]
        if any(match.priority == "emergency" for match in self.matches):
            return TriageState.REVIEWED_EMERGENCY
        if self.unresolved_evidence:
            return TriageState.URGENT_REVIEW_REQUIRED
        return TriageState.REVIEWED_URGENT

    @property
    def urgency_floor(self) -> Literal[GuidanceLevel.EMERGENCY, GuidanceLevel.URGENT] | None:
        if self.state == TriageState.REVIEWED_EMERGENCY:
            return GuidanceLevel.EMERGENCY
        if self.state in (TriageState.REVIEWED_URGENT, TriageState.URGENT_REVIEW_REQUIRED):
            return GuidanceLevel.URGENT
        return None


Language = Literal["en", "ur", "ps"]
WarningText = Annotated[str, StringConstraints(min_length=1, max_length=2000, pattern=r"\S")]


class WarningSelection(StrictModel):
    fingerprint: Digest
    rule_code: Identifier
    version: int = Field(ge=1, le=2147483647)
    language: Language


class WarningArtifact(StrictModel):
    reference: Identifier
    translations: Annotated[dict[Language, WarningText], Field(min_length=1, max_length=3)]

    def sha256(self) -> str:
        return hashlib.sha256(
            b"roghyar:warning:v1\x00" + canonical(self.model_dump(mode="json"))
        ).hexdigest()


def _from_evaluation(evaluation: Evaluation) -> TriageDecision:
    fingerprint = evaluation.fingerprint
    if evaluation.status == Status.EVALUATION_FAILURE:
        # Never propagate a partial result after an evaluation failure.
        return TriageDecision(Status.EVALUATION_FAILURE, evaluation.evaluated_at)
    return TriageDecision(
        reason=evaluation.status,
        evaluated_at=evaluation.evaluated_at,
        fingerprint=fingerprint,
        matches=tuple(sorted(
            evaluation.matches, key=lambda m: (m.priority != "emergency", m.rule_code, m.version),
        )),
        unresolved_evidence=tuple(sorted(set(evaluation.unresolved_evidence))),
    )


class TriageService:
    def __init__(self, engine: Engine, reviewer_keys: Mapping[str, ReviewerKey] | None = None,
                 warning_repository: Callable[[str, str], object | None] | None = None):
        self._rules = RedFlagRuleEngine(PostgresRuleRepository(engine), reviewer_keys)
        self._warning_repository = warning_repository

    def evaluate(self, evidence: object) -> TriageDecision:
        # Only the scoped repository and BE-03 verifier can supply an evaluation.
        try:
            return _from_evaluation(self._rules.evaluate(evidence))
        except Exception:
            return TriageDecision(Status.EVALUATION_FAILURE, datetime.now(timezone.utc))

    def resolve_warning(self, evidence: object, selection: object) -> str | None:
        """Return exact reviewed content for the current decision and selected locale."""
        if self._warning_repository is None:
            return None
        try:
            if isinstance(selection, WarningSelection):
                selection = selection.model_dump()
            request = WarningSelection.model_validate(selection)
            decision = self.evaluate(evidence)
            if decision.fingerprint != request.fingerprint or decision.urgency_floor is None:
                return None
            for match in decision.matches:
                if (match.rule_code, match.version, match.priority) != (
                    request.rule_code, request.version, decision.urgency_floor.value.lower(),
                ):
                    continue
                raw = self._warning_repository(match.warning_ref, match.warning_sha256)
                if isinstance(raw, WarningArtifact):
                    raw = raw.model_dump()
                artifact = WarningArtifact.model_validate(raw)
                if artifact.reference == match.warning_ref and artifact.sha256() == match.warning_sha256:
                    return artifact.translations.get(request.language)
        except Exception:
            # A failed content read cannot replace the rules decision or return other text.
            return None
        return None
