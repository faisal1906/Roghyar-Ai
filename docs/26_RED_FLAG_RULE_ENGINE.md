# BE-03 red-flag rule engine

The service implements the rule infrastructure for PDF §7.3. It evaluates structured evidence locally in the backend using Python and the private PostgreSQL rule repository. There is no patient-facing endpoint, natural-language extraction, rule seed, embedded reviewer key, or clinical release switch. Manual Rescue 1122 help remains available offline.

## Evidence and rule schema

A caller passes up to 128 normalized observations. Each has a fact ID, provenance and a strictly typed value when the provenance is `reported` or `verified`. The other states are `untrusted`, `unsupported` and `unknown`. These use no value. Evidence producers must verify provenance before constructing these inputs. A patient claim is a report, and a future extractor must supply separately validated evidence. The engine accepts no raw symptom text. BE-03 supplies no mapping from natural language to fact IDs.

Stable category IDs are `heart`, `breathing`, `brain_stroke`, `bleeding`, `consciousness`, `pregnancy`, `children_infants` and `poisoning_injury`. A row has a rule code, positive version, category, reviewed priority (`emergency` or `urgent`), reviewer UUID, review timestamp and active state. The category names do not encode clinical triggers.

The JSONB `pattern` allows schema version 1, a warning reference and SHA-256 digest, a required inclusion expression, an optional exclusion expression, and an attestation with key ID and 128 lowercase hex characters for the Ed25519 signature. Expressions use `all` or `any` groups of one to eight children, or a comparison leaf with a fact ID, operator and literal value. Available comparisons are `eq`, `ne`, `lt`, `lte`, `gt` and `gte`. Values are booleans or bounded integers. Boolean facts support equality and inequality. No coercions are permitted. Identifiers are lowercase ASCII letters, digits and underscores, start with a letter, and have a maximum length of 48. Warning digests refer to separately reviewed versioned warning artifacts. BE-03 does not resolve or display them.

The evaluator allows up to 64 active candidates, 8 KiB per persisted pattern, four expression levels and 32 nodes per rule across inclusion and exclusion. The repository selects one ordered, bounded snapshot. Oversized rows are represented as invalid definitions. The fixed operators operate on validated data and have no execution, regex, SQL or AI hooks.

## Evaluation result

Each comparison returns true, false or unknown. Missing, conflicting, unsupported, untrusted and mismatched-type observations become unknown. ALL is false if any child is false and true only when all are true. ANY is true if any child is true and false only when all are false. A rule matches when inclusion is true and exclusion, if present, is false. Unknown exclusions cannot count as false. Confirmed matches are retained when a different rule lacks evidence.

The result includes a UTC evaluation timestamp and, for a validated snapshot, a SHA-256 fingerprint of the ordered signed payload digests. Match entries have rule code and version, category, priority, supporting normalized fact IDs and the warning reference and digest. Evidence values and rule bodies are absent. The fingerprint identifies the rule snapshot and excludes observations. Standard outcomes are:

| Status | Meaning |
| --- | --- |
| `match` | One or more verified rules matched the supplied evidence. |
| `no_match` | Every evaluated rule was definitively false for the supplied evidence. This status cannot establish clinical safety. |
| `incomplete_evidence` | The evidence payload is invalid or relevant evidence leaves one or more rules unresolved. |
| `no_approved_rules` | The active approved repository has no candidates. |
| `invalid_configuration` | A candidate failed schema, bounds, uniqueness, approval or signature verification. The full snapshot is rejected. |
| `evaluation_failure` | The repository or evaluator failed unexpectedly. No partial result is returned. |

The engine and repository do not log private inputs, rule patterns or exception details. BE-04 owns the precedence policy and any safe fallback after an unavailable, incomplete or unmatched result.

## Review, signing and publishing

A database approval state identifies a candidate. Migration `0005_red_flag_attestation` requires the versioned envelope structure for active candidates, while the interpreter performs cryptographic verification. Reviewer keys must come from a separately secured clinical authority registry. Each trust anchor binds a reviewer UUID, public key, allowed categories and validity window. Signature verification requires the attestation's review time within the permitted window and the key still valid at evaluation. Revocation removes trust in that key. No production trust anchor or signing service ships with this ticket.

The Ed25519 message uses a versioned domain prefix followed by UTF-8-compatible ASCII JSON with sorted keys, compact separators and schema-normalized defaults. It includes rule code, version, category, priority, reviewer UUID, UTC review time, key ID and the full normalized rule body excluding the attestation envelope. The body binds the warning reference and digest. Editing thresholds, groups, version, priority or warning content requires a fresh signed review. A publisher could set database flags with a structurally valid forged signature. That row fails verification and prevents evaluation of the snapshot. Actual activation requires authorized publishing operations and a trusted registry.

The existing forced RLS keeps patient, anonymous and hospital-admin runtime sessions read-only and gives the runtime only active approved candidates. The restricted assessment writer has no rule access. The separate NOLOGIN publisher cannot delete history. The existing trigger prevents edits to reviewed content and audits activation and deactivation. The partial unique index permits one active version per rule code. Authorized rollback deactivates the current version and reactivates a still-attested previous version in one transaction. Audit history retains both changes and the operator database identity. Schema downgrade removes the additional envelope constraint. A staged migration must verify existing active content before applying the revision.

## Clinical review checklist

- [ ] Verify reviewer qualifications, jurisdiction and authorization for the rule category. Complete key provision, custody, revocation and publisher access review.
- [ ] Review observation provenance, eligibility, thresholds, units, required combinations, exclusion logic, missing and conflicting evidence, and coverage gaps.
- [ ] Confirm exact rule code, version, priority and warning-artifact hash before signing. Approve the versioned warning wording and independently review translations with qualified speakers.
- [ ] Build the QA-01 corpus with clinician-labeled positives, negatives, boundary cases, incomplete evidence and relevant demographic cases. Define sensitivity, specificity and acceptable failure criteria before release.
- [ ] Validate input extraction in Urdu, Pashto and English with separate language and device evidence. Establish how unreliable extraction is discarded or marked unknown.
- [ ] Verify BE-04 places eligible emergency matches ahead of every other clinical outcome and preserves a safe path when rules or evidence are unavailable.
- [ ] Verify staged publisher authorization, audit monitoring, key revocation, and exact-version rollback drills before enabling live use.

All automated rule and signature probes use isolated synthetic facts and temporary software-test keys. These checks establish software behavior only. Clinical approval, reviewed content, validated multilingual evidence, operational controls and BE-04 precedence remain release blockers.
