# BK-04 My Appointments and lifecycle

BK-04 is a private engineering contract. The standard app leaves GET and PATCH unmounted. No provider notification, acknowledgement, or public patient intake occurs.

## Identity and read contract

The server verifies an appointments-purpose BK-01 JWT. The dedicated lifecycle login binds the signed token to the persisted session, active identity, consumed verification challenge, signing-key ID, and token timestamps on each transaction. A distinct HMAC capability combines the signing key, session UUID and a purpose-specific domain. The database holds only its SHA-256 digest. Knowing a subject UUID, session UUID, phone number or booking reference does not authorize SQL access. Older appointments-purpose sessions without the digest require a fresh sign-in. The booking-purpose contract remains separate.

GET lists owned records, newest slot first, using at most 50 records and an exclusive `(slot_start,id)` cursor. Historical provider IDs remain visible after withdrawal. Current provider eligibility is explicitly unevaluated. JSON includes UTC and Asia/Karachi times, prior/successor IDs, and truthful unavailable delivery states. Binding data, private identity fields, medical reasons, and unpublished profiles stay outside the response. Same-start historical records use the appointment UUID as a tiebreaker. Page cursors carry only sort boundaries. Concurrent changes may appear in later reads.

## Mutation contract and state machine

PATCH requires an opaque `Idempotency-Key`, strict JSON, HTTPS and an explicit allowed Origin. Unknown and non-owned appointment identifiers return the same 404. Unsupported state, cutoff, target conflicts, and key reuse return safe 409 codes. An uncertain database commit returns 503 with same-key recovery instructions. This private idempotent extension does not modify the generated public schema.

| Source status | Cancel | Reschedule |
| --- | --- | --- |
| pending or confirmed | original becomes cancelled | original becomes rescheduled and a linked pending successor is created |
| completed, no_show, cancelled or rescheduled | denied | denied |

A configurable synthetic cutoff defaults to 30 minutes before the original start, with equality treated as closed. Real cutoffs require provider and policy approval. Cancellation works on an owned current appointment even after provider withdrawal. Rescheduling requires the same doctor and facility and reuses the BK-03/BK-02 trusted slot validator, including current signed date confirmation, lead time, and overlap checks. The original keeps its timestamps and encrypted history. A successor has a fresh booking code and does not copy the encrypted reason or assessment link.

## Consistency and custody

The transaction locks persisted OTP authority, the mutation key, and then the relevant booking authority and doctor. The locked original appointment is rechecked before mutation. Revocation that wins the identity lock blocks the mutation. Governance writes serialize against rescheduling through BK-03's publication and date locks. External reviewer trust is rechecked before commit. PostgreSQL exclusion remains the last occupancy boundary.

Status retirement, successor insertion, lineage, and replay evidence commit together. A failure rolls back the original and any attempted successor. Same-owner replay returns the recorded mutation result. Both changing the action and changing the target while reusing the key produce an idempotency conflict. The BK-03 creation snapshot remains immutable. GET supplies current status.

Revision `0014_appointment_lifecycle` adds a dedicated NOINHERIT role, per-owner forced RLS, a bounded-history index, and append-only event custody. A deferrable constraint trigger requires a matching event for lifecycle changes. New successors and predecessor events have unique references and must share patient, doctor and facility, with successors created in the same transaction after the predecessor. Cross-patient reads, direct history deletion, and accessing medical or OTP custody are denied. Downgrade preserves existing bookings and refuses to discard lifecycle history without approved archival.

## Verification and outstanding decisions

Hosted CI generates exact-head `bk04-acceptance` evidence. It includes mandatory PostgreSQL and Redis-backed OTP cases, migration round trips, historical regression artifacts, and timings for pagination, cancellation, rescheduling, replay and target conflict. Disposable workloads do not establish representative performance.

Real cutoff policy, provider confirmation and acceptance, SMS provisioning, key and retention approval, patient onboarding, and clinical and device acceptance remain open. BK-05 owns delivery and reminders. Public OTP, slot discovery, booking, lifecycle operations and clinical activation remain closed.
