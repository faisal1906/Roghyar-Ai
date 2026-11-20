# Risks and open decisions

| Item | Status | Effect |
| --- | --- | --- |
| Original Phase 0 governance documents | Reconstructed from available sources | Reconcile the replacements with the original versions if the documentation pack becomes available. |
| Clinical content and emergency rules | Pending clinician review | Clinical analysis and medicine publication stay closed. |
| Providers, clinic onboarding and privacy approvals | Pending | No real reservations, messaging or patient data collection. |
| Database and hosted CI verification | Closed | The disposable GitHub Actions database passed the live migration round trip, PostGIS checks and full suite. [Run 44544036033](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44544036033) |
| Clinical feature availability | Gated by later tickets | Symptom submission, clinical analysis, automatic emergency detection, guidance, verified directories and booking remain unavailable. Manual emergency help and local draft review are available. |
| UI-01 device verification | Chromium viewport emulation | Desktop and Galaxy S5 layouts visually reviewed. Offline cache and telephone link verified in Chromium. Physical handset dialer and connectivity checks remain part of later device QA. |
| UI-02 recognition coverage | Browser and language pack dependent | A fresh Windows Chrome 155 profile reported English as downloadable and Urdu and Pashto as unavailable. Typed input covers unsupported browsers and languages. Physical-device recognition and language quality remain unverified. |
| AI-05 server speech-to-text | Pending provider and privacy approval | Confirm language coverage, consent and audio retention before adding a server fallback. UI-02 sends no audio or transcript to a remote service. |
| UI-03 clinical submission and adaptive questions | Gated | The assessment form can validate and review a local draft. Symptom-dependent questions and server submission require reviewed clinical rules and the later backend and AI pipeline. |
| UI-04 emergency activation and content | Manual UI only | The generic warning is available on device. BE-03 and BE-04 own clinician-reviewed detection and approved alert content. Browser latency tests measure manual activation only. |
| UI-04 device and provider capabilities | Deferred | LOC-01 and LOC-04–06 own consent and verified nearby care. Location sharing requires consent, privacy review and confirmed delivery support. AI-05 and later device QA own reviewed speech, tone and vibration verification. |
| UI-05 browser and device coverage | Windows Chromium verified | Automated axe checks, native 200%/400% desktop zoom and viewport/font simulations passed locally. Physical Android installation, low-end performance, phone dialing, speech engines and assistive-technology behavior remain open. |
| UI-05 offline updates | Verified with two live Chromium tabs | Preparing an update preserves each draft. Reloading with a draft requires separate confirmation. Tabs still running the old UI-04 shell may need to close before the first prompt-managed worker can take over. |
| UI-06 photo capability inventories | Hosted guard passed | Future routes, models, migrations and runtime dependencies require deliberate policy updates. Review camera and image data paths as part of each change. |
| Phase 1 physical-device acceptance | Open with QA-04 and LA-01 | Verify low-end Android performance and installation, offline handset behavior and physical dialing, TalkBack and Windows screen readers. AI-05 owns verified English, Urdu and Pashto device speech coverage. |
| BE-01 managed PostgreSQL provisioning | Open with infrastructure and security owners | Verify private connectivity, dedicated non-owner role, membership and grants, TLS, extension schema, secrets, backups and redacted database error logging on a Supabase staging project. [Disposable PostgreSQL and PostGIS verified](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44580957188). |
| BE-01 key management and retention | Open with privacy and infrastructure owners | Approve key custody and rotation, booked data retention, backups and deletion handling. BE-02 must preserve the 90-day anonymization path. Real data intake stays closed. |
| BE-01 credential issuance and clinical publication | Gated | BK-01 owns OTP binding and issuance. Clinical reviewers own signed content. BE-03 and BE-05 own rule and formulary activation workflows. |
| BE-01 source references | Reviewed authoritative copies present | The requested PDF, roadmap, API contract and governance records exist. The earlier original Phase 0 documentation pack remains unavailable. |
| BE-02 assessment intake | Closed | Privacy consent, endpoint-level rate limits, credential issuance and revocation, managed database role review, key rotation and diagnostic redaction require verification before any public intake. |
| BE-02 retention | Disposable integration only | Privacy and infrastructure owners must approve backup expiry, legal holds, booked records and ambiguous chat retention. BK-03 must prevent linking a booking to an already scrubbed assessment. |
| BE-03 clinical activation | Closed | Clinical governance must verify reviewer qualifications, bind signing keys to authorized reviewers and categories, approve real rule versions and warning content, and validate sensitivity and specificity with QA-01. BE-04 must establish emergency precedence before release. |
| BE-03 evidence and publication operations | Open | Validate structured evidence and multilingual extraction. Approve signing-key custody, revocation, publisher authorization, staged activation and rollback procedures. |
| BE-04 clinical decisions and warning artifacts | Closed | Clinical reviewers must approve the precedence and fallback policy, exact warning bundles and translations, and QA-01 acceptance criteria. AI-01/03 must validate evidence provenance and extraction. GD-01 must enforce the reviewed urgency floor and approved rendering policy. |
| BE-05 medication release | Closed | Requires authenticated formulary reviewers, signed product-label content, controlled identifiers, interaction coverage, validated safety evidence, and approved GD-01/GD-03 and QA-01 workflows. |
| BE-06 shared throttling and ingress | Awaiting staging | Provision private Redis with TLS, constrained ACLs and a shared HMAC key. Verify ingress TLS, peer identity, body limits, telemetry redaction, and provider failover. Health and closed assessment routes stay independent of this service. |
| AI-01 inference and multilingual quality | Blocked for activation | Privacy and infrastructure owners must approve provider, retention, egress and telemetry. AI-06 and QA-01 must validate EN/UR/PS, transliteration, code-switching and false off-topic handling. |
| AI-02 conversation activation | Closed | Reviewed question catalogs, independent safety orchestration, and privacy-approved atomic state custody are required before public use. |
| AI-03 clinical activation | Closed | Requires published reviewed vocabulary, language validation, provider privacy approval and GD-01 authorization. |
| AI-04 content publication | Closed | Requires authorized reviewer keys, per-locale review, revocation and immutable version custody, plus approved clinical activation. |
| AI-05 native speech and clinical playback | Open with QA-04 | Verify exact language packs, local voices, offline behavior and device quality. Clinical playback requires authorized artifacts and delivery approval. |
| AI-06 representative quality and state custody | Open | Tier B requires approved inference, reviewed datasets and privacy controls. Same-snapshot revisions require atomic custody before public chat. |
| Tier B evaluation execution | Blocked | Privacy and language reviewers must approve the representative manifest and external inference. QA-04 still owns native speech. |
| Gate A closeout | Engineering evidence only | Native audio, clinician validation and live-model quality remain separate gates. |
| GD-01 lower guidance | Closed | Requires reviewed evidence screening, independently authorized policy keys and exact signed locale content. |
| GD-02 durable content and clinical publication | Open | Read-only in-memory snapshots only. Durable immutable history, publisher privileges and audit are outstanding with GD-04 and infrastructure. |
| GD-03 medication authorization | Closed | Requires reviewed normalization, interaction and ingredient coverage, qualified policy issuers and validated evidence custody. GD-02 durable history remains open. |
| GD-04 clinical publication | Closed in production | Requires qualified reviewer onboarding, authenticated operator provisioning, fresh trust snapshots and custody, approved translations and QA-01. |
| GD-05 clinical and device validation | Open | QA-01 and QA-04 own clinician validation, physical devices and assistive technology. Operational integration and key custody remain gated. |
| GD-05 signed chain | Synthetic only | Clinical, real-model and native accessibility validation remain independent gates. |
| GD-05 final five | Engineering inventory | Governed normalization and trusted evidence custody must be distinguished from synthetic fixtures. |
| GD-05 remaining inventory | Engineering | Alias ambiguity and ingredient vocabulary coverage must be verified before selection claims. |
| GD-05 normalization | Operational approval open | The synthetic registry checks exact aliases and independent ingredient completeness. Production needs qualified reviewers and verified label sources. |
| GD-05 custody and normalization | Open | Clinical-source labels have no independently governed issuance or issuer revocation. Alias ambiguity and full combination coverage require reviewed vocabulary. |
| GD-05 trust contract | Open | Deployable issuer custody, governed formulation/aliases, and complete-set interaction authority remain required. |
| Evidence custody | Disposable only | Atomic corrections are process local. Production requires a durable authoritative store and trusted ingestion. |
| Correction concurrency | Process-local | Durable transaction-bound custody remains required for production. |
| Historical proof | Private synthetic verification | Durable custody and operational issuer provisioning remain release blockers. |

