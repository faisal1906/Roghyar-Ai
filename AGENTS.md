# Codex rules — ROGHYAR AI

Read this file **before modifying anything**, then `README.md`, `docs/00_SOURCE_OF_TRUTH.md`, `docs/19_ROADMAP_AND_TICKETS.md`, and the documents relevant to the current ticket.

## Permanent priorities

1. Patient safety and truthful functionality are higher priority than shipping speed or pleasing UI.
2. The PDF in `reference/` is the **product source of truth**; these docs translate its requirements into implementation tasks. If a proposed implementation can create medical danger, privacy abuse or inaccurate availability claims, **stop the affected feature and record a blocker**. Never silently weaken a medical control.
3. Never output a diagnosis as certain. Triage is guidance, not a diagnosis. Never pretend an LLM is a doctor.
4. **Emergency rule checks must function without an LLM and precede generative advice.** Use explicit safety fallbacks when uncertain or offline. Critical emergency UI must not depend on waiting for a model response.
5. **No unapproved medicines or doses.** Only signed, active, versioned, age/contraindication-screened formulary rows may reach a patient. Empty formulary means non-drug guidance and referral, never guessed dosing.
6. **No fictional live data.** Never invent verified doctors, hospitals, PMDC numbers, directions, working hours, slot openings, or delivery success. Fixtures must be loudly labeled `SYNTHETIC—DEVELOPMENT ONLY` and excluded from production.
7. Keep secrets on server; minimize collection and third-party sharing of symptoms, names, numbers and location. No patient health details in analytics/error logs.
8. `AGENTS.md` and the docs are committed; source, tests and migrations are committed. Do not commit `.env`, service keys, generated secret files, access tokens or real patient records.
9. Implement with typed contracts, migration files, auth checks, structured logs, deterministic tests, accessibility and error states. No TODO-only "finished" claims.
10. Do **not** delete or rewrite the source PDF or governance docs without explicit authorization.

## Execution discipline

- Work on **one phase/ticket** at a time; inspect existing files first and preserve functional work.
- Before coding: briefly state plan and affected files; log unresolved inputs in `docs/21_RISKS_AND_OPEN_DECISIONS.md`.
- Prefer small cohesive changes and reversible database migrations. Do not deploy to production or run destructive migrations without user authorization.
- Add unit, integration and E2E tests for every vertical slice. When no provider key exists, implement a real provider adapter plus a sandbox stub clearly separated by environment; report a **provider integration blocker**, not success.
- Validate all untrusted text, location coordinates, booking fields and AI JSON at the server boundary. Enforce permissions server-side.
- Use `Asia/Karachi` for presentation and doctor schedules; store timestamps in UTC, not naive local timestamps.
- Keep English for all static interface labels except exactly specified bilingual entry label `Speak to AI Agent` / `AI Agent se Pochy`. AI response text can be Urdu/Pashto scripts.
- No photo upload or camera-based symptom recognition. Do not add payments, telemedicine consultation, e-prescriptions, diagnostic claims or a chatbot that answers general unrelated topics.

## Required end-of-ticket report

**Implemented** (with paths) · **Verification** (commands and actual results) · **Remaining blockers** (human approvals/providers/known issues) · **Risk review** · **Next smallest ticket**. Clearly distinguish real from mocked/incomplete behavior.
