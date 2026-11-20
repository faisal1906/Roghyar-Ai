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

UI PRs #1–#6 were integrated sequentially with ordinary merges. The final main tree matches the audited UI-06 head. [Integrated main CI](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44576024467)
