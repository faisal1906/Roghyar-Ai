# Phase 0 implementation checklist

| Ticket | Status | Evidence |
| --- | --- | --- |
| F-01 | Implemented | PWA and live API verified by the Chromium foundation test in the initial handoff. |
| F-02 | Hosted verification pending | The workflow runs upgrade, downgrade and re-upgrade with database and readiness assertions at each checkpoint. |
| F-03 | Hosted verification pending | The workflow regenerates the contract and types, checks for drift, builds the PWA and runs Playwright. |
| F-04 | Implemented | Seed tests cover the labeled empty scenario and local opt-in. |
| F-05 | Implemented | Settings tests cover database configuration, credential redaction and release flags. |
| F-06 | Implemented | Foundation ADR, source references, risk register and handoff are present. Phase 0 documents were reconstructed where originals were absent. |

Close Phase 0 after recording the successful GitHub Actions run for the verification commit. Phase 1 work is gated on that result.
