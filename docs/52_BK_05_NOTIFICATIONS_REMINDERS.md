# BK-05 appointment notifications and reminders

BK-05 adds private notification engineering. The normal API mounts no notification callback or preference route and starts no worker. The only dispatch authorization wired by this ticket is an expiring synthetic permit, which rejects the real gateway adapters. Production dispatch requires independent release authorization and an approved executor.

## Events and consent

| Durable history | Intent |
| --- | --- |
| BK-03 reservation created | Booking request recorded |
| BK-04 cancellation | Cancellation recorded |
| BK-04 reschedule | Rescheduling recorded and a separate successor request notice |
| Clinic-confirmed appointment with signed acknowledgement | 24-hour and 2-hour reminder intents where eligible |

SQL triggers enqueue notices inside the authoritative booking or lifecycle transaction. There is no provider I/O in either transaction. No default consent, historical backfill or cross-channel fallback is enabled. The verified appointments-purpose holder can record an explicit SMS or WhatsApp preference and optional reminder consent before booking. An append-only revision binds the channel, locale and policy version. Updating or withdrawing it invalidates unsent intents bound to the old version. Missing or withdrawn consent withholds dispatch. OTP SMS and general booking consent confer no notification consent.

The worker resolves only the appointment owner's encrypted verified phone through a lease-bound security-definer function. It checks the live OTP identity, consent, appointment state, dates, channel, template and phone lookup before handing off. Jobs contain no raw phone or rendered body. The owner-scoped delivery status view is separate from BK-03 and BK-04 immutable response snapshots.

## Templates and gateways

The exact channel, locale, event and version select a single signed template. Ed25519 verification enforces reviewer scope, expiry and revocation. Only the generated Asia/Karachi time placeholder is supported. English, Urdu and Pashto are explicit choices without locale fallback. Real wording, translations and localization require review.

SMS and official WhatsApp Business adapters require allowlisted HTTPS, server credentials, bounded transport and no redirects. WhatsApp uses an approved sender and template identifier. The stable job UUID is the provider request identity. A gateway 202 proves acceptance only after the typed response binds that UUID. Only a signed provider callback can mark delivery. A callback never changes appointment status or claims clinic acknowledgement.

## Leasing, retry and recovery

Workers claim bounded batches using `FOR UPDATE SKIP LOCKED`. A 30-second lease binds job, worker and fence. Preparation validates authority before committing a distinct submitting state. Provider I/O happens outside the database transaction. Another eligibility check immediately precedes I/O. Cancellation, rescheduling or consent withdrawal cancels queued and leased jobs. In-flight jobs are marked invalidated while preserving any already accepted fact. A committed handoff can race later cancellation or revocation. External transmission cannot be retracted.

Expired pre-submission leases become retryable. An expired submitting lease becomes unknown. Connection or explicitly retryable provider outcomes use bounded exponential backoff and at most four attempts. Unknown results require a signed reconciliation event and are never automatically resent. A callback uses provider-scoped signatures, fresh timestamps, job/request UUID and provider message ID, immutable receipt custody and idempotent event IDs. Signed receipt facts are retained after cancellation.

Redis budgets cover recipient, appointment, channel, template and aggregate sends. A database gate limits concurrent in-flight requests per provider. Quota failures defer submission without incrementing provider attempts. Full retry exhaustion is represented as a terminal failed job.

## Database and operations

Revision `0015_notification_outbox` adds forced-RLS consent, outbox and receipt custody without automatic notification creation. Separate restricted worker and receipt accounts have no patient or clinical table access. A NOLOGIN custody role owns the small recipient and event-trigger boundaries. Migration rollback requires empty custody or approved archival.

Operators should stop the synthetic executor or remove a channel from its configured provider map to pause it. Inspect recent state totals, due age, provider failure classifications and sanitized callback-rejection events. Use verified provider reconciliation for unknown outcomes. Keep terminal failures for approved investigation and do not replay them as new sends without separate authorization. Revoke obsolete consent and templates, remove compromised credentials, and reconcile only through authenticated receipts. Production retention for jobs, attempts, provider IDs, consent, templates, backups and receipts remains subject to privacy approval.

Hosted CI regenerates earlier acceptance artifacts and exact-head `bk05-acceptance` evidence. It tests live PostgreSQL and Redis, synthetic adapters and signed callbacks. Real SMS and WhatsApp Business onboarding, sender and template approval, subprocessor disclosure, patient consent policy, billing ceilings, delivery reliability, credential rotation, monitoring and real clinic acknowledgement remain open. BK-06 owns hospital administration and audit integration.
