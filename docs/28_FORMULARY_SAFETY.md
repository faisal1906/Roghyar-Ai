# BE-05 formulary safety

The private service verifies signed formulary content and tests deterministic safety screening. Patient assessment intake and medicine selection remain closed. The service ships without production medicine rows, clinically approved fixtures, reviewer keys, public recommendations or LLM calls.

## Schema and signed review

`0006_formulary_attestation` adds nullable JSONB `reviewed_payload` to the existing formulary. Inactive drafts may omit it. Active candidates need both age limits, a bounded version 1 envelope, a screening object, a key ID and a structurally valid signature. Existing approval metadata, forced RLS, one active version per condition and medicine, reviewed-content immutability and publication audits remain in force.

A reviewed record identifies a UUID, known condition ID, known medicine ID, positive version, reviewer UUID and UTC review instant. Conditions, medicines and allergens come from a separately governed vocabulary. The envelope contains `schema_version`, `key_id`, `signature`, `adult` and `screening`. Unsupported versions and unknown fields are rejected. The schema bounds identifiers to 48 characters, label strings to 512 characters, supporting text lists to 16 entries, screening lists to 64 entries and child bands to eight. Evaluation admits at most 64 active candidates per condition and at most 16 KiB per canonical signed body.

A dose object carries `form`, `unit`, exact `instructions`, exact `frequency` and exact `maximum_per_day`. Recognized forms are tablet, capsule and oral liquid. The schema permits mg or the corresponding dosage-form count for tablets and capsules, and mg or mL for oral liquid. The adult object must exactly agree with the signed legacy label columns. Conversions, calculations, interpolation and rounding are absent. Clinicians must ensure the approved label instructions and limits are complete and unambiguous for the exact product. Pediatric bands contain first and last whole-year ages and the same explicit dose object. Bands must be ordered, nonoverlapping and contained in the signed age range. Exact signed bands for ages 1–17 can be checked in synthetic tests. Infant, weight-based, unsupported or absent bands yield no dose. GD-03 and clinical review own any further pediatric specification.

The signing message is the ASCII domain `roghyar:formulary-review:v1`, a zero byte and canonical sorted compact ASCII-escaped JSON. It includes the exact record UUID, version, condition and medicine, age bounds, legacy labels, pediatric bands, contraindications, home steps, referral fields, UTC reviewer timestamp and identity, and the envelope except its signature. Activation and review-status flags are excluded so an authorized operator can re-activate the same immutable approved content. The reader requires approved, active rows and verifies the Ed25519 signature before screening. This is a different domain and a different typed trust registry from red-flag review. Each configured key has a verified reviewer identity, allowed medicine and condition scopes, validity window and public key. A revoked key is removed from the registry. Operations must roll out registry changes promptly and verify revocation on staging.

## Deterministic screening

The private safety context has whole-year age, pregnancy state, normalized allergy IDs, existing condition IDs and current medicine IDs. Trusted evidence adapters must represent incomplete, unreliable or unknown fields as null. An empty list is an affirmative complete report of no observations in that category. Pregnancy values are pregnant, not pregnant and verified not applicable. Supplying an unknown or unrecognized code cannot grant eligibility.

| Check | Behavior |
| --- | --- |
| Age and pregnancy | Strictly enforce the reviewed inclusive age limits and applicable exact dose band. Unknown pregnancy blocks selection. The signed screening policy is `not_pregnant_only` or `any_known_status`. |
| Allergies and conditions | Compare against signed allergen IDs and blocked conditions using the governed vocabularies. Legacy free-text contraindications are signed reviewer material and never executed as expressions. |
| Current medicines | Block matching normalized medicine IDs and signed blocked IDs. Require explicitly reviewed coverage, a complete list, and express signed compatibility with every remaining current medicine. Brand equivalence and duplicate-ingredient coverage require governed normalization and GD-03 verification. |
| Selection | Verify the whole active candidate set first. A failed candidate invalidates the snapshot. More than one eligible candidate returns `no_selection_policy`. A single eligible candidate yields the exact applicable dose only through the disposable synthetic probe. |

Denials distinguish empty approved content, invalid attestation, invalid configuration, insufficient information, known contraindications, unverified interactions, age or pregnancy mismatch, unsupported pediatric dose, ambiguous selection, unauthorized triage and evaluation failure. Denials carry no medication details. No patient evidence, approval secrets or exceptions are logged.

## Triage and production boundary

`FormularyService.evaluate` calls the BE-04 service and returns a medication denial for every current result, including no reviewed rule matched. The triage state is retained so medicine handling cannot hide emergency, urgent or unresolved risk. The future `HomeCareAuthority` interface requires separate approved GD-01 classification and GD-03 screening, tied to the verified snapshot, condition and safety context. BE-05 implements no authorization provider. The private synthetic probe requires the test environment, explicit synthetic fixtures and the disposable loopback CI database. It validates signed rows through the existing restricted runtime login.

## Publication, rollback and verification

The existing NOLOGIN publisher role can stage a candidate, activate it and change the active version in one transaction. The audit trigger records the publisher login and each review or activation transition. RLS keeps ordinary runtime reads limited to approved active candidates and denies runtime writes. Assessment writer privileges are separate. Only the application verifier decides whether an active database candidate has a valid scoped signature. A publisher's database approval flag cannot establish clinician authorization.

Reviewed content and signatures are immutable. Change the content through a new signed version. Rollback means deactivate the current version and reactivate a previously verified, still-authorized version, checking key validity and retaining both history entries. The migration refuses downgrade when any stored envelope would be lost, and requires an authority able to inspect the full table under forced RLS. Back up and separately plan removal or archival of envelopes before considering a downgrade. Live integration runs only on disposable PostgreSQL/PostGIS.

The tests use fictitious identifiers, nonclinical label placeholders and ephemeral software-test keys. They verify code behavior without clinical approval, safety or interaction-coverage claims.

## Clinical review checklist

- [ ] Verify qualified reviewer identity, medicine and condition scope, trust anchors, key custody, revocation and publisher access.
- [ ] Verify the exact product formulation, labeled dose, frequency, daily maximum and age bands. Review pregnancy eligibility and the bounds of all pediatric applicability.
- [ ] Approve allergy, contraindication, interaction and duplicate-ingredient coverage for governed identifiers and trusted patient evidence.
- [ ] Confirm the exact immutable version, signing message and audit and rollback evidence before allowing publication.
- [ ] Validate GD-01 and GD-03 integration, controlled QA-01 cases, unknown-information behavior, emergency precedence and approved non-drug fallback content.

Until those checks and production privacy and identity prerequisites are complete, the closed public HTTP contract and feature flags continue to govern release.
