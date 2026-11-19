# ROGHYAR AI

Phase 0 is the local foundation for the healthcare-guidance PWA. The product specification PDF, engineering roadmap, and API contract are at the workspace root.

## Run locally

Use Python 3.11 or 3.12, Node 22, and Docker Compose v2. Copy `.env.example` to `.env` in the workspace root and activate a Python virtual environment. Run these commands from the root:

```text
docker compose up -d --wait db
python -m pip install -r apps/api/requirements-test.txt
python -m alembic -c apps/api/alembic.ini upgrade head
python -m uvicorn roghyar.main:app --app-dir apps/api --no-access-log
```

In another terminal, run `npm ci` and `npm run dev` from `apps/web`, then open the URL Vite prints. The dev and preview servers proxy `/api/v1` to FastAPI at port 8000. Use `npm run build` followed by `npm run preview` to verify the service worker on localhost.

- `GET /api/v1/health/live` checks that the API responds.
- `GET /api/v1/health/ready` checks that PostGIS and the foundation revision are present. It returns HTTP 503 and a request ID when the database is unavailable.
- `GET /openapi.json` exposes the implemented API schema.

Run `docker compose down` to stop the database and keep its named volume. The baseline Alembic downgrade removes the revision while preserving PostGIS, which may be owned by the database image or service.

## Verify

- From `apps/api`, run `python -m pytest`. Set `ROGHYAR_TEST_POSTGIS=1` with the migrated database running to include the PostGIS integration test. Settings read `.env` from the current working directory. Run with the root on your working directory for custom database settings and set `PYTHONPATH=apps/api` before running pytest from there.
- From `apps/web`, run `npm run build`, `npx playwright install chromium`, then `npm run test:e2e`. Stop any local servers on ports 8000 and 4173 first. Playwright launches its own API and PWA preview servers.
- To refresh OpenAPI, run `python -m roghyar.export_openapi ../../contracts/openapi.json` from `apps/api` and `npm run generate:api` from `apps/web`. Commit both generated files with API changes.

The shell uses the real liveness endpoint and renders an unavailable state offline. Clinical guidance, medicine publication, verified directories, reservations, and provider delivery are gated. Settings reject attempts to enable clinical guidance or appointment booking during Phase 0.

The optional local seed command (`python -m roghyar.seed` from `apps/api` with `ROGHYAR_SYNTHETIC_FIXTURES=true`) emits a labeled empty database scenario and writes no records.
