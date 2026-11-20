# Versioned API contract (FastAPI /api/v1)

Source: PDF pp. 18–19. This document extends the PDF's endpoint list with production necessities. Implement Pydantic models, authenticated dependencies, rate limits, idempotency semantics and generated OpenAPI; no hardcoded fake successes.

## Shared protocol

BE-06 applies a common error envelope, a server-generated `X-Request-ID` response header and `Cache-Control: no-store`. The error body repeats the header ID. Framework validation, authentication, rate-limit and unexpected errors use fixed messages and null details. Enabled future routes may enforce a Redis quota and return `RATE_LIMITED` with `Retry-After`, or `DEPENDENCY_UNAVAILABLE` if quota storage fails. Closed assessment routes and health probes bypass quota storage. [HTTP security and throttling policy](docs/29_PHASE_2_ACCEPTANCE_REPORT.md)

HTTPS; JSON; `request_id` returned on errors; ISO-8601 UTC timestamps; frontend formats to PKT. Strict schema with bounded text lengths and coordinates. Example error:

```json
{"error":{"code":"SLOT_UNAVAILABLE","message":"That time was just booked. Choose another available slot.","request_id":"...","details":null}}
```

Error codes should include `VALIDATION_ERROR` 422, `UNAUTHENTICATED` 401, `FORBIDDEN` 403, `NOT_FOUND` 404, `CONFLICT` 409, `RATE_LIMITED` 429, `PROVIDER_UNAVAILABLE` 503, `CLINICAL_CONTENT_UNAVAILABLE` 503. Avoid leaking whether a phone number is registered.

## Agent and assessment

BE-05 adds internal signed formulary verification and synthetic screening. All existing triage states deny medication selection. No medicine endpoint or public response contract is added. [Formulary safety boundary](docs/28_FORMULARY_SAFETY.md)

BE-04 adds an internal precedence service with urgency floors, uncertainty and rule provenance. It has no public HTTP response contract or warning publication. [Deterministic triage interface](docs/27_TRIAGE_PRECEDENCE.md)

BE-03 adds a private rule evaluation interface for BE-04. It accepts structured evidence and carries explicit configuration, evidence and evaluation failure states. It adds no public HTTP contract. [Rule engine and safety boundaries](docs/26_RED_FLAG_RULE_ENGINE.md)

BE-02 exposes reserved POST and GET paths that return HTTP 503 with a request ID and `Cache-Control: no-store`. The POST path does not read the body. Public OpenAPI describes this available behavior. The internal disposable-test contract is an encrypted draft with a UUID, UTC creation timestamp, `draft` status and original validated fields. It has no clinical result. A short-lived signed credential supplies ownership, and a UUID retry key is scoped to that owner. Payloads contain no identity or ownership override. The routes below describe later activation.

Internal input fields are `text`, optional `language` (`en`, `ur`, `ps`), `severity` (`mild`, `moderate`, `severe`), either `duration_hours` (1, 2, 12 or whole days through 365) or `more_than_one_year`, whole-year `age` (0–120), optional `sex`, and optional `allergies`, `conditions`, `current_medicines` and `answers`. The engineering limits are 4,000 description characters, 16 items of 120 characters per safety list, and at most five question IDs with a boolean, an option ID, or up to eight option IDs per answer. IDs are at most 48 lowercase ASCII letters, digits or underscores and start with a letter. These limits bound storage and match the PDF's five-question maximum. Text is trimmed and NFC-normalized. Internal validation errors contain only known top-level field names and a generic message.

- `POST /agent/message`: request `{text,session_id?,language_hint?,previous_turn_id?}`. Response `{session_id,relevance:{category,relevant,confidence},reply:{text,language,speech_available},follow_up_chips[],emergency?}`. If an emergency is detected, include immediate emergency signal; do not wait for generated reply. Decline off-topic without hallucinated medical guidance.
- `POST /assessments`: request `{text,language?,severity,duration_hours?,more_than_one_year?,age,sex?,allergies?,conditions?,current_medicines?,answers?,session_id?}`. Server uses own rule engine, not client-supplied level. Response `{assessment_id,level,red_flags[],specialty,condition_code?,guidance:{summary,steps[],medicines[],see_doctor_if[],followup_hours?},next_screen,clinical_content_status}`. `next_screen` `emergency` or `guidance`; no unrestricted medical LLM text. Never return unapproved dose.
- `GET /assessments/{id}` (engineering addition): only with owning signed session or verified patient; restore guidance without public IDs.

