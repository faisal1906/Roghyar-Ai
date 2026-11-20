# BE-04 deterministic triage precedence

BE-04 provides a private, read-only orchestration service. It evaluates structured evidence using the BE-03 PostgreSQL repository and Ed25519 verifier before selecting a state. The service has no public endpoint, language model, symptom-text parser, clinical database writer or warning publication path.

## Decision table

Apply the rows in order after validating the BE-03 result. Severe, moderate and mild selections, diagnosis names and model confidence are outside this contract.

| BE-03 input | Decision state | Urgency floor | Required interpretation |
| --- | --- | --- | --- |
| MATCH with any emergency match | `reviewed_emergency` | EMERGENCY | Preserve every match, including urgent matches, exact rule versions, warning bindings and unresolved facts. Other results cannot lower this floor. |
| MATCH with urgent matches only and unresolved facts | `urgent_review_required` | URGENT | Preserve the confirmed urgent concern. Further safety assessment is required. BE-03 currently aggregates unresolved facts without a priority attribution, so any unresolved fact conservatively triggers this state. |
| MATCH with urgent matches only and no unresolved facts | `reviewed_urgent` | URGENT | Preserve the urgent concern and provenance. This describes the evaluated rules and evidence only. |
| NO_MATCH | `no_reviewed_match` | None | No reviewed rule matched the supplied evidence. Leave non-emergency classification pending in a later validated workflow. |
| INCOMPLETE_EVIDENCE | `further_assessment_required` | None | Preserve uncertainty and unresolved fact IDs where available. Missing, conflicting or untrusted observations cannot justify lower urgency. |
| NO_APPROVED_RULES or INVALID_CONFIGURATION | `rules_unavailable` | None | There is no usable reviewed snapshot. The reason code identifies why. |
| EVALUATION_FAILURE, unexpected exception or inconsistent result | `evaluation_failure` | None | Discard partial findings and expose no exception content. |

Each request evaluates one snapshot. A later snapshot, newly resolved evidence or a revoked key requires a fresh evaluation. The service performs no automatic state transitions through a language model. PostgreSQL outages are represented as evaluation failures. The offline manual UI-04 Rescue 1122 path remains independent of backend results. Generic emergency help does not claim detection.

## Internal contract and trust

`TriageService(engine, reviewer_keys, warning_repository)` constructs the fixed BE-03 interpreter and restricted database repository internally. The method `evaluate(evidence)` accepts BE-03 structured observations from trusted adapters and validates their shape through BE-03. Raw symptoms, fabricated evaluations, urgency, clinical outcome and diagnosis fields are outside the evidence interface. The private Python call boundary authorizes the evidence producer. AI-01/03 must separately validate observation provenance and multilingual extraction before they can supply trusted evidence. There is no production trust-anchor configuration or AI-to-triage wiring in BE-04.

The immutable `TriageDecision` exposes state and urgency floor, BE-03 status as reason code, UTC evaluation timestamp, validated snapshot fingerprint, exact matched rule versions, categories, priorities, evidence IDs, warning references and SHA-256 digests. It includes sorted unresolved fact IDs. Matches are ordered by emergency first, then stable rule code and version. The fields are internal. Downstream logging must omit evidence IDs, warning content and assessed details. The service has no assessment row writes or privilege changes.

The PDF §6 levels are EMERGENCY, URGENT, SEE A DOCTOR and HOME CARE. Only the first two are possible BE-04 urgency floors. GD-01 owns the four-level guidance workflow and rendering, using reviewed rules and approved content. A subsequent classifier, including AI-03, must respect the urgency floor and any pending safety assessment. BE-05 owns the formulary scaffold. BE-02 still stores only encrypted drafts in isolated tests. A future decision writer needs its own narrowly scoped role, verified assessment ownership and version-bound audit records.

## Warning artifact verification

The private `resolve_warning(evidence, selection)` re-evaluates with the same trusted source. Selection contains the expected snapshot fingerprint, matched rule code and version, and requested locale (`en`, `ur`, `ps`). It only selects matches at the decision's urgency floor. The lookup is constrained to the warning reference and digest carried by that signed rule. The SHA-256 digest covers the domain prefix `roghyar:warning:v1` followed by a zero byte and the canonical sorted, compact ASCII-escaped JSON for `{reference, translations}`. Translation values must be nonblank strings of at most 2,000 characters. The complete translation map is bound by the digest. Missing artifacts, mismatched reference or contents, unavailable locales, changed snapshots and repository errors return no content. Warning lookup failure preserves the rule decision. There is no cross-locale fallback or patient display integration.

## Verification and release blockers

Synthetic Ed25519 tests cover the decision table, mixed urgency and unknown facts, conflicting observations, expired and revoked authority, invalid signatures, configuration failure, snapshot changes, warning artifact tampering and forged input rejection. The live PostGIS test invokes the orchestrator with a real restricted runtime login. Existing HTTP 503, privacy, RLS, encryption, retention, photo and offline guards remain in the full regression suite.

A local engineering benchmark used 64 signed rules × 32 expression nodes and 25 timed samples. The observed median was 33.77 ms and p95 was 67.21 ms. It includes signature verification and precedence over in-memory synthetic rows, and excludes database I/O, symptom extraction and rendering. The reproducible test prints the measurements in the hosted run as well. This is a software benchmark. Clinical detection accuracy and end-to-end latency require a reviewed QA-01 corpus and separately validated evaluations.

Clinical governance must approve the real rules, accepted evidence sources, precedence and fallback policy, warning bundles and English, Urdu and Pashto translations. Operations must verify clinician identity, key provisioning and timely revocation, reviewed publisher access, auditable version rollback and private staging controls. QA-01 owns sensitivity, specificity, boundary and failure metrics. Intake, automatic detection, clinical guidance, medicines, directories and booking remain closed.
