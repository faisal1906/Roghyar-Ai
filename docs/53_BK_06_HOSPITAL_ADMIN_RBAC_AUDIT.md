# BK-06 Hospital administration

Engineering acceptance is OPEN. The private foundation adds independently signed facility membership revisions, a pluggable identity provider, persisted short-lived staff bearer sessions and facility-scoped reads. Legacy admin flags and patient sessions confer no access. Staff inactivity or a membership revision change invalidates older sessions. Evidence documents and raw staff credentials are kept outside audit rows.

The private route factory exposes session, bounded appointment/doctor/schedule/audit reads, and durable doctor or schedule review requests. The public application leaves the factory unmounted. Signed LOC-05 revisions and BK-02 confirmations remain under their existing governance. The review requests require a separate authorized processing and review workflow.

Revision `0016_hospital_admin_rbac` introduces distinct issuer, provisioning and runtime roles with forced RLS. The runtime role reads a minimum operational projection without patient, OTP or clinical data and cannot change appointments or provider verification. The proposal audit records are append-only for the runtime role.

Remaining engineering requirements include the connected and accessibility-tested dashboard, independently authorized clinic acknowledgement and appointment mutations, notification integration, governed submission handoff, independent acceptance evidence and the Phase 6 rollup. Real staff onboarding, identity provider approval, retention, external key custody and deployment remain separate release gates.
