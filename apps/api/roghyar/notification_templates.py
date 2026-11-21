"""Approved private notification and reminder content contracts."""
from dataclasses import dataclass
from datetime import datetime
from string import Formatter
from typing import Annotated, Literal
from unicodedata import category
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from .doctor_schedules import LOCAL
from .hospital_search import SearchModel
from .red_flags import canonical

Channel = Literal["sms", "whatsapp"]
Locale = Literal["en", "ur", "ps"]
Kind = Literal["booking", "cancelled", "rescheduled", "successor", "reminder24", "reminder2"]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Signature = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{128}$")]
ProviderMessage = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,100}$")]


class NotificationDenied(ValueError):
    pass


class Template(SearchModel):
    channel: Channel
    locale: Locale
    kind: Kind
    version: Literal[1]
    approved_text: Annotated[str, Field(min_length=1,max_length=300)] = Field(repr=False)
    provider_template: Identifier | None = None
    reviewer: Identifier
    expiry: AwareDatetime
    signature: Signature = Field(repr=False)

    @model_validator(mode="after")
    def checked_fields(self):
        if (self.channel == "whatsapp") != (self.provider_template is not None):
            raise ValueError("Unbound provider template")
        if any((category(c).startswith("C") and c not in "\u200c\u200d") or c in "<>" for c in self.approved_text):
            raise ValueError("Unsupported template content")
        if "://" in self.approved_text or any(field not in (None, "when") or spec or conversion
                for _, field, spec, conversion in Formatter().parse(self.approved_text)):
            raise ValueError("Unsupported template placeholder")
        return self

    def signing_bytes(self):
        return b"roghyar:appointment-notification-template:v1\0" + canonical(
            self.model_dump(mode="json",exclude={"signature"}))


class ClinicApproval(SearchModel):
    appointment: UUID
    hospital: UUID
    slot_start: AwareDatetime
    reviewer: Identifier
    recorded_at: AwareDatetime
    expiry: AwareDatetime
    signature: Signature = Field(repr=False)

    def signing_bytes(self):
        return b"roghyar:notification-clinic-reminder:v1\0" + canonical(
            self.model_dump(mode="json",exclude={"signature"}))


@dataclass(frozen=True)
class Signer:
    key: Ed25519PublicKey
    starts: datetime
    expires: datetime
    scopes: frozenset[tuple[str, str]]
    revoked: bool = False


def verify_artifact(artifact, signer, scope, now):
    if (signer is None or signer.revoked or scope not in signer.scopes
            or not signer.starts <= now < min(signer.expires, artifact.expiry)):
        raise NotificationDenied("unapproved_artifact")
    try:
        signer.key.verify(bytes.fromhex(artifact.signature), artifact.signing_bytes())
    except InvalidSignature:
        raise NotificationDenied("unapproved_artifact") from None


class Catalog:
    def __init__(self, templates=(), template_keys=None, clinic_keys=None):
        self.templates = tuple(Template.model_validate(item.model_dump()) for item in templates)
        self.template_keys, self.clinic_keys = dict(template_keys or {}), dict(clinic_keys or {})

    def render(self, job, now):
        rows = [item for item in self.templates
                if (item.channel,item.locale,item.kind,item.version) == (job.channel,job.locale,job.kind,job.version)]
        if len(rows) != 1:
            raise NotificationDenied("unapproved_artifact")
        artifact, = rows
        verify_artifact(artifact, self.template_keys.get(artifact.reviewer),
                        (artifact.channel, artifact.locale), now)
        when = job.slot_start.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M PKT")
        result = artifact.approved_text.format(when=when)
        if not result.strip() or len(result.encode()) > 1200:
            raise NotificationDenied("unapproved_artifact")
        parameters = (when,) if any(f == "when" for _, f, _, _ in Formatter().parse(artifact.approved_text)) else ()
        return result, artifact.provider_template, parameters

    def approve_reminders(self, approval, now):
        approval = ClinicApproval.model_validate(approval.model_dump())
        if not approval.recorded_at <= now < approval.expiry <= approval.slot_start:
            raise NotificationDenied("unapproved_artifact")
        verify_artifact(approval, self.clinic_keys.get(approval.reviewer),
                        ("reminder", str(approval.hospital)), now)
        return approval
