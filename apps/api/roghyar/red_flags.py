"""Private, deterministic rule evaluation. Raw symptoms never enter this interface."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator
from sqlalchemy import Engine, text

MAX_RULES = 64
MAX_BYTES = 8192
MAX_DEPTH = 4
MAX_NODES = 32
DOMAIN = b"roghyar:red-flag-review:v1\x00"
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Fact = bool | Annotated[int, Field(ge=-1000000, le=1000000)]


class Category(StrEnum):
    HEART = "heart"
    BREATHING = "breathing"
    BRAIN_STROKE = "brain_stroke"
    BLEEDING = "bleeding"
    CONSCIOUSNESS = "consciousness"
    PREGNANCY = "pregnancy"
    CHILDREN_INFANTS = "children_infants"
    POISONING_INJURY = "poisoning_injury"


class Status(StrEnum):
    MATCH = "match"
    NO_MATCH = "no_match"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    NO_APPROVED_RULES = "no_approved_rules"
    INVALID_CONFIGURATION = "invalid_configuration"
    EVALUATION_FAILURE = "evaluation_failure"


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)


class Observation(StrictModel):
    fact: Identifier
    provenance: Literal["reported", "verified", "untrusted", "unsupported", "unknown"]
    value: Fact | None = None

    @model_validator(mode="after")
    def check_value(self):
        reliable = self.provenance in ("reported", "verified")
        if reliable != (self.value is not None):
            raise ValueError("Inconsistent observation")
        return self


class Evidence(StrictModel):
    observations: Annotated[list[Observation], Field(max_length=128)]


class Comparison(StrictModel):
    fact: Identifier
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte"]
    value: Fact

    @model_validator(mode="after")
    def check_operator(self):
        if isinstance(self.value, bool) and self.op not in ("eq", "ne"):
            raise ValueError("Unsupported comparison")
        return self


class Group(StrictModel):
    op: Literal["all", "any"]
    conditions: Annotated[list[Comparison | Group], Field(min_length=1, max_length=8)]


Group.model_rebuild()


class Attestation(StrictModel):
    key_id: Identifier
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]


class Pattern(StrictModel):
    schema_version: Literal[1]
    warning_ref: Identifier
    warning_sha256: Digest
    include: Comparison | Group
    exclude: Comparison | Group | None = None
    attestation: Attestation


@dataclass(frozen=True)
class RuleRecord:
    rule_code: str
    version: int
    category: str
    level: str
    pattern: object
    review_status: str
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    active: bool


@dataclass(frozen=True)
class ReviewerKey:
    reviewer: UUID
    public_key: Ed25519PublicKey
    categories: frozenset[Category]
    valid_from: datetime
    valid_until: datetime


@dataclass(frozen=True)
class Match:
    rule_code: str
    version: int
    category: Category
    priority: Literal["emergency", "urgent"]
    evidence: tuple[str, ...]
    warning_ref: str
    warning_sha256: str


@dataclass(frozen=True)
class Evaluation:
    status: Status
    evaluated_at: datetime
    fingerprint: str | None = None
    matches: tuple[Match, ...] = ()
    unresolved_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    value: bool | None
    evidence: frozenset[str]


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def attestation_payload(record: RuleRecord, pattern: Pattern) -> bytes:
    if record.reviewed_by is None or record.reviewed_at is None or record.reviewed_at.utcoffset() is None:
        raise ValueError("Review metadata is missing")
    return DOMAIN + canonical({
        "rule_code": record.rule_code, "version": record.version, "category": record.category,
        "priority": record.level, "reviewed_by": str(record.reviewed_by),
        "reviewed_at": record.reviewed_at.astimezone(timezone.utc).isoformat(),
        "key_id": pattern.attestation.key_id,
        "pattern": pattern.model_dump(exclude={"attestation"}, mode="json"),
    })


def validate_pattern(value: object) -> Pattern:
    if type(value) is not dict or type(value.get("schema_version")) is not int:
        raise ValueError("Invalid rule definition")
    pending = [(value.get("include"), 1)]
    if value.get("exclude") is not None:
        pending.append((value["exclude"], 1))
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if type(node) is not dict or depth > MAX_DEPTH or count > MAX_NODES:
            raise ValueError("Invalid rule definition or rule limit exceeded")
        if node.get("op") in ("all", "any"):
            children = node.get("conditions")
            if type(children) is not list or not 1 <= len(children) <= 8:
                raise ValueError("Invalid group")
            pending.extend((child, depth + 1) for child in children)
    pattern = Pattern.model_validate(value)
    if len(canonical(pattern.model_dump(mode="json"))) > MAX_BYTES:
        raise ValueError("Rule size limit exceeded")
    return pattern


def normalize_evidence(evidence: Evidence) -> dict[str, Fact | None]:
    facts: dict[str, Fact | None] = {}
    for item in evidence.observations:
        if item.fact in facts:
            previous = facts[item.fact]
            if type(previous) is not type(item.value) or previous != item.value:
                facts[item.fact] = None
        else:
            facts[item.fact] = item.value
    return facts


def combine(op: Literal["all", "any"], decisions: list[Decision]) -> Decision:
    deciding = op == "any"
    relevant = [item for item in decisions if item.value is deciding]
    if relevant:
        value = deciding
    else:
        relevant = [item for item in decisions if item.value is None]
        value = None if relevant else not deciding
        relevant = relevant or decisions
    return Decision(value, frozenset().union(*(item.evidence for item in relevant)))


def decide(node: Comparison | Group, facts: Mapping[str, Fact | None]) -> Decision:
    if isinstance(node, Group):
        return combine(node.op, [decide(child, facts) for child in node.conditions])
    actual = facts.get(node.fact)
    if type(actual) is not type(node.value):
        return Decision(None, frozenset({node.fact}))
    value = {
        "eq": lambda: actual == node.value, "ne": lambda: actual != node.value,
        "lt": lambda: actual < node.value, "lte": lambda: actual <= node.value,
        "gt": lambda: actual > node.value, "gte": lambda: actual >= node.value,
    }[node.op]()
    return Decision(value, frozenset({node.fact}))


class RedFlagRuleEngine:
    def __init__(self, repository: Callable[[], list[RuleRecord]],
                 reviewer_keys: Mapping[str, ReviewerKey] | None = None):
        self._repository = repository
        self._keys = dict(reviewer_keys or {})

    def evaluate(self, evidence: object) -> Evaluation:
        now = datetime.now(timezone.utc)
        try:
            rows = self._repository()
        except Exception:
            return Evaluation(Status.EVALUATION_FAILURE, now)

        try:
            if len(rows) > MAX_RULES:
                raise ValueError("Rule count exceeded")
            rules = {}
            for row in rows:
                if not row.active:
                    continue
                if row.review_status != "approved" or row.rule_code in rules:
                    raise ValueError("Conflicting active rule state")
                IDENTIFIER_ADAPTER.validate_python(row.rule_code, strict=True)
                if type(row.version) is not int or not 1 <= row.version <= 2147483647:
                    raise ValueError("Invalid rule version")
                category = Category(row.category)
                if row.level not in ("urgent", "emergency"):
                    raise ValueError("Invalid priority")
                pattern = validate_pattern(row.pattern)
                payload = attestation_payload(row, pattern)
                key = self._keys.get(pattern.attestation.key_id)
                if (
                    key is None or row.reviewed_by != key.reviewer or category not in key.categories
                    or not key.valid_from <= row.reviewed_at <= now < key.valid_until
                ):
                    raise ValueError("Review authority unavailable")
                key.public_key.verify(bytes.fromhex(pattern.attestation.signature), payload)
                rules[row.rule_code] = (row, category, pattern, hashlib.sha256(payload).hexdigest())
            if not rules:
                return Evaluation(Status.NO_APPROVED_RULES, now)
            ordered = sorted(rules.values(), key=lambda item: item[0].rule_code)
            fingerprint = hashlib.sha256(canonical([digest for _, _, _, digest in ordered])).hexdigest()
        except (ValueError, TypeError, AttributeError, InvalidSignature, RecursionError):
            return Evaluation(Status.INVALID_CONFIGURATION, now)
        except Exception:
            return Evaluation(Status.EVALUATION_FAILURE, now)

        try:
            # Revalidate even when given a model instance to avoid trusting a mutated model.
            if isinstance(evidence, Evidence):
                evidence = evidence.model_dump()
            facts = normalize_evidence(Evidence.model_validate(evidence))
        except (ValueError, TypeError):
            return Evaluation(Status.INCOMPLETE_EVIDENCE, now, fingerprint)
        except Exception:
            return Evaluation(Status.EVALUATION_FAILURE, now, fingerprint)

        matches = []
        unresolved = set()
        try:
            for row, category, pattern, _ in ordered:
                decision = decide(pattern.include, facts)
                if pattern.exclude is not None:
                    excluded = decide(pattern.exclude, facts)
                    inverse = None if excluded.value is None else not excluded.value
                    decision = combine("all", [decision, Decision(inverse, excluded.evidence)])
                if decision.value is True:
                    matches.append(Match(row.rule_code, row.version, category, row.level,
                                         tuple(sorted(decision.evidence)), pattern.warning_ref,
                                         pattern.warning_sha256))
                elif decision.value is None:
                    unresolved.update(decision.evidence)
        except Exception:
            return Evaluation(Status.EVALUATION_FAILURE, now, fingerprint)
        status = Status.MATCH if matches else Status.INCOMPLETE_EVIDENCE if unresolved else Status.NO_MATCH
        return Evaluation(status, now, fingerprint, tuple(matches), tuple(sorted(unresolved)))


class PostgresRuleRepository:
    """Read a single, size-limited snapshot using the normal restricted runtime login."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def __call__(self) -> list[RuleRecord]:
        from .scoped_session import CredentialVerifier, scoped_session

        with scoped_session(self._engine, CredentialVerifier(None), None) as session:
            rows = session.execute(text("""
                SELECT rule_code, version, category, level, review_status, reviewed_by, reviewed_at, active,
                  CASE WHEN octet_length(pattern::text) <= :wire_bytes THEN pattern END AS pattern
                FROM roghyar.red_flag_rules
                WHERE active AND review_status = 'approved'
                ORDER BY rule_code, version
                LIMIT :limit
            """), {"wire_bytes": MAX_BYTES, "limit": MAX_RULES + 1})
            return [RuleRecord(**row) for row in rows.mappings()]
