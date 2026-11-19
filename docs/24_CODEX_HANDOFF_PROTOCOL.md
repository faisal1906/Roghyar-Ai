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
