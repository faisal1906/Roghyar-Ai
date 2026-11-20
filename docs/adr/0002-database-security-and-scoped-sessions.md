# PostgreSQL schema and scoped sessions

Accepted for BE-01 engineering. Production provisioning and handling real patient records require separate approval. The authoritative PDF §§9 and 11–15, the root API contract, and the roadmap govern this decision.

## Schema and ownership

Alembic revision `0001_postgis` remains the extension baseline. `0002_core_schema` adds the private `roghyar` schema and `0003_rls` installs forced row-level security, least-privilege grants and the clinical audit trigger. An installation owner runs migrations, owns the schema and tables, and stays out of application traffic. The API runtime login is `roghyar_runtime`, a non-owner without superuser, role administration, replication, BYPASSRLS or role memberships.

| Relations | Storage and runtime access |
| --- | --- |
| hospitals, doctors, doctor_schedules | Geographic point (SRID 4326) with GiST, verification provenance, provider identifiers and local schedule hours. Runtime directory columns are limited to verified and published rows, with unpublished rows visible only to active admins of their hospital. Verifier and source fields are private. Physician profile photo metadata is confined to the provider directory. |
| patients, appointments | Verified subject binding, encrypted identity and visit reason, consent and appointment lifecycle timestamps. A patient sees their own verified patient and booking records. A hospital admin sees booking records at their assigned hospitals, with patient identity tables separately protected. The composite foreign key prevents a doctor being associated with a booking at another hospital. |
| assessments, chat_messages | Encrypted descriptions, safety answers and message content. A verified patient sees patient-bound rows. An anonymous browser must prove ownership of its signed session to see unlinked rows. Hospital admins have no access. The duration sentinel is separate from integer hours. |
| admin_users | Active hospital assignments are resolved in PostgreSQL. Runtime can read the minimal columns of its own active assignments and has no membership writes. |
| formulary, red_flag_rules | Versioned rows default to draft and inactive. Active requires an approved review, reviewer UUID and date. At most one active version per content key. Reviewed versions are immutable except for activation changes. A trigger records review and activation changes. Runtime can read only approved active rows and cannot set approval metadata. |
| otp_codes, audit_log | OTP digest, keyed phone lookup, purpose and expiry. Runtime has zero privileges. Audit records contain action and coded identifiers with no free-text patient data. |

The NOLOGIN maintenance role manages future privileged database workflows with explicit RLS policies. It has no clinical publication access. A separate NOLOGIN publication role can write clinical versions and activate signed content. Grant either role only to individually controlled service or operator identities under change control. The migration owner is a privileged break-glass identity. Backup, schema ownership, default grants and ability to assume these roles require review on the managed cluster.

Appointments reserve a doctor at a given start only while pending or confirmed. The partial unique index permits the same slot after cancellation or rescheduling. BK-03 owns transactional reassignment, overlapping intervals, schedule alignment, idempotency, holder expiry, assessment ownership checks, booking-code generation and legal state transitions. Database defaults record UTC instants. The service owns transitions and explicit updates to `status_changed_at`. Runtimes cannot modify timestamps in BE-01. Admin mutation auditing is required before enabling admin write endpoints. Display and slot generation use Asia/Karachi.

## Authentication and Supabase

FastAPI is the trusted API boundary. A private PostgreSQL connection authenticates directly as the dedicated runtime login. The `roghyar` schema stays out of Supabase exposed schemas. The browser uses neither the database login nor a Supabase service-role key. Verify managed extension placement, TLS certificates, role provisioning, database access rules and API schema exposure on a staging project before release. The migration supports PostGIS installed under `public` or `extensions`.

The scoped session verifies short-lived signed JWTs with an explicit issuer and audience, fixed HS256, UUID subject and recognized principal type. A separate high-entropy signing secret lives on the server. Credential issuance, rotation, revocation, OTP verification and hospital-admin identity onboarding remain later work. BE-01 has a verifier only. Patient credentials require a matching database subject with a verification timestamp. Hospital scope is derived from active database membership. An absent token yields a public session without ownership. Malformed, invalid, expired and wrong-kind credentials are denied. Maintenance cannot be selected by a token.

Scope is applied with parameterized transaction-local settings after opening a read-only transaction and verifying the actual database role. Commit and rollback clear the scope, including when a physical connection is reused. Custom PostgreSQL settings are writable by the runtime database login. They enforce isolation against untrusted HTTP input only when that login stays inside the trusted API, with parameterized SQL and fixed queries. Direct SQL access, compromised runtime credentials or a SQL injection would cross this boundary. Keep database connectivity private and run SQL injection and role/ACL review before collecting patient information.

## Encryption and retention

AES-256-GCM runs in the API with a fresh random nonce. The envelope binds its format version, record UUID and field purpose as authenticated data. The lookup index stores an HMAC-SHA-256 over canonical E.164 phone numbers under a separate key. It does not store the phone itself. Set `ROGHYAR_FIELD_ENCRYPTION_KEY` and `ROGHYAR_PHONE_LOOKUP_KEY` to distinct base64-encoded 32-byte random keys from a secret manager. Provision a separate high-entropy `ROGHYAR_SESSION_SIGNING_KEY`. Missing or malformed required keys fail closed. Provision and test rotation with a compatible decrypt/backfill window before processing real data. Lookup digests are pseudonymous and must be treated as sensitive. TLS and managed encryption at rest remain production controls. Restrict database error logging before intake because PostgreSQL constraint errors can include row details. Keep bind value logging and plaintext diagnostics disabled at the API, database and telemetry layers.

