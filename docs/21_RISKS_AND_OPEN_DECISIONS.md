# Risks and open decisions

| Item | Status | Effect |
| --- | --- | --- |
| Original Phase 0 governance documents | Reconstructed from available sources | Reconcile the replacements with the original versions if the documentation pack becomes available. |
| Clinical content and emergency rules | Pending clinician review | Patient-facing assessments and medicine publication stay closed. |
| Providers, clinic onboarding and privacy approvals | Pending | No real reservations, messaging or patient data collection. |
| Database and hosted CI verification | Closed | The disposable GitHub Actions database passed the live migration round trip, PostGIS checks and full suite. [Run 44544036033](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44544036033) |
| Clinical feature availability | Gated by later tickets | Symptom submission, assessment, emergency detection, guidance, verified directories and booking remain unavailable. The voice input prepares a draft in memory. |
| UI-01 device verification | Chromium viewport emulation | Desktop and Galaxy S5 layouts visually reviewed. Offline cache and telephone link verified in Chromium. Physical handset dialer and connectivity checks remain part of later device QA. |
| UI-02 recognition coverage | Browser and language pack dependent | A fresh Windows Chrome 155 profile reported English as downloadable and Urdu and Pashto as unavailable. Typed input covers unsupported browsers and languages. Physical-device recognition and language quality remain unverified. |
| AI-05 server speech-to-text | Pending provider and privacy approval | Confirm language coverage, consent and audio retention before adding a server fallback. UI-02 sends no audio or transcript to a remote service. |