## Providers and geolocation

- `GET /hospitals/nearby?lat=&lng=&type=&emergency_24_7=&open_now=&sort=&specialty=`: verified records, distance_km, optional `eta_minutes` with provenance; radius expansion. No persistent location without explicit consent.
- `GET /hospitals/search?q=`: verified name/alias autocomplete across locations; suggestions include city and verification freshness.
- `GET /hospitals/{id}?lat=&lng=`: public details, contacts, departments, optional distance.
- `GET /doctors?specialty=&hospital_id=&lat=&lng=`: verified providers, working hours, last-verified date. Under 10 relevant search facets queryable.
- `GET /doctors/{id}`: verified full profile or clear unverified/inactive state; no synthetic doctor in production.
- `GET /doctors/{id}/slots?date=YYYY-MM-DD`: 30-day real slots with DB reservations excluded; response `{date,timezone:"Asia/Karachi",slot_minutes,slots:[{start_utc,start_local,available}]}`. No stale availability guarantees; POST re-check.

## Identity and appointments

- `POST /otp/request`: `{phone,purpose:"booking"|"appointments"}`, generic acceptance response, strict rate limit and anti-abuse.
- `POST /otp/verify`: `{phone,code,purpose}` returns short-lived session token / secure HttpOnly cookie; OTP one-time and bound to normalized phone and purpose.
- `POST /appointments`: `{doctor_id,slot_start,full_name,age,guardian_name?,phone,gender?,reason?,consent:true,assessment_id?}`, `Idempotency-Key` mandatory; requires verified phone. Response `{id,booking_code,status,doctor,hospital,slot_start,slot_end,notification_status,provider_ack_status}`. `409` for conflict; **do not return success until committed**.
- `GET /appointments/mine`: verified patient identity; only own records.
- `PATCH /appointments/{id}`: `{action:"cancel"|"reschedule",new_slot_start?}`, ownership and valid statuses enforced; if same doctor slot contention, transaction.
- `POST /emergency/notify`: explicitly authorized recipient/location share request, throttle and consent; **only claim sent if provider confirmed**. Prefer native share sheet for direct patient control if messaging gateway unavailable.

## Admin namespace

`GET/POST/PATCH/DELETE /admin/doctors`, `/admin/schedules`, `GET/PATCH /admin/appointments`, and secure login/session endpoints. Non-destructive archive rather than losing historical booking links. Server derives authorized facility; no cross-hospital ID selection for unauthorized records. Audit mutations.

## Event and retry rules

Implement notification delivery through durable jobs; never retry booking creation blindly without idempotency key. `409 SLOT_UNAVAILABLE` is expected under races. Expose real provider failures as `pending/failed/unavailable`, not `sent`. Version contract changes and add contract tests.

### AI-01 private routing contract

The [AI-01 contract](docs/30_LANGUAGE_RELEVANCE_CLASSIFICATION.md) is internal. The planned agent endpoint above is not implemented, and these models are excluded from the generated public schema. Routing labels cannot authorize clinical decisions or bypass the independent safety pipeline.

The [AI-02 conversation state](docs/31_CONVERSATION_MEMORY_AND_GUARDS.md) is private server state and excluded from OpenAPI.

The [AI-03 triage bridge](docs/32_STRUCTURED_TRIAGE_VALIDATION.md) is private and excluded from OpenAPI.

The [AI-04 reply writer](docs/33_CONTENT_LIMITED_REPLY_WRITER.md) is private and excluded from public OpenAPI.

[GD-01 guidance](docs/36_FOUR_LEVEL_GUIDANCE_FLOW.md) is a private contract and excluded from public OpenAPI.

The [structured home-care bundle](docs/37_REVIEWED_HOME_CARE_CONTENT.md) is a private GD-02 contract.

[GD-03 medication screening](docs/38_DETERMINISTIC_MEDICATION_SAFETY.md) is private and excluded from public OpenAPI.
