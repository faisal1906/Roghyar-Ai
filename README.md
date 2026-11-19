# ROGHYAR AI

Phase 0 is the local foundation for the healthcare-guidance PWA. The product specification PDF, engineering roadmap, and API contract are at the workspace root.

## Run locally

Use Python 3.11 or 3.12 and Node 22. The shell and liveness route can run without a local database. Copy `.env.example` to `.env` in the workspace root and activate a Python virtual environment. Run these commands from the root:

```text
python -m pip install -r apps/api/requirements-test.txt
python -m uvicorn roghyar.main:app --app-dir apps/api --no-access-log
```

In another terminal, run `npm ci` and `npm run dev` from `apps/web`, then open the URL Vite prints. The dev and preview servers proxy `/api/v1` to FastAPI at port 8000. Use `npm run build` followed by `npm run preview` to verify the service worker on localhost.

- `GET /api/v1/health/live` checks that the API responds.
- `GET /api/v1/health/ready` checks for PostGIS and the current schema and access revision. It returns HTTP 503 and a request ID when the database is unavailable or needs migration.
- `GET /openapi.json` exposes the implemented API schema.

GitHub Actions provides the disposable database for Phase 0 acceptance. For optional local database development, run `docker compose up -d --wait db` and `python -m alembic -c apps/api/alembic.ini upgrade head` from the root. Use `docker compose down` to stop the service and keep its named volume.

## Verify

- From `apps/api`, run `python -m pytest`. Settings read `.env` from the current working directory. To use the root environment file, set `PYTHONPATH=apps/api` and run `python -m pytest apps/api/tests` from the root.
- The [hosted workflow](.github/workflows/phase0.yml) tests the core schema, PostGIS geography and GiST query, readiness, database privileges and RLS on a disposable database. It verifies rollback to the pre-RLS revision and to base, preserving the image-managed extension. See the [database security ADR](docs/adr/0002-database-security-and-scoped-sessions.md) for the separate migration and runtime credentials.
- From `apps/web`, run `npm run build`, `npx playwright install chromium`, then `npm run test:e2e`. Stop any local servers on ports 8000 and 4173 first. Playwright launches its own API and PWA preview servers.
- To refresh OpenAPI, run `python -m roghyar.export_openapi ../../contracts/openapi.json` from `apps/api` and `npm run generate:api` from `apps/web`. Commit both generated files with API changes.

The shell uses the real liveness endpoint and renders an unavailable state offline. Clinical guidance, medicine publication, verified directories, reservations, and provider delivery are gated. Settings reject attempts to enable clinical guidance or appointment booking during Phase 0.

The optional local seed command (`python -m roghyar.seed` from `apps/api` with `ROGHYAR_SYNTHETIC_FIXTURES=true`) emits a labeled empty database scenario and writes no records.
