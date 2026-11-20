"""Immutable reviewed home-care snapshots and exact version lookup."""

from datetime import timedelta
import hashlib
from typing import Annotated, Literal, Self

from cryptography.exceptions import InvalidSignature
from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, field_validator, model_validator

from .classification import Language, StrictContract
from .red_flags import Digest, Identifier, canonical
from .reply_writer import ReviewerAuthority, Translation

DOMAIN = b"roghyar:home-care-content:v1\x00"
MAX_BYTES = 32768
PositiveVersion = Annotated[int, Field(ge=1, le=2147483647)]
PlainText = Annotated[str, StringConstraints(min_length=1, max_length=600)]


class HomeCareVariant(StrictContract):
    locale: Language
    review_ref: Identifier
    summary: PlainText = Field(repr=False)
    steps: Annotated[tuple[PlainText, ...], Field(min_length=1, max_length=8, repr=False)]
    see_doctor_if: Annotated[tuple[PlainText, ...], Field(min_length=1, max_length=8, repr=False)]

    @field_validator("summary", "steps", "see_doctor_if")
    @classmethod
    def plain_text(cls, value):
        for text in (value,) if isinstance(value, str) else value:
            Translation.safe_plain_text(text)
        return value


class ContentBinding(StrictContract):
    content_id: Identifier
    version: PositiveVersion
    digest: Digest


class HomeCareBody(StrictContract):
    schema_version: Literal[1]
    content_id: Identifier
    version: PositiveVersion
    level: Literal["HOME CARE"]
    category: Literal["non_medication"]
    condition: Identifier
    applicability: Identifier
    review_status: Literal["approved"]
    active: bool
    reviewer: Identifier
    key_id: Identifier
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    source_ref: Identifier
    followup_hours: Annotated[int, Field(ge=1, le=720)] | None = None
    variants: Annotated[tuple[HomeCareVariant, ...], Field(min_length=1, max_length=3, repr=False)]

    @model_validator(mode="after")
    def validate_body(self) -> Self:
        if len({v.locale for v in self.variants}) != len(self.variants):
            raise ValueError("Duplicate locale")
        if self.reviewed_at.utcoffset() != timedelta(0) or (self.expires_at is not None and
                (self.expires_at.utcoffset() != timedelta(0) or self.expires_at <= self.reviewed_at)):
            raise ValueError("Invalid review time range")
        if len(self.signing_bytes()) > MAX_BYTES:
            raise ValueError("Content exceeds byte limit")
        return self

    def signing_bytes(self) -> bytes:
        return DOMAIN + canonical(self.model_dump(mode="json"))


class SignedHomeCare(StrictContract):
    body: HomeCareBody
    digest: Digest
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")] = Field(repr=False)


class HomeCareSelection(StrictContract):
    binding: ContentBinding
    condition: Identifier
    applicability: Identifier
    locale: Language
    now: AwareDatetime


class HomeCareResult(StrictContract):
    status: Literal[
        "verified", "invalid_context", "empty", "unknown_content", "unknown_version", "inactive",
        "conflicting_active_versions", "invalid_artifact", "wrong_binding", "wrong_scope", "expired",
        "invalid_authority", "revoked_authority", "expired_authority", "invalid_signature", "missing_locale",
    ]
    binding: ContentBinding | None = None
    variant: HomeCareVariant | None = Field(default=None, repr=False)
    followup_hours: Annotated[int, Field(ge=1, le=720)] | None = None

    @model_validator(mode="after")
    def bound_result(self) -> Self:
        if self.status == "verified":
            if self.binding is None or self.variant is None:
                raise ValueError("Verified content requires an exact binding")
        elif any(v is not None for v in (self.binding, self.variant, self.followup_hours)):
            raise ValueError("Denied content cannot carry instructions")
        return self


class HomeCareContentRepository:
    """Immutable snapshot with no publication methods. GD-01 owns patient authorization."""

    def __init__(self, artifacts: tuple[SignedHomeCare, ...] = (), *,
                 authorities: dict[str, ReviewerAuthority] | None = None):
        if len(artifacts) > 128:
            raise ValueError("Repository limit exceeded")
        self._artifacts = tuple(SignedHomeCare.model_validate(a.model_dump()) for a in artifacts)
        identities = {(a.body.content_id, a.body.version) for a in self._artifacts}
        if len(identities) != len(self._artifacts):
            raise ValueError("Duplicate content revision")
        self._authorities = dict(authorities or {})

    def resolve(self, selection: HomeCareSelection) -> HomeCareResult:
        try:
            selection = HomeCareSelection.model_validate(selection.model_dump())
        except (ValidationError, AttributeError):
            return HomeCareResult(status="invalid_context")
        if selection.now.utcoffset() != timedelta(0):
            return HomeCareResult(status="invalid_context")
        if not self._artifacts:
            return HomeCareResult(status="empty")
        binding = selection.binding
        versions = [a for a in self._artifacts if a.body.content_id == binding.content_id]
        if not versions:
            return HomeCareResult(status="unknown_content")
        item = next((a for a in versions if a.body.version == binding.version), None)
        if item is None:
            return HomeCareResult(status="unknown_version")
        if sum(a.body.active for a in versions) > 1:
            return HomeCareResult(status="conflicting_active_versions")
        body = item.body
        if not body.active:
            return HomeCareResult(status="inactive")
        payload = body.signing_bytes()
        if hashlib.sha256(payload).hexdigest() != item.digest:
            return HomeCareResult(status="invalid_artifact")
        if item.digest != binding.digest:
            return HomeCareResult(status="wrong_binding")
        if (body.condition, body.applicability) != (selection.condition, selection.applicability):
            return HomeCareResult(status="wrong_scope")
        now = selection.now
        if body.expires_at is not None and now >= body.expires_at:
            return HomeCareResult(status="expired")
        authority = self._authorities.get(body.key_id)
        if authority is None or authority.reviewer != body.reviewer or "home_care" not in authority.scopes:
            return HomeCareResult(status="invalid_authority")
        if authority.revoked:
            return HomeCareResult(status="revoked_authority")
        if not authority.valid_from <= body.reviewed_at <= now < authority.valid_until:
            return HomeCareResult(status="expired_authority")
        try:
            authority.key.verify(bytes.fromhex(item.signature), payload)
        except InvalidSignature:
            return HomeCareResult(status="invalid_signature")
        variant = next((v for v in body.variants if v.locale == selection.locale), None)
        if variant is None:
            return HomeCareResult(status="missing_locale")
        return HomeCareResult(status="verified", binding=binding, variant=variant,
                              followup_hours=body.followup_hours)
