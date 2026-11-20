"""Private, independently signed authorities for synthetic medication screening."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, model_validator

from .classification import StrictContract
from .red_flags import Digest, Identifier, canonical

Scope = Literal["product_identity", "ingredient_completeness", "interaction_set"]
Version = Annotated[int, Field(ge=1, le=2147483647)]


def fingerprint(value) -> str:
    return sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class ProductReviewer:
    identity: str
    key: Ed25519PublicKey
    scope: Scope
    valid_from: datetime
    valid_until: datetime
    revoked: bool = False


class SignedRecord(StrictContract):
    schema_version: Literal[1]
    vocabulary: Identifier
    reviewer: Identifier
    key_id: Identifier
    reviewed_at: AwareDatetime
    expires_at: AwareDatetime
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")] = Field(repr=False)

    def signing_bytes(self) -> bytes:
        domain = f"roghyar:{self.scope}:v1".encode() + b"\x00"
        return domain + canonical(self.model_dump(mode="json", exclude={"signature"}))

    def verified(self, keys: Mapping[str, ProductReviewer], now: datetime) -> bool:
        reviewer = keys.get(self.key_id)
        if (reviewer is None or reviewer.revoked or reviewer.identity != self.reviewer
                or reviewer.scope != self.scope
                or not reviewer.valid_from <= self.reviewed_at <= now < min(self.expires_at, reviewer.valid_until)):
            return False
        try:
            reviewer.key.verify(bytes.fromhex(self.signature), self.signing_bytes())
        except InvalidSignature:
            return False
        return True

    def binding(self) -> str:
        return sha256(self.signing_bytes()).hexdigest()


class Formulation(StrictContract):
    product: Identifier
    formulation: Identifier
    product_version: Version
    formulation_version: Version
    label_ref: Identifier


class ProductIdentity(SignedRecord):
    scope: Literal["product_identity"]
    identity: Formulation
    aliases: Annotated[tuple[Identifier, ...], Field(max_length=16)]
    ingredient_binding: Digest
    formulary_id: UUID | None = None
    formulary_version: Version | None = None
    formulary_digest: Digest | None = None

    @model_validator(mode="after")
    def validate_bindings(self):
        if tuple(sorted(set(self.aliases))) != self.aliases or self.identity.product in self.aliases:
            raise ValueError("Invalid alias list")
        bindings = (self.formulary_id, self.formulary_version, self.formulary_digest)
        if any(v is None for v in bindings) and any(v is not None for v in bindings):
            raise ValueError("Incomplete formulary binding")
        return self


class IngredientReview(SignedRecord):
    scope: Literal["ingredient_completeness"]
    identity: Formulation
    mapping_version: Version
    complete: Literal[True]
    ingredients: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    ingredients_digest: Digest

    @model_validator(mode="after")
    def validate_ingredients(self):
        if (tuple(sorted(set(self.ingredients))) != self.ingredients
                or fingerprint(self.ingredients) != self.ingredients_digest):
            raise ValueError("Invalid ingredient mapping")
        return self


class InteractionReview(SignedRecord):
    scope: Literal["interaction_set"]
    candidate: Digest
    members: Annotated[tuple[Digest, ...], Field(min_length=1, max_length=17)]
    set_digest: Digest
    outcome: Literal["allowed", "blocked"]

    @model_validator(mode="after")
    def validate_members(self):
        if (tuple(sorted(set(self.members))) != self.members or self.candidate not in self.members
                or fingerprint(self.members) != self.set_digest):
            raise ValueError("Invalid interaction set")
        return self


@dataclass(frozen=True)
class ProductRegistry:
    """Trusted server-provided active snapshots. No public constructor or persistence path."""
    identities: tuple[ProductIdentity, ...] = ()
    ingredients: tuple[IngredientReview, ...] = ()
    interactions: tuple[InteractionReview, ...] = ()
    known_ingredients: frozenset[str] = frozenset()

    def check(self, *, row, formulary_digest, current, products, vocabulary, keys, now):
        if (len(self.identities) > 256 or len(self.ingredients) > 256
                or len(self.interactions) > 256 or len(current) > 16
                or len(products) != len({entry.product for entry in products})):
            return "interaction_coverage_missing", ()
        mappings = {entry.product: tuple(sorted(entry.ingredients)) for entry in products}
        selected = []
        seen_ingredients = set()
        for token in (row.medicine, *current):
            matches = [p for p in self.identities if token in (p.identity.product, *p.aliases)]
            if len(matches) != 1:
                return "interaction_coverage_missing", ()
            product = ProductIdentity.model_validate(matches[0].model_dump())
            if product.vocabulary != vocabulary or not product.verified(keys, now):
                return "interaction_coverage_missing", ()
            if not selected and (product.identity.product, product.formulary_id, product.formulary_version,
                                 product.formulary_digest) != (row.medicine, row.id, row.version, formulary_digest):
                return "binding_mismatch", ()
            proofs = [m for m in self.ingredients if m.binding() == product.ingredient_binding]
            if len(proofs) != 1:
                return "interaction_coverage_missing", ()
            try:
                proof = IngredientReview.model_validate(proofs[0].model_dump())
            except ValidationError:
                return "interaction_coverage_missing", ()
            if (proof.identity != product.identity or proof.vocabulary != vocabulary or not proof.verified(keys, now)
                    or keys[proof.key_id].identity == keys[product.key_id].identity
                    or keys[proof.key_id].key == keys[product.key_id].key
                    or not set(proof.ingredients) <= self.known_ingredients
                    or mappings.get(product.identity.product) != proof.ingredients):
                return "interaction_coverage_missing", ()
            if any(p.identity.product == product.identity.product for p in selected) or seen_ingredients.intersection(proof.ingredients):
                return "duplicate_ingredient_risk", ()
            selected.append(product)
            seen_ingredients.update(proof.ingredients)
        members = tuple(sorted(p.binding() for p in selected))
        reviews = [r for r in self.interactions if r.candidate == selected[0].binding() and r.members == members]
        if len(reviews) != 1:
            return "interaction_coverage_missing", ()
        review = InteractionReview.model_validate(reviews[0].model_dump())
        if review.vocabulary != vocabulary or not review.verified(keys, now):
            return "interaction_coverage_missing", ()
        if review.outcome == "blocked":
            return "known_contraindication", ()
        return None, tuple(p.identity.product for p in selected[1:])
