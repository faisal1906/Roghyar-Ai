# GD-01 four-level guidance flow

PDF section 6 defines EMERGENCY, URGENT, SEE A DOCTOR and HOME CARE, represented by the existing GuidanceLevel enum. AI-03 SEE_DOCTOR and HOME_CARE remain candidate spellings without authority.

| Deterministic safety state | Internal guidance |
| --- | --- |
| Reviewed emergency | Emergency floor and decision retained regardless of warning availability |
| Reviewed urgent | Urgent floor and decision retained |
| Urgent with unresolved risk | Urgent floor retained, final level withheld, further assessment required |
| Incomplete evidence | Further assessment required |
| Absent rules, invalid configuration, evaluator failure | Explicit unavailable state. BE-03 groups invalid signatures with invalid rule configuration |
| No reviewed match | No lower level unless independent approval and exact content both verify |

The caller supplies a verified principal, owner-bound authoritative conversation snapshot, assessment event and revision from trusted custody. AI-03 validates ownership, expiry and assessment input before mapping current direct/corrected reports, then calls BE-03/04. The bridge has no inference adapter and no proposal input. Unknown, unsupported and contradictory observations cannot establish lower guidance.

A lower decision requires an independently signed, domain-separated v1 Ed25519 authorization using the dedicated policy trust store and a configured active policy/version. It binds the owner and canonical assessment/report digest (including source and supersession events), conversation revision, event, vocabulary version, signed rule snapshot, reviewed screening reference and exact content version/digest. Only authorized clinical-scope identities with current unrevoked keys qualify, and the approval expires independently. The screening reference attests to screening performed by a qualified authority. This ticket provides no production screening implementation, key provisioning or approval issuance. Both trust configuration and active policy set default to empty.

AI-04 verifies signed copy for the exact intent, state, artifact binding and locale. Emergency and urgent use the existing BE-04 warning resolver. Missing or invalid content leaves a reviewed floor intact. Lower guidance requires rendered copy before authorization. This private result carries only the bounded signed reply and internal decision provenance. No public response or persistence is added.

GD-02 owns reviewed summary, steps, warning text and follow-up intervals. GD-03 owns independently authorized medication screening and exact formulary output. GD-01 has only a constant unavailable medication status and leaves BE-05 denials intact. GD-04 owns publication, revocation and translation operations. Unknown pregnancy, allergies and medicine history cannot be assumed absent. QA-01 validation, privacy, identity and authoritative state custody remain blockers. Manual offline Rescue 1122 remains independent.
