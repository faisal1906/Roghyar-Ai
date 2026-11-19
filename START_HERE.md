# Prompt to paste into Codex — begin ROGHYAR AI

You are the implementation engineer for **ROGHYAR AI**. This is a real healthcare-guidance PWA with real appointment booking, not a concept demo.

1. Read `AGENTS.md`, `README.md`, the original `reference/ROGHYAR_AI_Final_Model_Specification.pdf`, `docs/00_SOURCE_OF_TRUTH.md`, `docs/19_ROADMAP_AND_TICKETS.md`, `docs/21_RISKS_AND_OPEN_DECISIONS.md`, and `docs/24_CODEX_HANDOFF_PROTOCOL.md`.
2. Audit current repo and `git status`; preserve anything already there. If empty, initialize the proposed monorepo from `docs/05_REPOSITORY_CONVENTIONS.md`.
3. **Implement Phase 0** now: React + TypeScript PWA scaffold, FastAPI service, PostgreSQL/PostGIS local development, Alembic, validated settings and `.env.example`, OpenAPI contract, tests and CI. Prefer actual runnable code and a documented local run flow to a lengthy proposal.
4. Use synthetic, visibly labeled local fixtures only. Keep clinical medicine publication and real doctor/clinic reservations disabled until actual human verification. Build robust seams/interfaces for missing providers. Do not silently fabricate OTP, hospitals, verified clinicians or operational booking success.
5. Run the tests/build that are possible. Report commands and actual results; identify anything you couldn't execute.
6. Follow `docs/24_CODEX_HANDOFF_PROTOCOL.md` for your final report. State the **single next ticket** and the user inputs required only if genuinely blocking.

Important: Safety checks must run without LLM. The uploaded PDF remains authoritative for product specifics, but clinician review, privacy and real provider onboarding are hard release gates. No production deployment or live third-party actions without my explicit permission.
