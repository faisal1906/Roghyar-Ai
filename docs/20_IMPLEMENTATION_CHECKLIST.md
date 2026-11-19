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
| UI-06 | Next | Photo feature removal and Phase 1 final verification. |
