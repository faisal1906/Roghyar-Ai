# Risks and open decisions

| Item | Status | Effect |
| --- | --- | --- |
| Original Phase 0 governance documents | Reconstructed from available sources | Reconcile the replacements with the original versions if the documentation pack becomes available. |
| Clinical content and emergency rules | Pending clinician review | Clinical analysis and medicine publication stay closed. |
| Providers, clinic onboarding and privacy approvals | Pending | No real reservations, messaging or patient data collection. |
| Database and hosted CI verification | Closed | The disposable GitHub Actions database passed the live migration round trip, PostGIS checks and full suite. [Run 44544036033](https://github.com/faisal1906/Roghyar-Ai/actions/runs/44544036033) |
| Clinical feature availability | Gated by later tickets | Symptom submission, clinical analysis, automatic emergency detection, guidance, verified directories and booking remain unavailable. Manual emergency help and local draft review are available. |
| UI-01 device verification | Chromium viewport emulation | Desktop and Galaxy S5 layouts visually reviewed. Offline cache and telephone link verified in Chromium. Physical handset dialer and connectivity checks remain part of later device QA. |
| UI-02 recognition coverage | Browser and language pack dependent | A fresh Windows Chrome 155 profile reported English as downloadable and Urdu and Pashto as unavailable. Typed input covers unsupported browsers and languages. Physical-device recognition and language quality remain unverified. |
| AI-05 server speech-to-text | Pending provider and privacy approval | Confirm language coverage, consent and audio retention before adding a server fallback. UI-02 sends no audio or transcript to a remote service. |
| UI-03 clinical submission and adaptive questions | Gated | The assessment form can validate and review a local draft. Symptom-dependent questions and server submission require reviewed clinical rules and the later backend and AI pipeline. |
| UI-04 emergency activation and content | Manual UI only | The generic warning is available on device. BE-03 and BE-04 own clinician-reviewed detection and approved alert content. Browser latency tests measure manual activation only. |
| UI-04 device and provider capabilities | Deferred | LOC-01 and LOC-04–06 own consent and verified nearby care. Location sharing requires consent, privacy review and confirmed delivery support. AI-05 and later device QA own reviewed speech, tone and vibration verification. |
| UI-05 browser and device coverage | Windows Chromium verified | Automated axe checks, native 200%/400% desktop zoom and viewport/font simulations passed locally. Physical Android installation, low-end performance, phone dialing, speech engines and assistive-technology behavior remain open. |
| UI-05 offline updates | Verified with two live Chromium tabs | Preparing an update preserves each draft. Reloading with a draft requires separate confirmation. Tabs still running the old UI-04 shell may need to close before the first prompt-managed worker can take over. |
| UI-06 photo capability inventories | Regression guard under implementation | Future routes, models, migrations and runtime dependencies require deliberate policy updates. Review camera and image data paths as part of each change. |
