# AI-02 bounded conversation memory

PDF sections 5.4 and 10.2 specify up to five adaptive questions and short topic continuity. The private service in `apps/api/roghyar/conversation.py` implements the lifecycle and option validation. A reference to the current topic prevents an off-topic classification from triggering a definitive refusal. Uncertain labels stay uncertain. No semantic reference resolution or translation occurs.

## State and limits

Version 1 has a conversation UUID, verified anonymous-session or patient scope, UTC creation and fixed expiry, event IDs, asked question keys, pending key, facts, and recent messages with current language and routing labels. The event and question counts are lengths of immutable tuples. Limits are 15 minutes, 20 accepted events, five unique questions and five facts. Questions use single-choice typed tokens with two to eight options. Retain at most four messages, 600 Unicode code points each and 1,800 aggregate. Text is normalized through AI-01. Trimming is oldest-first and stops with a context-limit result if it would discard the active topic. Structured facts and pending questions are separate from trimmed message history. Other limits are engineering defaults.

| Action | Transition |
|---|---|
| Start | New, empty UUID-bound state |
| Ask approved question | Awaiting answer, with exactly one pending key |
| Answer with matching key and valid option | Answer received and pending cleared |
| Explicit correction | Replace the current confirmed value and link its event ID as superseded |
| Classified message | Update routing and language, preserve pending question and facts |
| Uncertain message | Clarification required, or preserve awaiting answer if a question is pending |
| Complete / cancel / expire | Clear retained content and leave a terminal marker |
| Reset | Replace authoritative state with a new UUID and empty conversation |
| Failure | Typed reason with no replacement snapshot, except expiry which returns cleared state |

Missing answers remain pending. Facts distinguish direct reports, corrections, suggestions and unknown values. Only direct reports and corrections cross the private AI-03 handoff as **unverified user reports**. Superseded values are removed. Arbitrary text or AI labels cannot create facts. Clinical question selection and a verified question catalog are release blockers. The shipped default catalog is empty.

## Trust and privacy

Trusted server code must supply a verified principal, the current authoritative state and any independently verified BE-04 decision. Never deserialize state, identity, catalog or triage decisions from a client or provider. A reviewed emergency or urgent decision yields a safety handoff before inference or transition; it is never rewritten. Unsafe-request routing prevents normal question handling and completion. It supplies no crisis instructions. Manual offline Rescue 1122 stays independent.

The service is storage-free. Callers must atomically compare and replace the current snapshot, and retire the old UUID on reset, to prevent concurrent forks or rollback to an old snapshot. Replaying an accepted event against current state is rejected, as are stale revisions and mismatched conversation or owner IDs. Do not retain prior snapshots after cancellation, expiry or reset. Public use requires approved identity, encrypted atomic state custody, retention and reviewed safety orchestration. No browser, Redis or SQL chat persistence or public agent route is introduced.

The optional AI-01 adapter receives only the current message, with no history, identity or credentials from the memory state. The default is unconfigured. Inference errors preserve prior state and expose only typed reason codes. Synthetic tests measure software behavior and in-process overhead, with no claim of multilingual or clinical performance.
