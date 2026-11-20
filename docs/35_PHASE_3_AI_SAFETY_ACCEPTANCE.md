# Phase 3 AI safety acceptance

| Ticket | Engineering evidence | Remaining gate |
| --- | --- | --- |
| AI-01 | Strict language/relevance contracts and provider response tests | Reviewed model and language data |
| AI-02 | Bounded transitions, owner checks, revision and expiry tests | Atomic authoritative state custody across workers |
| AI-03 | Source, vocabulary and precedence tests, including the signed-rule cross-component suite | Published vocabulary and reviewed extraction |
| AI-04 | Signature, reviewer scope, exact language and warning digest checks | Qualified reviewer operations and released translations |
| AI-05 | Chromium speech doubles, local-only playback and offline emergency tests | Native OS/browser and EN/UR/PS speech quality |
| AI-06 | Versioned synthetic corpus, chained tests and sanitized exact-head CI artifacts | Tier B and expanded reviewed coverage |

## Threat model

| Boundary | Trust and attacker inputs | Unsafe outcome and mitigation | Evidence and residual risk |
| --- | --- | --- | --- |
| User messages and transcripts | Untrusted text, Unicode and role-like strings | Prompt influence. Bound text and separate instructions from data. Require explicit local recording. | AI-01 and speech tests. Native quality and semantic injection remain unverified. |
| AI-01 output | Untrusted labels, hint and score | False refusal or authority escalation. Strict JSON, abstention and independent rule routing. | Classification contract and AI-06 cases. A model can still mislabel language or relevance. |
| AI-02 state and identity | Verified principal and authoritative snapshot required. Client IDs and turns are untrusted. | Owner crossover or replay. Validate scope, revision and lifecycle. | Conversation tests. Concurrent mutations from a single snapshot still require atomic custody. |
| AI-03 extraction and proposals | Untrusted facts, IDs, spans, summary and level | Forged clinical evidence. Validate schema and vocabulary. Only mapped reports enter signed evaluation. | Structured-triage tests and chained suite. Reviewed multilingual semantics remain open. |
| BE-03 rules | Repository rows need scoped Ed25519 review | Forged match. Verify signatures, versions, review scope, time and revocation. | Rule and RLS tests. Operational publication remains gated. |
| BE-04 precedence | Trusted evaluation only | Model downgrades emergency. Rules run first and warning bundles are bound by digest and locale. | Chained synthetic signature/precedence tests. No clinical sensitivity result. |
| AI-04 artifacts | Untrusted bytes until authorized | Tampered or mis-scoped text. Exact signed rendering and separate clinical/product reviewer scope. | Writer tests, including clinical-unavailable scope regression. Real translation review is open. |
| AI-05 output | Browser-declared voice locality | Sensitive or unauthorized speech. Fixed preview only, local voice, explicit action and cancellation. | Chromium mocks. Hardware behavior and quality open. |
| External provider | Untrusted reply, failure, transport metadata | Egress or fail-open advice. No configured provider; bounded approved gateway boundary and typed failure. | Gateway timeout and redirect tests. Live telemetry and consent remain unverified. |
| Logs, storage and API | Incoming identifiers, errors and bodies untrusted | PHI disclosure. Redacted errors, closed routes, scoped database access, transient drafts and offline cache tests. | HTTP, Redis, RLS, PWA and photo guards. Production telemetry and custody require review. |

Direct prompt injection changes input text. Malformed schemas, unauthorized identities and unavailable dependencies are separate failure classes. Instructions alone confer no approval. The chained harness uses disposable keys and swaps only the rule repository and model adapter. Signed evaluation and warning resolution run through the real services.

## Reproduction and gates

Run pytest for apps/api/tests with JUnit output, then run apps/api/tests/report_ai06.py with that JUnit file and an output directory. Hosted CI saves sanitized JSON and Markdown with commit, runtime versions, corpus hash, case outcomes and suite counts. The 12 corpus cases exercise controlled multilingual labels and signed emergency precedence. They do not exercise learned inference.

Tier B requires privacy-approved, consented/de-identified or licensed data and representative sampling by EN/UR/PS, Romanized writing, dialect and code-switching. Record dataset revision, provenance, license, train/test separation, blinded language and clinician annotation guidelines, reviewer qualifications, disagreements, subgroup sizes and coverage gaps. Preserve reviewed reference labels separately from model output. For each subgroup report language precision/recall, relevance confusion counts and precision/recall, false off-topic refusals, abstentions, unsupported-language errors, negation/correction and attribution errors, unauthorized replies and attempted precedence violations. Record numerator, denominator and sample count, with intervals where informative. Do not export raw patient text or secrets. A real-model scoring adapter and approved benchmark data remain outstanding.

- **Engineering integrity** uses the hosted exact-head regression evidence. The synthetic corpus covers selected boundaries and does not exhaust the requested attack and browser cases.
- **Real-model multilingual quality** remains open. PDF section 17 calls for at least 95% same-language replies on 50 messages per language, at least 98% refusal on 100 off-topic messages, and less than 2% wrongful refusal on 200 health messages. No approved model was evaluated.
- **Clinical safety** remains open. QA-01 owns 200 clinician emergency cases, at least 99% detection and zero tolerated misses for heart, stroke and breathing groups. Device speech, translation review and clinical latency are unmeasured here.
- **Production activation** remains closed. No public agent, intake, automated detection, clinical guidance, medicines, booking, provider inference, remote audio or clinical TTS. Consent, retention, provider and key operations remain required. Manual offline Rescue 1122 remains independent.
