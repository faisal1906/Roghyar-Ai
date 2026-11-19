# Codex handoff record

## 2026-11-19 · Phase 0 foundation

- **Implemented** — F-01 PWA shell and real FastAPI health routes, F-02 local PostGIS Compose service and Alembic baseline, F-03 exported OpenAPI and generated TypeScript definitions with a typed HTTP client and CI, F-04 local-only empty seed manifest, F-05 validated settings and fail-closed release flags, and F-06 foundation ADR and risk register.
- **Verified locally** — Python suite passed 13 tests and skipped the live PostGIS test. Alembic rendered baseline upgrade SQL. OpenAPI and TypeScript regeneration produced identical files. The production PWA built successfully. Playwright passed one Chromium test covering the real API connection and offline shell. The seed command emitted the synthetic label and empty scenario.
- **Remaining** — Docker-backed Compose startup, live Alembic upgrade and downgrade, live PostGIS integration, and hosted CI. The original source-of-truth translation, repository conventions, risk register, Phase 0 prompt and handoff protocol were absent. This file records the current handoff.
- **Risk review** — No patient data, clinical content, provider directory entries, or booking confirmations are emitted. Clinical guidance and appointment booking flags reject activation. Database diagnostics are reduced to a structured event and request ID.
- **Next ticket** — Close F-02 verification by running Compose, the Alembic round trip, and the live PostGIS integration test in a Docker-enabled environment.

## 2026-11-19 · F-02 hosted verification setup

- GitHub Actions runs the pinned PostGIS image in a disposable service and verifies the Alembic revision and readiness at each checkpoint. Geographic column and GiST checks use transaction-scoped temporary data.
- The local backend run passed 13 tests and skipped the opt-in live test. A separate run with the GitHub Actions flag and the database opt-in omitted failed as intended.
- Phase 0 acceptance awaits the hosted result. The reconstruction of missing Phase 0 references follows the original PDF and available handoff materials.
- The [first hosted run](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44543664575) passed live PostGIS migration checks, backend tests, contract drift checks and the production build. Chromium setup failed because Playwright 1.52 does not support the Ubuntu 26.04 image selected by `ubuntu-latest`. The workflow now targets Ubuntu 24.04 to run the existing browser suite.

## 2026-11-19 · Phase 0 closed

- **Implemented** — F-02 migration and PostGIS assertions in `apps/api/tests/test_postgis.py` and the disposable service in `.github/workflows/phase0.yml`.
- **Verification** — [Hosted run 44544036033](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44544036033) succeeded on `e323d60858f2280543a462314fd77f1936728c50`. The Alembic CLI completed upgrade → downgrade → upgrade on PostgreSQL 16.9 and PostGIS 3.5.2. The initial head and base integration invocations passed one test each. The final head backend suite passed 14 with zero skips. Both head checks verified UTC, a temporary geography column with SRID 4326, the GiST query, and HTTP 200 readiness. Base preserved PostGIS, cleared the Alembic revision, and returned HTTP 503 readiness with HTTP 200 liveness. Contract regeneration matched Git, the production PWA build passed, and Chromium passed one Playwright E2E test.
- **Remaining blockers** — Clinical review, privacy approval and verified provider onboarding remain release gates. The original documentation pack is unavailable. The reconstructed Phase 0 references are recorded in the risk register.
- **Risk review** — The rollback ran only against the disposable CI service. Geographic probes used temporary points without provider or patient data. Clinical guidance and booking activation remain disabled.
- **Next ticket** — UI-01 for the shell, English navigation and trust row.

## 2026-11-19 · UI-01 application shell

