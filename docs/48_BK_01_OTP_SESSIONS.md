# BK-01 OTP and verified-phone sessions

The private OTP router exposes a request and verification contract to an isolated test app. The normal server does not mount it. Booking, assessment, and patient onboarding require separate authorization.

## Challenge and provider

ASCII Pakistani mobile forms `03XXXXXXXXX` and `+923XXXXXXXXX` share one canonical identity. The service generates a six-digit value with Python's cryptographic random generator. PostgreSQL stores a domain-separated keyed verifier bound to the lookup, purpose, and challenge UUID and stores a keyed phone lookup. No plaintext phone or OTP is required in the OTP tables.

The challenge begins pending, becomes active on the exact gateway acknowledgement, and expires after five minutes. A newer request supersedes the older pending or active challenge. A late acknowledgement cannot reactivate it. Five invalid attempts lock the challenge. PostgreSQL serializes challenge transitions by phone lookup and commits consumption, verified identity, and session creation together. The verified identity is separate from existing patient and hospital principals.

The gateway uses an explicitly allowlisted public IPv4 HTTPS endpoint and requires a certificate valid for that address. Its dedicated credential stays server-side. A single bounded request expects HTTP 202 with the exact challenge identifier and accepted status. Acknowledgement means that the gateway accepted the request. Delivery, vendor uptime, and live billing remain operational checks.

## Limits and sessions

Shared Redis counters enforce peer, phone, purpose, resend cooldown, and aggregate send budget. Limits are fixed-window and consumed conservatively when a later check denies the operation. Peer identity comes from the connection scope and forwarded client headers have no authority here. Redis errors fail closed. Counter keys use HMAC-derived identifiers.

The private contract requires the configured HTTPS Origin. Requests use bounded JSON. Verify returns a short-lived, purpose-scoped bearer token through a no-store response. No cookie or browser token storage is implemented. Future browser transport and CSRF controls require a separate integration review. The token has a separate signing key, issuer, audience and type. Authorization requires the matching live session, consumed verification and unrevoked identity in PostgreSQL. Session revocation takes effect on subsequent authorization checks. A verified-phone subject has no clinical or administrative privileges.

## Database and operations

Migration `0011_otp_sessions` adds forced-RLS challenge, identity and session tables and a dedicated writer role. The standard runtime and assessment writer have no custody-table privileges. Downgrade is allowed for an empty custody history and requires an approved archival procedure once records exist.

Hosted CI regenerates Phase 2 through Phase 5 evidence and publishes the exact-head `bk01-acceptance` artifact, including JUnit-mapped acceptance gates and migration evidence. Disposable tests use real PostgreSQL and Redis with synthetic gateway inputs. Production SMS onboarding, provider billing limits, key custody and rotation, abuse monitoring, retention and purge approval, and public patient identity binding remain open. BK-02 owns slots and genuine schedules. BK-03 owns booking transactions.
