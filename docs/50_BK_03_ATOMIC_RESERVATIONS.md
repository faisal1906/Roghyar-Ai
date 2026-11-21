# BK-03 atomic reservations and idempotency

The private booking router is exercised in an isolated application. Public appointment writes and notifications remain disabled.

## Request and identity

A bounded JSON body supplies doctor, timezone-aware minute-boundary start, name, age, canonicalizable Pakistani mobile and explicit consent. An opaque idempotency key is mandatory. The booking-purpose BK-01 signature is checked before opening the transaction. The dedicated writer then locks and revalidates the persisted OTP session and its identity, checks the consumed challenge binding and compares the verified phone lookup. A revocation that acquires the relevant row lock first prevents a booking. A booking that holds the lock orders before a later revocation. No clinical or administrative token carries booking authority.

PostgreSQL RLS relates patient and booking rows to the current live booking-session capability. The service verifies the JWT before installing that capability as a transaction-local setting. Keep the booking DB login private and treat the session UUID as sensitive. The writer cannot inspect or edit OTP tables, assessments, reviewer records, or clinical custody. Profile creation occurs in the same transaction as the reservation. Names, phone and the optional reason use existing authenticated encryption. An existing verified profile stays unchanged. Assessment linking and minor booking are blocked pending approved authorization contracts.

## Authority, contention and commit

The transaction takes the LOC-04/05 publication lock in shared mode and the BK-02 date-confirmation lock in shared mode. Publication, withdrawal and new confirmation writes acquire the corresponding exclusive locks. The booking re-evaluates the current projection, full reviewed schedule digest, exact provider confirmation and deadline, local date, lead time, and exact slot. External reviewer revocation is rechecked immediately before commit. It is outside PostgreSQL lock ordering and requires a dedicated custody guarantee before release.

A doctor-scoped transaction lock serializes ordinary writes and prevents competing inserts from deadlocking in GiST. PostgreSQL's independent exclusion constraint blocks any overlapping active interval for the same doctor, including a write outside the booking service. Ranges are half-open. Pending and confirmed statuses block the interval. No slot hold is created before the transaction.

Appointment, encrypted profile (where needed), replay result and append-only event commit together. The response is pending with unavailable notification and provider acknowledgement. It contains a random 96-bit booking reference and derived provider IDs. BK-04 owns appointment lifecycle operations. BK-05 owns delivery and provider acknowledgement integration.

## Durable replay and administration

The replay key is scoped to the verified subject and stored as a domain-separated HMAC. A separate HMAC domain covers canonical normalized request fields. Same-key successful retries require a currently valid booking token and return the original durable result. Changed requests conflict. Failed attempts leave no committed idempotency row. An uncertain commit returns an explicit same-key retry response. Committed keys must remain queryable for the approved recovery interval. Retention, rotation and lifecycle interaction must be designed before real use.

Revision `0013_atomic_reservations` enables `btree_gist` and checks for historical active overlaps before installing the constraint. If it blocks a migration, a privileged operator must inspect the affected interval records using the same doctor, active status and half-open overlap comparison, and resolve each conflict with the data owner. The migration never modifies appointment history. Downgrade requires empty reservation custody or approved archival and leaves the shared extension installed.

## Acceptance

The exact-head `bk03-acceptance` artifact reports PostgreSQL integration tests, Redis-backed OTP prerequisites, migration round trips, regression totals and disposable timing samples. Real provider schedules and acceptance, public intake, production key operations, data retention, minors, assessment ownership linkage, representative load, clinical acceptance and physical-device verification remain unresolved.