- **Implemented** — PDF §§4.1 and 4.3 shell requirements in `apps/web/src` with reusable shell and home sections, teal and slate tokens, 48px controls, English navigation, and hash routes that retain their destination after offline reload.
- **Verification** — `npm run build` passed. `npm run test:e2e` passed five Chromium tests. Local pytest passed 13 tests and skipped the CI-only PostGIS test. Browser coverage in `apps/web/tests` exercises destination availability, route focus, keyboard disclosure, desktop/tablet/Android widths, reduced motion, API failure, offline reload and reconnect. Desktop and Galaxy S5 layouts were reviewed in Chromium. The [repository workflow](https://github.com/faisal1906/Roghyar-Ai/actions/workflows/phase0.yml) also runs live PostGIS and generated contract checks.
- **Risk review** — Static warning information and the direct Rescue 1122 telephone link work independently of service connectivity. Unavailable routes collect no symptoms or location, and the release flags stay closed. Physical handset call placement was outside the browser verification. [Khyber Pakhtunkhwa Rescue 1122](https://rescuekp.gov.pk/contact)
- **Rollback** — Revert the UI-01 commits. The ticket introduces no migration or API schema changes.
- **Next ticket** — UI-02 for the bilingual AI entry label, voice interaction, and editable transcript.

## 2026-11-19 · UI-02 voice entry

- **Implemented** — Reusable `VoiceInput` on Home and the AI Agent route, with both PDF §4.2 labels, a microphone control, English recognition language selection, and a typed and editable symptom description. `speech.ts` requires the browser's on-device speech API and configures `en`, `ur-PK`, or `ps-AF`. Optional language-pack setup requires a separate click. Recordings end on stop, timeout, cancellation, or navigation and are aborted when the document is hidden. No audio or transcript is uploaded or stored persistently.
- **Verification** — `npm run build` and all 12 Chromium E2E tests passed. The local backend suite passed 13 tests and skipped the CI-only PostGIS integration test. Chromium covers recognition lifecycle with browser API doubles, language-pack setup and timeout, permission denial, unavailable languages, editable drafts, keyboard interaction, reduced motion, and the existing offline and emergency-link behavior.
- **Remaining blockers** — A fresh Windows Chrome 155 profile reported English as downloadable and Urdu and Pashto as unavailable. A separate native English setup probe did not finish in its 30-second window. Physical-device recognition remains unverified. AI-05 owns server STT provider selection, language coverage and privacy approval. Submission for analysis remains unavailable.
- **Risk review** — The browser can run the microphone only after an explicit action. Clear and reload remove the draft. Emergency access and diagnosis notices remain visible. The API contract, database, and clinical release flags are unchanged. [Web Speech API on-device requirements](https://webaudio.github.io/web-speech-api/#dom-speechrecognition-processlocally)
- **Rollback** — Revert this UI-only change. The ticket introduces no migrations or API changes.
- **Next ticket** — UI-03 for the assessment form and duration inputs.

## 2026-11-19 · UI-03 symptom assessment

- **Implemented** — `apps/web/src/assessment.tsx` and the pure `assessment-draft.ts` model implement PDF §5 input and local review. The app shares the same draft between Home, AI Agent, and Assess. Exact durations normalize to hours and the more-than-a-year choice remains a separate value. Native radio chips and numeric fields keep keyboard behavior and field associations. Clear assessment confirms and resets all shared fields.
- **Verification** — TypeScript and the production build passed, along with 6 frontend unit tests and 17 Chromium tests. Local pytest passed 13 with the CI-only PostGIS test skipped. Regenerated OpenAPI and TypeScript matched Git. Browser coverage includes first-error focus, the 360px layout, 48px controls, reduced motion and local review without a write request. GitHub Actions runs the same frontend checks and live PostGIS.
- **Risk review** — Drafts use in-memory React state. Local review neither submits nor classifies the entered symptoms. Rescue 1122 remains available through the shell. No photo input, persistence, API changes, or release flag changes were introduced.
- **Remaining** — `follow-up-questions.tsx` provides optional single- and multiple-choice controls for up to five questions. Reviewed symptom-dependent questions and the safe submission pipeline belong to the later backend and AI work.
- **Rollback** — Revert UI-03. There are no API or database migrations.
- **Next ticket** — UI-04 for the emergency alert interface and local fail-safe path.

## 2026-11-19 · UI-04 emergency help

- **Implemented in PDF §7.1–7.2** — Manual opening from the persistent header, emergency notice and assessment. The accessible full-screen modal uses two `tel:1122` actions, a warning icon with a reduced-motion variant, generic warning signs and immediate actions, explicit acknowledgement, focus containment and restoration, and a background scroll lock. Opening the alert interrupts any active microphone session while preserving the draft.
- **Offline and privacy** — The cached PWA opens the alert without backend, model, maps, location or messaging requests. The alert provides an offline medical-condition and hospital lookup limitation and an online verified-care unavailable state. The app does not dispatch an ambulance. [Khyber Pakhtunkhwa Rescue 1122](https://rescuekp.gov.pk/contact)
- **Deferred from PDF §7** — §7.1/§7.3 detection, approved category-specific content and emergency precedence remain with BE-03 and BE-04. Verified facility name, emergency status, distance, travel time, directions and phone require LOC-01 and LOC-04–06. Location sharing requires separate explicit consent and provider delivery confirmation. Spoken warnings require reviewed translations and AI-05, and tone and haptics require device validation. Typed ports exist without active implementations.
- **Verification** — The local production build and OpenAPI consistency check passed, along with 9 frontend unit tests, 24 Chromium tests, and 13 backend tests with the CI-only PostGIS test skipped. Chromium measured manual opening at 26.3 ms against the one-second UI target on the final local run. Browser checks also cover focus, scroll lock, offline reload, API outage, privacy, 320px and 360px layouts, 48px targets, reduced motion and at least 7:1 computed contrast. Physical dialing, screen-reader device checks and clinical detection testing remain open.
- **Rollback** — Revert UI-04. There are no API, database or clinical release flag changes.
- **Next ticket** — UI-05 for responsive accessibility and PWA hardening.

## 2026-11-19 · UI-05 accessibility and PWA hardening

- **Implemented** — Matching visible and accessible Rescue labels, wrapping header and controls, measured sticky-header scroll clearance, validation announcements, and visible emergency call focus in short zoomed viewports. The existing English interface and approved bilingual entry label are retained. Voice and optional health fields retain automatic text direction for user-entered script.
- **PWA** — `vite.config.ts`, `pwa-update.tsx` and `pwa-state.ts` define a root-scoped standalone manifest and an explicit worker activation flow. The cache allowlist covers public shell assets, and only root navigations receive the offline shell. Active tabs remain open when a worker takes control. A reload that clears a draft requires separate confirmation. An install failure leaves the current shell running and offers a retry. Legacy UI-04 tabs may need to close before their first update can activate. [Vite PWA update guidance](https://vite-pwa-org.netlify.app/guide/prompt-for-update.html)
- **Browser verification** — `accessibility.spec.ts` checks every route, review and emergency help at 320, 360, 375, 390, 768, 1024 and 1440 CSS pixels. Axe scans reported zero violations for the selected WCAG 2.2 A/AA rules. Chromium verified 32px browser default text, Urdu and Pashto text direction, keyboard flow, and viewport reflow. A separate Windows Chromium check set native 200% and 400% zoom and verified the emergency call and 640px/320px reflow. These results cover browser automation. Physical screen readers, dialing, install prompts and low-end Android remain open. [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- **Offline and installation** — `pwa.spec.ts` checks the production manifest, decoded 192px and 512px PNGs and Chromium installability diagnostics. It exercises actual worker update and failure events, two-tab draft preservation, cached route reload, emergency access, private API failure offline and no health draft in browser storage. UI entry and navigation made no automatic camera, microphone or location requests.
- **Local results** — 11 frontend unit tests and 40 Chromium tests passed. TypeScript, production build and OpenAPI consistency passed. Pytest passed 13 tests with the live PostGIS test reserved for hosted CI. Desktop and mobile screenshots were visually reviewed.
- **Performance** — Five fresh Windows Chromium contexts per production build at 360×640, 4× CPU throttling, 80ms network latency and 200,000 bytes/s download. UI-04 → UI-05 median cold first/largest contentful paint was 1,368 → 1,496ms. Median offline first paint was 248 → 248ms. Each run recorded zero layout shift and three long tasks. Startup app asset requests fell from three to two after removing the separate registration script. Gzipped JS/CSS went from 68.25/3.61kB to 69.51/3.80kB. The browser sample shows a slower median cold paint and does not establish device performance.
- **Risk review and rollback** — Clinical flags, backend, PostGIS and generated API contract stay unchanged. The rollback is a UI-only revert. Keep the existing worker and reload behavior in mind when rolling back.
- **Next ticket** — UI-06 for photo feature removal and Phase 1 final verification.

## 2026-11-19 · UI-06 Phase 1 audit

- **Audit and fixes** — No symptom-photo implementation existed in frontend, backend, contracts, migrations, fixtures, dependencies or assets. The review found the missing PDF §4.2 floating bilingual shortcut. The shortcut routes to the existing editable draft and uses no device permission. It stays in-flow on compact screens to keep assessment controls unobstructed.
- **Guards** — Browser checks cover the text-only flow, no camera or automatic microphone, private local review and offline emergency. Backend checks verify the health-only route and schema inventory, reject a synthetic upload and compare dependencies and migration history. Live PostGIS inspects application-owned columns. Frontend tests inventory the supported runtime and tooling dependencies.
- **Acceptance** — [Hosted run 44570578763](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44570578763) passed 12 frontend unit tests, 42 Chromium tests with axe, and 17 backend tests with live PostGIS. TypeScript, the production build and generated contract drift checks passed. Phase 1 engineering is complete. The [acceptance report](25_PHASE_1_ACCEPTANCE_REPORT.md) records remaining physical-device and clinical requirements separately.
- **Rollback** — Revert UI-06 if necessary. Production API routes, schema, database migrations and release flags are unchanged.
- **Next ticket** — BE-01 for database schema, migrations, RLS and scoped sessions.
