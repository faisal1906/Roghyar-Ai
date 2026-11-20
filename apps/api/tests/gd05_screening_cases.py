"""Disposable signed product and interaction acceptance fixtures."""
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from roghyar.evidence_authority import EvidenceAttestation, EvidenceIssuer
from roghyar.evidence_custody import InMemoryEvidenceCustody
from roghyar.medication_safety import MedicationSafetyService, ProductIngredients, digest
from roghyar.product_identity import (
    Formulation, IngredientReview, InteractionReview, ProductIdentity, ProductRegistry, ProductReviewer, fingerprint,
)
from roghyar.red_flags import canonical
from test_formulary_safety import BLOCKED, COMPATIBLE, MEDICINE, OTHER, VOCABULARY

MISSING = "interaction_coverage_missing"
ELIGIBLE = "synthetic_eligible"
CASES = {
    "canonical": ELIGIBLE, "alias": ELIGIBLE, "unknown_alias": MISSING, "ambiguous_alias": MISSING,
    "shared_formulation_alias": MISSING, "formulary_binding": "binding_mismatch", "empty_registry": MISSING,
    "identity_revoked": MISSING, "identity_expired": MISSING, "identity_signature": MISSING,
    "identity_scope": MISSING, "identity_vocabulary": MISSING, "alias_changed": MISSING,
    "inactive_alias": MISSING, "duplicate_aliases": "duplicate_ingredient_risk",
    "single_ingredient": ELIGIBLE, "combination": ELIGIBLE, "partial": MISSING,
    "duplicate_ingredient": MISSING, "unknown_ingredient": MISSING, "ingredient_changed": MISSING,
    "stale_product_version": MISSING, "reused_mapping": MISSING, "ingredient_revoked": MISSING,
    "ingredient_expired": MISSING, "ingredient_signature": MISSING, "policy_mapping_mismatch": MISSING,
    "ingredient_overlap": "duplicate_ingredient_risk", "missing_mapping": MISSING,
    "no_current": ELIGIBLE, "one_current": ELIGIBLE, "two_current": ELIGIBLE,
    "pairwise_only": MISSING, "blocked_set": "known_contraindication", "unknown_product": MISSING,
    "extra_member": MISSING, "removed_member": MISSING, "reordered": ELIGIBLE, "new_mapping_version": MISSING,
    "interaction_revoked": MISSING, "interaction_expired": MISSING, "interaction_signature": MISSING,
    "wrong_owner": "invalid_context", "wrong_policy": "binding_mismatch",
    "pregnant_restricted": "age_not_covered", "pregnant_applicable": ELIGIBLE, "not_pregnant": ELIGIBLE,
    "pregnancy_unknown": "unknown_pregnancy_status", "pregnancy_contradiction": "unknown_pregnancy_status",
    "pregnancy_unsupported": "unknown_pregnancy_status", "pregnancy_verified_applicability": ELIGIBLE,
    "pregnancy_stale_policy": "binding_mismatch", "pregnancy_fresh_policy": "age_not_covered",
    "be05_coverage_required": "interaction_coverage_missing",
    "authority_not_independent": "interaction_coverage_missing",
    "be05_blocks_reviewed_set": "known_contraindication",
    "missing_custody": "binding_mismatch",
}


