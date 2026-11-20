# BK-02 private booking calendar

BK-02 extends the LOC-05 doctor projection and slot generator. The normal application exposes neither a slot endpoint nor an appointment endpoint. Synthetic confirmations exercise the contract without establishing operational availability.

## Authority and slot selection

A read starts with the current LOC-04 hospital, LOC-05 doctor, affiliation, schedule, and specialty taxonomy. The local range is today through local day 30 inclusive in Asia/Karachi. The existing generator excludes past and lead-time-ineligible slots and uses whole, non-overlapping session intervals. Overnight sessions require a different reviewed schedule contract.

A separate Ed25519 confirmation binds doctor, facility, both publication revisions, the canonical full schedule digest, local date, exact ordered session intervals and duration, evidence hash, organization, source version, reviewer, effective time and expiry. One independently allowlisted provider authority signs the date. Missing, stale, mismatched or invalid evidence withholds slots. LOC-05 closures take priority. A replacement requires both the reviewed replacement and the matching date confirmation. Signed closure, leave, holiday, withdrawal and unavailable states withhold slots. The latest sequence is authoritative and a previous sequence is never used as a fallback. A terminal withdrawal for a date cannot be reactivated through this writer.

The gateway adapter is explicitly configured and disabled by default. It fetches one bounded document/evidence pair over a fixed allowlisted HTTPS endpoint without redirects or retries. The same evidence and signature checks apply to manually imported documents. An unsuccessful import records no confirmation. An existing unexpired confirmation remains eligible in the private view until withdrawn, superseded or expired. BK-02 does not approve that outage policy for patient-facing use.

## Custody, privacy and consistency

Migration `0012_booking_slot_governance` adds forced-RLS append-only confirmation history and a facility scope table. The dedicated writer is restricted to configured facilities and has no appointment privileges. An advisory transaction lock serializes sequences by doctor, facility and date. Audit history records the writer role and time with the signed revision. Private key custody and human operator identification remain operational dependencies. Empty disposable custody supports downgrade. Nonempty history requires approved archival.

The read uses one PostgreSQL repeatable-read snapshot, including a bounded set of daily confirmation rows and one batched occupancy query. Only pending and confirmed intervals block overlapping slots at that doctor and facility. No patient fields cross the occupancy boundary. The result binds the doctor and hospital revisions, schedule digest, confirmation sequence and evidence deadline. The reader rechecks expiry and provider trust before returning. Commits made after the database snapshot are visible on subsequent reads. BK-03 owns atomic revalidation, reservation and idempotency.

## Verification and release

Hosted CI regenerates the exact-head `bk02-acceptance` artifact with mandatory cases, migration evidence, earlier acceptance evidence and test counts. It records disposable database timings for one-day, seven-day, full-window, exception-bearing, occupied and concurrent queries. Timing includes signature verification and database I/O and excludes external provider I/O. These measurements cover the disposable fixture.

Real hospital and doctor onboarding, authorized source contracts, key and revocation operations, representative load, public slot activation and actual reservations remain pending. Public OTP, booking and clinical activation remain closed. BK-03 is the next implementation ticket.

