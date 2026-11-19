# Repository conventions

Reconstructed from the Phase 0 roadmap, specification and committed repository.

| Path | Purpose |
| --- | --- |
| `apps/web` | React/TypeScript PWA, generated API types and Playwright |
| `apps/api` | FastAPI, settings, Alembic migrations and pytest |
| `contracts` | Generated OpenAPI contract |
| `.github/workflows` | Hosted verification with a disposable PostGIS service |
| `docs` | Governance, ADRs, implementation checklist and handoff |
| Repository root | Original PDF, kickoff prompt, roadmap and API contract |

Commit source, migrations, tests and generated contracts together. Keep keys, local environment files and patient records out of Git. Keep database sessions in UTC and schedule presentation in Asia/Karachi. Follow the test commands in the [README](../README.md). Run destructive migration verification only on the disposable CI service.