async def exercise(case, flow, context, evidence, policy, row, formulary_keys, policy_authority, policy_key, guidance_key):
    now = context.now
    roles = ("product_identity", "ingredient_completeness", "interaction_set")
    keys = {scope: Ed25519PrivateKey.generate() for scope in roles}
    authorities = {scope: ProductReviewer(scope, key.public_key(), scope,
                   now - timedelta(minutes=1), now + timedelta(minutes=5)) for scope, key in keys.items()}

    if case == "authority_not_independent":
        authorities["ingredient_completeness"] = replace(
            authorities["ingredient_completeness"], identity=policy_authority.reviewer)

    def sign(model, scope, **data):
        item = model(schema_version=1, scope=scope, vocabulary=data.pop("vocabulary", "synthetic"),
                     reviewer=authorities[scope].identity, key_id=scope, reviewed_at=now, expires_at=now + timedelta(minutes=2),
                     signature="0" * 128, **data)
        return item.model_copy(update={"signature": keys[scope].sign(item.signing_bytes()).hex()})

    def ingredient(form, items, version=1):
        items = tuple(sorted(items))
        return sign(IngredientReview, "ingredient_completeness", identity=form, mapping_version=version,
                    ingredients=items, ingredients_digest=fingerprint(items), complete=True)

    def identity(form, proof, aliases=(), **overrides):
        bounds = (dict(formulary_id=row["id"], formulary_version=row["version"],
                       formulary_digest=policy.formulary_digest) if form.product == MEDICINE else {})
        return sign(ProductIdentity, "product_identity", identity=form, aliases=aliases,
                    ingredient_binding=proof.binding(), **(bounds | overrides))

    def review(candidate, current, outcome="allowed"):
        members = tuple(sorted(p.binding() for p in (candidate, *current)))
        return sign(InteractionReview, "interaction_set", candidate=candidate.binding(), members=members,
                    set_digest=fingerprint(members), outcome=outcome)

    first_product = BLOCKED if case == "be05_blocks_reviewed_set" else COMPATIBLE
    forms = [Formulation(product=product, formulation=f"synthetic_form_{i}", product_version=1,
                         formulation_version=1, label_ref=f"synthetic_label_{i}")
             for i, product in enumerate((MEDICINE, first_product, OTHER))]
    base = ("synthetic_ingredient", "synthetic_second") if case in ("combination", "partial") else ("synthetic_ingredient",)
    listed = [base, ("synthetic_compatible_ingredient",), ("synthetic_other_ingredient",)]
    if case == "ingredient_overlap":
        listed[1] = base
    if case == "unknown_ingredient":
        listed[1] = ("synthetic_unrecognized_ingredient",)
    proofs = [ingredient(form, items) for form, items in zip(forms, listed)]
    products = [identity(form, proof, ("synthetic_alias", "synthetic_alias_two") if i == 1 else ())
                for i, (form, proof) in enumerate(zip(forms, proofs))]
    candidate, first, second = products
    current = [first_product]
    if case in ("alias", "alias_changed", "inactive_alias", "ambiguous_alias", "shared_formulation_alias"):
        current = ["synthetic_alias"]
    if case == "unknown_alias":
        current = ["synthetic_unknown_alias"]
    if case == "duplicate_aliases":
        current = ["synthetic_alias", "synthetic_alias_two"]
    if case == "no_current" or case.startswith("pregnan") or case == "not_pregnant":
        current = []
    if case in ("two_current", "pairwise_only", "blocked_set", "extra_member", "reordered"):
        current = [COMPATIBLE, OTHER]
    if case == "reordered":
        current.reverse()
    if case == "unknown_product":
        current.append("synthetic_unregistered")
    covered = [first] if current else []
    if case in ("two_current", "reordered", "blocked_set", "removed_member"):
        covered = [first, second]
    reviews = [review(candidate, covered, "blocked" if case == "blocked_set" else "allowed")]
    if case == "pairwise_only":
        reviews.append(review(candidate, [second]))
    if case == "formulary_binding":
        products[0] = identity(forms[0], proofs[0], formulary_version=2)
    if case in ("ambiguous_alias", "shared_formulation_alias"):
        form = forms[1].model_copy(update={"formulation": "synthetic_variant"}) if case == "shared_formulation_alias" else forms[2]
        proof = ingredient(form, listed[2])
        products.append(identity(form, proof, ("synthetic_alias",)))
        proofs.append(proof)
    if case in ("identity_revoked", "identity_expired", "identity_scope", "ingredient_revoked",
                "ingredient_expired", "interaction_revoked", "interaction_expired"):
        scope = roles[("identity", "ingredient", "interaction").index(case.split("_")[0])]
        updates = {"revoked": True} if case.endswith("revoked") else (
            {"scope": "interaction_set"} if case.endswith("scope") else {"valid_until": now})
        authorities[scope] = replace(authorities[scope], **updates)
    if case == "identity_signature":
        products[1] = first.model_copy(update={"signature": "0" * 128})
    if case == "identity_vocabulary":
        products[1] = identity(forms[1], proofs[1], first.aliases, vocabulary="synthetic_old")
    if case in ("alias_changed", "inactive_alias"):
        if case == "alias_changed":
            products[1] = first.model_copy(update={"aliases": ("synthetic_alias",)})
        else:
            products.remove(first)
    if case in ("missing_mapping", "ingredient_changed", "ingredient_signature", "duplicate_ingredient"):
        if case == "missing_mapping":
            proofs.pop(1)
        else:
            updates = {"signature": "0" * 128} if case == "ingredient_signature" else {
                "ingredients": ("synthetic_altered",)} if case == "ingredient_changed" else {
                "ingredients": listed[1] * 2}
            proofs[1] = proofs[1].model_copy(update=updates)
            if case == "duplicate_ingredient":
                products[1] = identity(forms[1], proofs[1])
    if case in ("stale_product_version", "reused_mapping"):
        form = forms[1].model_copy(update={"product_version": 2}) if case == "stale_product_version" else forms[1]
        products[1] = identity(form, proofs[2] if case == "reused_mapping" else proofs[1])
    if case == "new_mapping_version":
        proofs[1] = ingredient(forms[1], listed[1], 2)
        products[1] = identity(forms[1], proofs[1])
    if case == "interaction_signature":
        reviews[0] = reviews[0].model_copy(update={"signature": "0" * 128})

    pregnancy = ("pregnant" if case in ("pregnant_restricted", "pregnant_applicable",
                 "pregnancy_stale_policy", "pregnancy_fresh_policy") else
                 None if case == "pregnancy_unknown" else
                 "not_applicable" if case in ("pregnancy_verified_applicability", "pregnancy_unsupported")
                 else "not_pregnant")
    original = evidence
    evidence = evidence.model_copy(update={"values": evidence.values.model_copy(
        update={"pregnancy": pregnancy, "current_medicines": current})})
    issuers = {}
    if case == "pregnancy_contradiction":
        evidence = evidence.model_copy(update={"pregnancy": evidence.pregnancy.model_copy(update={"status": "contradictory"})})
    if case == "pregnancy_verified_applicability":
        key = Ed25519PrivateKey.generate()
        issuers["synthetic_pregnancy"] = EvidenceIssuer(
            "synthetic_pregnancy", key.public_key(), frozenset({"pregnancy"}), now,
            now + timedelta(minutes=2), "disposable")
        proof = EvidenceAttestation(owner=context.owner.subject, owner_kind=context.owner.kind,
            conversation=context.state.conversation_id, event=evidence.pregnancy.event, revision=evidence.revision,
            field="pregnancy", value_digest=digest(canonical(pregnancy)), vocabulary="synthetic",
            issuer="synthetic_pregnancy", key_id="synthetic_pregnancy", issued_at=now,
            expires_at=now + timedelta(minutes=1), signature="0" * 128)
        proof = proof.model_copy(update={"signature": key.sign(proof.signing_bytes()).hex()})
        evidence = evidence.model_copy(update={"pregnancy": evidence.pregnancy.model_copy(update={
            "status": "verified_complete", "source": "clinical", "attestation": proof})})
    correcting = case in ("pregnancy_stale_policy", "pregnancy_fresh_policy")
    custody = InMemoryEvidenceCustody(context.owner, context.state, original if correcting else evidence,
                                     assessment_event=context.event_id, now=now)
    old_policy = policy
    if correcting:
        event = uuid4()
        evidence = evidence.model_copy(update={"revision": 3, "pregnancy": evidence.pregnancy.model_copy(
            update={"event": event})})
        state = custody.correct(context.owner, context.state, expected_revision=2, field="pregnancy",
            predecessor=context.event_id, event=event, evidence=evidence, now=now)
        lower = context.approval.model_copy(update={"revision": 3})
        lower = lower.model_copy(update={"signature": guidance_key.sign(lower.signing_bytes()).hex()})
        context = context.model_copy(update={"state": state, "revision": 3, "approval": lower})
    mapping = [ProductIngredients(product=form.product, ingredients=items) for form, items in zip(forms, listed)]
    if case in ("partial", "policy_mapping_mismatch"):
        target = 0 if case == "partial" else 1
        mapping[target] = mapping[target].model_copy(update={"ingredients": ("synthetic_ingredient",)})
    updates = {"products": tuple(mapping), "guidance_digest": digest(context.approval.signing_bytes())}
    if case not in ("wrong_policy", "pregnancy_stale_policy"):
        updates["evidence_digest"] = digest(canonical(evidence.model_dump(mode="json")))
    policy = policy.model_copy(update=updates)
    policy = policy.model_copy(update={"signature": policy_key.sign(policy.signing_bytes()).hex()})
    if case == "pregnancy_stale_policy":
        policy = old_policy
    registry = ProductRegistry(tuple(products), tuple(proofs), tuple(reviews), frozenset({
        "synthetic_ingredient", "synthetic_second", "synthetic_compatible_ingredient", "synthetic_other_ingredient"}))
    def compose(snapshot):
        return MedicationSafetyService(flow, VOCABULARY, vocabulary_version="synthetic",
        formulary_reviewers=formulary_keys, policy_reviewers={"synthetic": policy_authority},
        active_policies=frozenset({("synthetic", 1)}), evidence_custody=None if case == "missing_custody" else custody, evidence_issuers=issuers,
        product_registry=snapshot, product_reviewers=authorities)
    if case == "wrong_owner":
        context = context.model_copy(update={"owner": replace(context.owner, subject=uuid4())})
    inputs = dict(guidance_context=context, evidence=evidence, policy=policy, formulary=row)
    service = compose(None if case == "empty_registry" else registry)
    assert (await service.screen(**inputs)).model_dump() == {"reason": CASES[case]}
    if CASES[case] == ELIGIBLE:
        assert (await compose(replace(registry, interactions=())).screen(**inputs)).reason == MISSING
