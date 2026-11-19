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
