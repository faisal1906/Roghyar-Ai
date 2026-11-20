# AI-01 language and relevance classification

AI-01 implements the private single-message routing contract from PDF §§10.1–10.2. No FastAPI router, provider registration, chat history or database migration is added. Existing assessment requests still receive 503 before body consumption. The browser's voice recognition language is a preference for speech capture. Its editable transcript remains in memory.

## Input and provider contract

`ClassificationInput` is a strict object with `text` and an optional nullable `language_hint` (`en`, `ur`, `ps`). The raw input limit is 4,000 Unicode code points, matching the assessment description limit. Text is validated for meaningful content, normalized to NFC and trimmed. Tabs, line breaks and joining characters used in supported scripts are allowed. Other Unicode control/format characters, private-use characters, unassigned characters and unpaired surrogates are rejected. Ownership, roles, history and all other keys are rejected. Public ingress size checks still belong at the future HTTP boundary.

The asynchronous adapter returns UTF-8 JSON with exactly three required fields.

| Field | Meaning |
|---|---|
| `languages` | Unique ordered list of up to three supported language identifiers, most prominent first. An empty list represents unresolved or unsupported language. A hint never determines the result. |
| `relevance_categories` | Unique list of up to nine PDF section 10.2 categories. Empty means abstention. |
| `relevance_confidence` | Finite numeric score from zero to one. Exactly `null` when categories are empty. |

`ProviderOutput` rejects invalid JSON, duplicate keys, invalid labels, extra or missing fields, contradictory score/category combinations and outputs larger than 4,096 bytes. There are no fields for explanations, prompt contents, dosage, diagnoses, observations, clinical levels or warnings.

## Routing policy

| Category | Intended route |
|---|---|
| `symptom`, `disease_info`, `medicine`, `wellness` | In-scope health conversation. The `medicine` label grants no medication authority. |
| `doctor_or_hospital`, `booking_or_app_help` | In-scope healthcare navigation or app help. |
| `greeting` | In-scope greeting when clear. An ambiguous fragment can abstain. |
| `off_topic` | Candidate for future limited refusal, subject to independent safety routing. |
| `unsafe_request` | Separate restricted route for requests to harm oneself or others. This ticket implements no crisis reply or emergency decision. |

For multiple labels, select the first in this order: unsafe request, symptom, disease information, medicine, doctor or hospital, booking or app help, wellness, greeting, off-topic. This is a routing tie-break, with no clinical meaning. An inferred medical label survives accompanying unrelated text. If the adapter misses the medical meaning entirely, the routing service cannot recover it. An off-topic label must never block an independent safety check or suppress an authenticated emergency result. AI-02/03 must enforce and verify that boundary before any public use. This ticket uses the PDF's clarification threshold below 0.60. Confidence remains a provider estimate of relevance.

| Result variant | Fields in addition to `status` and `classifier_version` (`1`) |
|---|---|
| `classified` | `languages`, `relevance_category`, `relevance_confidence`, `needs_clarification=false`, `reason_code=classified` |
| `uncertain` | `languages`, `needs_clarification=true`, `reason_code=language_unknown` or `relevance_uncertain`; no definitive category |
| `rejected` | `reason_code=invalid_input` |
| `unavailable` | `reason_code` for unconfigured or unavailable provider, timeout, bad JSON or schema, oversized output, or unexpected failure |

## Provider, trust and privacy

`ClassificationService()` returns `provider_unconfigured` on valid input. It is unused by application startup. `HTTPSClassificationAdapter` implements a fixed, vendor-neutral HTTPS gateway contract using the already pinned HTTPX version. A trusted server composition must explicitly approve transfer and supply a server-side bearer credential. Each request contains `contract_version`, fixed `instructions`, and separate untrusted `input`. HTTPS verification is on, environment proxies and redirects are disabled, compressed replies are refused, and the byte quota is enforced during streaming. There are no retries. The default service deadline is 3 seconds with cooperative cancellation, and the adapter has its own transport timeouts. Caller cancellation propagates. No request or response body is logged or stored by the application. Production review must confirm gateway security, tracing redaction, retention, authorization, network egress and secret custody before wiring real traffic. Prompt separation and schema validation limit authority; neither guarantees semantic resistance to injection.

AI-02 owns memory, clarification and any later limited response. AI-03 owns validated evidence extraction. Raw text and routing scores cannot create trusted observations, signed rule matches or outcomes from BE-03/04. The manual offline Rescue 1122 path remains independent of AI inference.

## Verification and activation gates

The synthetic corpus in `apps/api/tests/synthetic_classification_corpus.json` specifies expected software behavior for supplied fixture labels, including Urdu and Pashto scripts, Roman variants, code-switching, typos, unsupported language, ambiguity and text that combines health content with unrelated requests. Script alone cannot identify Urdu versus Pashto or distinguish English from romanized Urdu or Pashto. No heuristic detector is wired into the service. Tests exercise validation, synthetic inference, timeout/cancellation and a mocked HTTPS transport. They establish contract behavior, not inference quality, clinical accuracy or live networking. The full hosted workflow also exercises the public route inventory and offline emergency behavior.

AI-06 and QA-01 need representative privacy-approved samples and bilingual review, measured separately by language, script, transliteration and code-switching. Report language set precision/recall, relevance precision/recall, false off-topic rejection of health messages, abstention rates, ambiguity, mixed-language and adversarial failures. Compare observed outcomes with the PDF's evaluation goals using approved sampling and acceptance criteria. Production remains blocked on approved inference, consent/privacy and egress controls, representative language testing, validated independent safety routing and clinical governance. Reverting the feature commit removes the private contract without data or schema rollback.
