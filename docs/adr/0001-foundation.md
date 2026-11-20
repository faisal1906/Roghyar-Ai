# ADR 0001 · Phase 0 foundation

Status: Accepted for local development

Use React and TypeScript with Vite and Workbox for the PWA, FastAPI with Pydantic for the versioned API, Alembic for schema changes, and PostgreSQL with PostGIS for geography. These choices follow the Phase 0 roadmap and PDF sections 12–15. The web shell consumes generated types from FastAPI OpenAPI through a same-origin API path. Cache the shell and its static assets for offline startup. Keep API responses out of the service worker cache.

Require a migrated database for readiness and allow liveness independently. Use UTC database sessions and reserve Asia/Karachi for future appointment presentation. Keep infrastructure connection diagnostics out of responses and logs.

Clinical and provider routes require their own implementation and approvals. Phase 0 exposes health routes only. Local fixtures require opt-in, carry the synthetic label, and are barred by production settings. The initial scenario is empty because the foundation owns no clinical or directory tables.

The proposed monorepo layout is provisional until the original repository conventions are available.

## AI-01 inference composition

The [private classification gateway](../30_LANGUAGE_RELEVANCE_CLASSIFICATION.md) is injected by trusted server code only after explicit data-transfer approval. No settings, public routes, storage, provider keys or network calls are added to application startup. Future composition must ensure TLS, restricted egress and redacted transport telemetry.

AI-02 uses [storage-free conversation transitions](../31_CONVERSATION_MEMORY_AND_GUARDS.md) with a trusted current snapshot. Future integration requires privacy-approved atomic snapshot custody.

AI-03 keeps model candidates out of the signed rule evaluator. See [AI-03 trust boundaries](../32_STRUCTURED_TRIAGE_VALIDATION.md).

AI-04 uses [exact signed reply artifacts and BE-04 warning bindings](../33_CONTENT_LIMITED_REPLY_WRITER.md). Provider-produced prose is outside the rendering contract.

AI-05 permits only a fixed-copy local speech preview. See [speech capabilities and fallback design](../34_SPEECH_CAPABILITY_AND_FALLBACKS.md).

AI-06 records synthetic engineering evidence separately from model quality and clinical approval. See [Phase 3 acceptance](../35_PHASE_3_AI_SAFETY_ACCEPTANCE.md).

GD-01 uses separate policy authorization and content verification. See [guidance trust boundaries](../36_FOUR_LEVEL_GUIDANCE_FLOW.md).

GD-02 keeps [structured clinical content](../37_REVIEWED_HOME_CARE_CONTENT.md) in immutable private snapshots pending durable publishing design.

GD-03 separates [medication policy authorization](../38_DETERMINISTIC_MEDICATION_SAFETY.md) from clinical guidance and signed formulary content.
