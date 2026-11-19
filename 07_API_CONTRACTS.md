# Versioned API contract (FastAPI /api/v1)

Source: PDF pp. 18–19. This document extends the PDF's endpoint list with production necessities. Implement Pydantic models, authenticated dependencies, rate limits, idempotency semantics and generated OpenAPI; no hardcoded fake successes.

## Shared protocol

HTTPS; JSON; `request_id` returned on errors; ISO-8601 UTC timestamps; frontend formats to PKT. Strict schema with bounded text lengths and coordinates. Example error:

```json
{"error":{"code":"SLOT_UNAVAILABLE","message":"That time was just booked. Choose another available slot.","request_id":"...","details":null}}
```

Error codes should include `VALIDATION_ERROR` 422, `UNAUTHENTICATED` 401, `FORBIDDEN` 403, `NOT_FOUND` 404, `CONFLICT` 409, `RATE_LIMITED` 429, `PROVIDER_UNAVAILABLE` 503, `CLINICAL_CONTENT_UNAVAILABLE` 503. Avoid leaking whether a phone number is registered.

## Agent and assessment

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