For assessments with no associated booking after 90 days, a future scheduled maintenance job must lock the assessment and confirm the absence of a booking in the same transaction used to anonymize it. Clear patient and session identifiers, text, questionnaire answers, safety details, demographics and clinical details in one update. Delete the retained chat for expired unbooked conversations according to the approved session/booking relationship. Record an anonymization timestamp, retain minimal operational metadata, and coordinate this job with transactional booking to prevent a race when checking for linked bookings. The retention constraint enforces a scrubbed row shape. Privacy approval must define booked-record retention, deletion requests, backup expiry, job identity and retry handling before patient intake. The ticket adds no live collection and runs no scheduled production job.

## Rollback and release

The RLS downgrade removes its grants, policies and triggers while retaining the data and reserved cluster roles. The schema downgrade is destructive and is exercised only on disposable CI. A production rollback requires a backup, a validated recovery plan and explicit authorization. PostGIS remains installed across rollback. Clinical guidance, booking, OTP issuance, patient submissions and provider publication remain gated. All live tests use synthetic rows in the disposable GitHub Actions database.

## BE-02 assessment writer and retention

`0004_assessments` adds a dedicated, non-owner login `roghyar_assessment_writer`. It can read only its own draft ciphertext and insert only the draft UUID, exactly one owner column, and three ciphertext columns. It cannot change ownership or write clinical fields, update existing drafts, or delete rows. The original runtime transactions remain read-only. Both logins verify PostgreSQL role attributes, then set the verified subject and kind for one transaction. Supabase clients must never receive either login.

Anonymous ownership requires a signed subject. Patient ownership resolves the verified subject to a patient row protected by RLS. There is no token issuance path. The internal service requires the test environment, explicit synthetic fixtures, a loopback connection to the disposable CI database, valid signing credentials and configured encryption keys. Public POST and GET return 503 before parsing input and never wire in the internal service. There is no production setting to enable collection.

The UUID retry key is combined with the authenticated kind and subject to derive a stable record ID. Atomic insert-on-conflict followed by an owner-filtered read returns the original draft for identical retries and raises a conflict for a changed payload. A rolled-back transaction leaves no reserved key. The scrubbed record retains an inaccessible identifier and timestamps, so reuse of a retired key cannot recreate the assessment. Clients must use fresh unpredictable UUID keys for new drafts. New rows encrypt the description, answers and remaining validation fields separately, binding each to the record UUID and field name. Legacy columns stay nullable and are not populated by the writer. The BE-02 rollback refuses to discard live encrypted drafts.

Retention runs within an explicit maintenance transaction and defaults to dry-run aggregate counts. The mutating transaction serializes retention invocations and locks appointment writes during ownership scrubbing. All linked appointments protect their assessment. It removes anonymous session chat only where no other live assessment shares that session. Patient-bound and ambiguously linked chat require a reviewed retention policy. Later scheduling should use a private daily job with an approved database identity, dry-run review, retry monitoring and backup/legal-hold checks. BK-03 must reject links to scrubbed assessments and coordinate locking with retention. No live scheduler or retention endpoint is enabled.

## BE-03 review trust

The database's active approval metadata provides a candidate set. The private evaluator additionally checks Ed25519 attestations against reviewer keys obtained from a separate clinical authority registry. Existing RLS, unique active-version index, immutable reviewed content and audit trigger remain in force. Migration `0005_red_flag_attestation` adds a structural constraint for active candidates. Real reviewer keys and publishing credentials are outstanding. [Rule schema and review procedure](../26_RED_FLAG_RULE_ENGINE.md)

## BE-05 formulary review trust

The formulary has its own signing domain and scoped reviewer registry. Migration `0006_formulary_attestation` adds a required envelope for active formulary rows while preserving the existing read and publisher roles. The private application verifier requires exact-content Ed25519 proof. Patient-facing medication selection remains closed. [Formulary security requirements](../28_FORMULARY_SAFETY.md)

## BE-06 quota store trust

BE-06 uses an external shared quota store. The adapter keeps a separate HMAC secret, takes peer identity from the ASGI transport or subjects from cryptographic verification, and fails closed for enabled protected workloads on store outage. Health and closed assessment routes have no quota dependency. Production Redis, TLS termination and ingress trust require staging approval. [API security and rate limits](../29_PHASE_2_ACCEPTANCE_REPORT.md)


GD-04 adds [forced-RLS home-care publication history](../39_TRANSLATION_AND_CLINICAL_PUBLISHING.md) and separate authenticated publisher custody.

## BK-02 booking calendar custody

The private booking calendar reads the existing LOC-04 and LOC-05 projections in one read-only snapshot. Its signed date evidence is stored in forced-RLS append-only history by the facility-scoped slot writer. The writer has no appointment privileges. The occupancy function returns an interval predicate and the calendar exposes no patient data. See [BK-02](../49_BK_02_SLOT_ENGINE.md).
