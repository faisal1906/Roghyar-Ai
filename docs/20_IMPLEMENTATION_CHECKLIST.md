# Phase 0 implementation checklist

| Ticket | Status | Evidence |
| --- | --- | --- |
| F-01 | Implemented | PWA and live API verified by the Chromium foundation test in the initial handoff. |
| F-02 | Passed | PostgreSQL 16.9 and PostGIS 3.5.2. Upgrade → downgrade → upgrade passed. Both head checks returned readiness HTTP 200 and verified geography SRID 4326 and GiST query execution. At base, Alembic had no applied revisions, PostGIS remained installed, readiness returned HTTP 503 and liveness returned HTTP 200. |
| F-03 | Passed | All 14 backend tests passed with zero skips, OpenAPI and generated TypeScript matched Git, the production build passed, and Chromium passed the foundation E2E test. |
| F-04 | Implemented | Seed tests cover the labeled empty scenario and local opt-in. |
| F-05 | Implemented | Settings tests cover database configuration, credential redaction and release flags. |
| F-06 | Implemented | Foundation ADR, source references, risk register and handoff are present. Phase 0 documents were reconstructed where originals were absent. |

Phase 0 closed on 2026-11-19. [Successful GitHub Actions run for commit `e323d60`](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44544036033)

## Phase 1

| Ticket | Status | Evidence |
| --- | --- | --- |
| UI-01 | Implemented | Responsive shell, English navigation, unavailable views, quick actions, diagnosis notice, and a persistent Rescue 1122 link. Playwright covers desktop, tablet, small Android viewport, keyboard, reduced motion, service failure and offline reload. |
| UI-02 | Implemented | Bilingual Home and AI Agent entry, on-device speech recognition and language-pack setup where offered, editable in-memory transcript and typed fallback. Browser interaction verified with API doubles. Physical-device language coverage remains open. |
| UI-03 | Implemented | In-memory symptom assessment draft, exact duration normalization, age and description validation, optional safety details, and local review. Typed follow-up controls are ready for later reviewed questions. |
| UI-04 | Implemented | Manual emergency dialog, offline fallback, two Rescue 1122 links, modal focus and scroll handling, and generic warning information. Automatic detection and provider integrations remain gated. |
| UI-05 | Implemented | Route reflow at seven widths, keyboard and zoom checks, automated WCAG 2.2 A/AA scans, public-only PWA cache, and draft-safe update recovery. |
| UI-06 | Implemented and verified | Production photo-capability guard, API and migration inventories, and final [Phase 1 acceptance report](25_PHASE_1_ACCEPTANCE_REPORT.md). [Passing UI-06 CI](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44570578763) |

The Phase 1 engineering gate is closed. Physical-device and release acceptance remain open in the [acceptance matrix](25_PHASE_1_ACCEPTANCE_REPORT.md#acceptance-matrix). The next engineering ticket is BE-01.

## Phase 2

| Ticket | Status | Evidence |
| --- | --- | --- |
| BE-01 | Engineering verified | Core schema, forced RLS, signed transaction scope, field encryption, schema inventory and live runtime isolation. Hosted run passed 22 backend tests with zero skips, 12 frontend unit tests and 42 Chromium tests. Supabase staging and privacy approvals remain open. [Hosted verification](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44580957188) [Database security decision](adr/0002-database-security-and-scoped-sessions.md) |
| BE-02 | Engineering verified | Strict pre-triage drafts, dedicated assessment writer, encrypted storage, owner-scoped retries, and disposable 90-day retention. Public routes return 503. [Hosted verification](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44676633283) passed 58 backend tests without skips, 12 frontend unit tests, and 42 Chromium tests. |
| BE-03 | Clinical activation closed | Private deterministic rule interpreter, attestation verification, bounded snapshot loading and publication security checks. Qualified reviewer sign-off, trust-anchor provision, corpus evaluation and BE-04 precedence are open. [Rule engine](26_RED_FLAG_RULE_ENGINE.md) |
| BE-04 | Clinical activation closed | Private precedence and safe fallback states, exact warning-artifact verification, and synthetic unit and live database checks. Clinical approval, validated evidence, QA-01 and GD-01 remain release dependencies. [Precedence contract](27_TRIAGE_PRECEDENCE.md) |
| BE-05 | Medication release closed | Separate Ed25519 review authority, strict signed formulary schema and deterministic synthetic safety screening. GD-01, GD-03, QA-01 and clinical review remain release dependencies. [Formulary safety](28_FORMULARY_SAFETY.md) |
| BE-06 | Engineering verification on exact-head CI | Standard errors, request correlation, fail-closed shared quotas, bounded request parsing and production HTTP policy. [Phase 2 acceptance matrix](29_PHASE_2_ACCEPTANCE_REPORT.md) |

UI PRs #1–#6 were integrated sequentially with ordinary merges. The final main tree matches the audited UI-06 head. [Integrated main CI](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44576024467)

## AI-01 — Language and relevance contract

- [x] Private typed input, provider output and routing result with synthetic EN/UR/PS regression fixtures.
- [x] Opt-in HTTPS transport boundary with deadline, cancellation and strict response validation.
- [ ] Approve and verify live provider, privacy controls and representative multilingual quality before activation (AI-06 / QA-01).

- [x] AI-02 private bounded memory and follow-up transitions. Public use remains gated.

- [x] AI-03 private structured evidence and candidate validation. Clinical activation remains closed.

- [x] AI-04 private signed content rendering and warning precedence. Production publication remains closed.

- [x] AI-05 local speech capability reporting and fixed-copy playback preview. Physical-device and language quality remain open.

- [x] AI-06 synthetic chained regression and exact-head CI evidence. Tier B and clinical approval remain open.
- [x] AI-06 Gate A fixed checklist passed on b1c35fc. Gates B/C/D remain separate.
- Gate A closeout evidence is generated from the fixed manifest and exact-head hosted results.
- GD-01 private four-level flow. Production guidance remains closed.
- GD-02 signed structured in-memory retrieval implemented. Durable immutable repository and publisher controls remain open.
- GD-03 private medication evidence and policy checks implemented. Patient-facing medication remains unavailable.
- GD-04 adds durable home-care history and private publishing. Production provisioning remains closed.
- GD-05 exact-head acceptance evidence added. Full Phase 4 edge-case acceptance remains open.
- GD-05 signed cross-service chain implemented. Remaining named engineering inventories are in the Phase 4 artifact.
- GD-05 PostgreSQL-backed chain and revocation checks added to mandatory CI evidence.
- GD-05 added strict age-input inventory and stale PostgreSQL revision replacement evidence.
- GD-05 added individual signed-chain case mappings. See the Phase 4 artifact for remaining coverage.
- GD-05 residual reviewer/content tests mapped. Phase 4 engineering stays open for the named inventories.
- GD-05 independent clinical evidence signatures verified with disposable keys; broader custody and normalization remain open.
- GD-05 disposable evidence custody implemented with five individually mapped field cases.
- Changed-value and process-local interleaving regressions mapped.
- GD-05 historical transplant and evidence reporter regressions mapped. The cross-field, cross-owner and cross-conversation controls each verify a separate authorized source and destination. The exact-head reporter derives the provenance result.

- GD-05 governed screening adds 57 mandatory case mappings and ten reporter regressions. The exact-head Phase 4 artifact records screening and provenance separately. Chain-mutation and age inventory gaps remain open.

GD-05 closeout adds independent structured-chain, restricted PostgreSQL and age-rebinding mappings. The exact-head Phase 4 artifact derives each engineering inventory.
