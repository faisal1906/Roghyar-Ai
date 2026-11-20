# AI-04 content-limited reply writer

PDF sections 10 and Appendix A.3 constrain replies to authorized content. The private writer selects exact plain text with no substitutions, transformations or generated prose. AI-01 supplies routing, AI-02 supplies authoritative pending questions, and AI-03 supplies an independent BE-04 result. AI-03 summaries, proposed levels, specialties and condition codes grant no publication authority.

## Selection and authority

Reviewed emergency and urgent take precedence over ordinary routing. The writer binds the highest-priority matching rule and locale through BE-04's resolver, which re-evaluates evidence and checks fingerprint, rule code/version, warning reference and complete warning bundle digest. Missing content preserves the intent and returns no text. Urgent with unresolved evidence routes to safety review. Rules unavailable and evaluator failures route to clinical unavailable. Other AI-03 outcomes, including no match and lower proposals, route to safety review. Unsafe requests are restricted without authored crisis instructions.

Ordinary intents are greeting, off-topic, navigation unverified, clarification and classification unavailable. A pending follow-up may take precedence over greeting, off-topic or clarification with a current, owner-bound state and server-authorized question. The artifact binds the question key and exact available option IDs. AI-02 retains the five-question cap and owns all transitions.

Catalog schema v1 signs content ID and revision, intent, reviewer and key ID, review time and status, activation, permitted BE-04 states, optional question key, and the full ordered translation set. Each locale has independent review provenance and exact text. Ed25519 signs domain-prefixed canonical JSON with a distinct reply-content signing domain. SHA-256 covers those same bytes. The configured authority must match reviewer, scope and valid period, and remain unrevoked. Product-copy keys cannot sign clinical intents. Warning copy uses BE-04's separate rule-bound channel. Catalog selection requires exactly one active artifact for the context. Missing, duplicate or unapproved artifacts yield no text.

An explicit EN/UR/PS locale is accepted, or a single classified supported language. Mixed or uncertain classifications require an explicit selection. No English substitution or script guessing, Roman Urdu/Pashto transliteration, or machine translation is implemented. Output retains exact Unicode and contains content identity, revision and digest only when rendered. There are no patient-text placeholders, HTML or provider calls.

## Release gates

The default catalog, reviewer trust store and question list are empty. Production requires verified publisher and key custody, immutable version history and revocation operations, independent locale and clinical review, approved GD-01/GD-02/GD-04 integration, AI-06 and QA-01 validation, and privacy-approved identity and content delivery. Synthetic fixtures use ephemeral keys and establish software behavior. No route, storage, RLS grant, booking, speech integration, or model selector is added.
