"""Private evidence issuance. Trust anchors are supplied by the server."""
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints

from .classification import StrictContract
from .red_flags import Digest, Identifier, canonical

EvidenceName = Literal["age", "pregnancy", "allergies", "conditions", "current_medicines"]
DOMAIN = b"roghyar:evidence-issuance:v1\x00"


@dataclass(frozen=True)
class EvidenceIssuer:
    identity: str
    public_key: Ed25519PublicKey
    fields: frozenset[EvidenceName]
    valid_from: datetime
    valid_until: datetime
    provenance: str
    revoked: bool = False


class EvidenceAttestation(StrictContract):
    schema_version: Literal[1] = 1
    owner: UUID
    owner_kind: Literal["anonymous", "patient"]
    conversation: UUID
    event: UUID
    revision: Annotated[int, Field(ge=0, le=20)]
    field: EvidenceName
    value_digest: Digest
    vocabulary: Identifier
    issuer: Identifier
    key_id: Identifier
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")] = Field(repr=False)

    def signing_bytes(self) -> bytes:
        return DOMAIN + canonical(self.model_dump(mode="json", exclude={"signature"}))

    def verified_by(self, authority: EvidenceIssuer | None, now: datetime) -> bool:
        if (authority is None or authority.revoked or authority.identity != self.issuer
                or not authority.provenance or self.field not in authority.fields
                or not authority.valid_from <= self.issued_at <= now < min(self.expires_at, authority.valid_until)):
            return False
        try:
            authority.public_key.verify(bytes.fromhex(self.signature), self.signing_bytes())
        except InvalidSignature:
            return False
        return True
