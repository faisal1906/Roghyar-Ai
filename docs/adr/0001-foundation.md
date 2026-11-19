# ADR 0001 · Phase 0 foundation

Status: Accepted for local development

Use React and TypeScript with Vite and Workbox for the PWA, FastAPI with Pydantic for the versioned API, Alembic for schema changes, and PostgreSQL with PostGIS for geography. These choices follow the Phase 0 roadmap and PDF sections 12–15. The web shell consumes generated types from FastAPI OpenAPI through a same-origin API path. Cache the shell and its static assets for offline startup. Keep API responses out of the service worker cache.

Require a migrated database for readiness and allow liveness independently. Use UTC database sessions and reserve Asia/Karachi for future appointment presentation. Keep infrastructure connection diagnostics out of responses and logs.

Clinical and provider routes require their own implementation and approvals. Phase 0 exposes health routes only. Local fixtures require opt-in, carry the synthetic label, and are barred by production settings. The initial scenario is empty because the foundation owns no clinical or directory tables.

The proposed monorepo layout is provisional until the original repository conventions are available.
