# Master execution plan for Codex

Implement **eight phases** preserving PDF §16 sequence. Durations in the PDF are estimates only, not guaranteed schedule. At the end of each phase update actual progress in `docs/24_CODEX_HANDOFF_PROTOCOL.md` without rewriting historical evidence.

## Phase 0 — Foundation and decisions (prerequisite, engineering addition)

**Tickets** `F-01` scaffold monorepo with React TypeScript PWA + FastAPI, health endpoints; `F-02` Docker Postgres/PostGIS and Alembic migrations; `F-03` API schema + generated client + CI + pytest/Playwright; `F-04` synthetic seed model isolated from production; `F-05` environment/settings, feature flags, secure logs; `F-06` ADR decisions and risk register. **Gate:** local one-command-ish setup, green tests, zero exposed secrets.

## Phase 1 — Interface upgrade (PDF phase 1)

**Tickets** `UI-01` shell, English navigation and trust row; `UI-02` voice entry dual label + editable transcript + text fallback; `UI-03` assessment form/duration; `UI-04` emergency overlay and local fail-safe path; `UI-05` responsive, accessible PWA shell; `UI-06` remove all photo plumbing. **Gate:** UI E2E for consent, duration, no photos, 1122 visibility, low-end mobile.

## Phase 2 — Backend and safety foundation

**Tickets** `BE-01` migrations/schema/RLS and scoped sessions; `BE-02` validation and assessment persistence; `BE-03` clinician-reviewed red-flag rule interpreter and versioning scaffold; `BE-04` deterministic triage precedence; `BE-05` formulary approval and selector scaffold; `BE-06` API tests, errors, rate limits. **Gate:** emergency decision possible with LLM completely turned off; no default approved medicine fixtures.

## Phase 3 — AI and multilingual voice

**Tickets** `AI-01` classification language/relevance JSON; `AI-02` short follow-up memory and guard; `AI-03` structured triage + validators; `AI-04` content-limited reply writer; `AI-05` browser STT/TTS with provider fallbacks and transparency; `AI-06` prompt injection, EN/UR/PS regression suite. **Gate:** published language/guard quality reports and no model-controlled doses.

## Phase 4 — Instant guidance and clinical content

**Tickets** `GD-01` four-level guidance flow; `GD-02` reviewed home-care content repository; `GD-03` deterministic medication contraindication rules; `GD-04` approved translation library and clinical publishing interface; `GD-05` accessibility, age/pregnancy/unknown-risk tests. **Gate:** clinical reviewer signs actual content; if not, meds remain disabled and tasks flagged blocked.

## Phase 5 — Location, provider directory and maps

**Tickets** `LOC-01` permission/watchPosition/denied support; `LOC-02` PostGIS 5/15/30/60 km query and filters; `LOC-03` hospital autocomplete and fuzzy names; `LOC-04` verified directory import/review; `LOC-05` doctor-specialty matching, genuine schedule; `LOC-06` directions and optional ETA integration. **Gate:** no false verified tags or made-up ETA; genuine provider provenance recorded.

## Phase 6 — Booking, OTP and hospital administration

**Tickets** `BK-01` OTP provider/limits/session; `BK-02` slot engine with exceptions and real 30-day schedules; `BK-03` atomic reservations + idempotency; `BK-04` My Appointments + cancel/reschedule; `BK-05` SMS/WhatsApp receipt, reminder jobs; `BK-06` hospital RBAC/dashboard/audit. **Gate:** concurrent same-slot test exactly one succeeds and real pilot clinic honors bookings.

## Phase 7 — Hardening and prelaunch

**Tickets** `QA-01` 200 clinician emergencies including EN/UR/PS + target metrics; `QA-02` input classification coverage; `QA-03` security/RLS penetration tests; `QA-04` accessibility/device tests and WCAG 7:1 alert; `QA-05` backups, monitoring, runbooks, privacy retention; `QA-06` production provider sandbox/live workflows and sign-offs. **Gate:** all critical safety blockers closed.

## Phase 8 — Pilot launch

**Tickets** `LA-01` final installable PWA/Android validation; `LA-02` staged deployment and database migration review; `LA-03` local pilot onboarding in Mardan; `LA-04` monitoring, incident contacts and retrospective; `LA-05` optional Android store distribution decision. **Gate:** written approval and verified operational readiness; no automatic deploy.

## Ticket policy

For each ticket: reference exact requirements, affected files, API/DB changes, failure states, tests, manual steps, open dependency, rollback and truthful completion label. **Never claim completed just because front-end UI looks correct.** Use `prompts/PHASE_*.md` in order.
