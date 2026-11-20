# GD-04 translation and clinical publishing

The publication scope is home-care purpose plus condition and applicability. Revision identity is content ID and version. PostgreSQL stores the exact parsed signed body and immutable translation attestations as JSONB, verified by canonical serialization. The signed GD-02 active bit means eligible for operational activation. The separate state column starts at draft. Content changes need new signed revisions. Runtime can see active rows only.

The private service uses authenticated PostgreSQL session identities on a separate operator allowlist. It assumes custody of those login credentials on an isolated publishing service. Authors cannot witness their own review. Clinical signing keys and language signing keys come from an independently administered trust snapshot, never from stored artifact metadata. Language reviewers independently sign the exact content binding and locale review reference in a separate domain. Exact EN/UR/PS variants are returned with no fallback.

The transaction takes a scope lock, checks generation and expected active identity, verifies current trust and swaps active state. A partial unique index protects scope uniqueness. The trigger appends actor, state, revision, digest, UTC time and transaction identity, without duplicating wording. Runtime and assessment-writer roles have no publication grants. Forced RLS protects both tables. The migration refuses to remove nonempty history.

A rollback revalidates a previously superseded or inactive artifact. Revoked revisions cannot reactivate. Trust is refreshed per selection and failures withhold content. Trust onboarding, durable revocation distribution and production operator credentials remain unprovisioned. Raw SQL publisher credentials must stay within the trusted service. Database state alone never grants clinical or language authority and runtime always performs verification.

The repository implements GD-02's exact lookup interface for GD-01 injection. AI-04 must still verify its separate signed reply. BE-04 warning resolution and GD-03 medication authority remain separate and unchanged. Broader warning/reply publishing adapters are deferred. No patient fields, public routes, keys or seeded content are added.

Qualified reviewers must check source accuracy, warning and negation equivalence, step order, locale/script and cultural clarity, follow-up interval, and scope before signing. Synthetic keys and wording prove software boundaries only. QA-01, QA-04, model quality and privacy remain external gates.

GD-05 adds independent rollback and writer denial evidence. See [Phase 4 acceptance](40_PHASE_4_ENGINEERING_ACCEPTANCE.md) and the exact-head CI artifact.

Live publication checks remain separate from the GD-05 in-memory chain. Injection of the published and revoked PostgreSQL repository through the full flow remains uncovered.

GD-05 adds PostgreSQL reader injection through the real private guidance and medication services, with fresh translation revocation and content revocation checks.
The GD-05 live integration now also tests stale approval after atomic revision replacement, fresh authorization, and subsequent revocation.
PR #24 verified atomic revision replacement and fresh revocation through the restricted PostgreSQL-backed guidance chain. This complements the historical in-memory fixture.
The current GD-05 PostgreSQL-backed chain verifies restricted-runtime publication, replacement and revocation. Earlier notes describing this chain as missing are historical.