GD-05 historical-attestation tests cover correction history, separate authorized foreign contexts and current issuer authority using disposable signatures. The exact-head Phase 4 artifact assesses provenance. Durable and cross-process atomic patient-state custody, verified reviewer onboarding and production key operations remain release blockers.

GD-05 governed identity, ingredient completeness and exact-set interaction checks have synthetic controls. Qualified label review, real formulation and interaction coverage, approved pregnancy applicability criteria, current authority provisioning and durable atomic custody remain release blockers.

GD-05 fixtures exercise signed custody and publication with synthetic content. Durable cross-process custody, qualified reviewer onboarding, production key and revocation operations, consent, privacy approvals, QA-01 and QA-04 remain external release blockers.
LOC-01 requires explicit foreground consent and standard accuracy. The PDF describes immediate high-accuracy acquisition on directory entry. Native behavior and any later disclosure or precision changes require review.
LOC-02 keeps precise coordinates out of application requests. The planned GET contract needs a separately approved disclosure, URL and logging design. Reviewed hospital type, operating hours and specialty semantics are unavailable; the private service rejects those filters.
LOC-03 search is private. Real alias and verification provenance, multilingual fuzzy quality, production query plans, request throttling and search-term handling remain release dependencies.
LOC-04 requires authentic provider evidence, independent reviewer onboarding, separate database identities and evidence-retention approval before real publication. Production performance and public discovery remain unverified or closed.
