# Official-source notes for the proposed real-model evaluation

Checked 2026-10-30. These are public-doc facts only; no API/model calls were made.

## Candidate text model IDs and listed prices

- OpenAI model catalog: `gpt-6-luna` is described as cost-sensitive/high-volume; model ID `gpt-6-luna`, context window 1.05M. https://developers.openai.com/api/docs/models
- OpenAI standard pricing: `gpt-6-luna` input $0.10 / 1M tokens and output $0.50 / 1M tokens (short-context). https://developers.openai.com/api/docs/pricing
- Anthropic current lineup: `claude-haiku-5-5` (API ID `claude-haiku-5-5`), described as high-volume/latency-sensitive; listed from $0.10 input and $0.50 output per MTok. https://platform.claude.com/docs/en/models/overview
- Anthropic detailed pricing: Claude Haiku 5.5 prompts up to 100,000 tokens cost $0.10 input / $0.50 output per MTok; over 100,000 tokens a higher tier applies. https://platform.claude.com/docs/en/about-claude/pricing
- Gemini model catalog recommends stable current `gemini-3.5-flash-lite` or `gemini-3.8-flash` for new projects; 2.5 family has usage restrictions for users without recent activity. https://ai.google.dev/gemini-api/docs/models
- Gemini 3.5 Flash-Lite paid-tier standard: $0.30 input / $2.50 output per 1M tokens; the page currently lists its model ID as `gemini-3.5-flash-lite`. https://ai.google.dev/gemini-api/docs/pricing

## Usage measurement fields

- OpenAI Responses create reference reports usage with `input_tokens`, `output_tokens`, `total_tokens`, `input_tokens_details` (including cached tokens) and `output_tokens_details` (including reasoning tokens). https://developers.openai.com/api/reference/cli/resources/responses/methods/create/
- Anthropic Messages response example exposes `usage.input_tokens` and `usage.output_tokens`; production schema should additionally capture cache usage fields when present. https://platform.claude.com/docs/en/build-with-claude/working-with-messages
- Gemini GenerateContent returns `usageMetadata` including `promptTokenCount`, `cachedContentTokenCount`, `candidatesTokenCount`, `toolUsePromptTokenCount`, and related counts; `generationConfig.maxOutputTokens` sets a ceiling. https://ai.google.dev/api/generate-content

## Data controls and retention

- OpenAI API content is not used for training by default; abuse-monitoring logs may include prompts/responses and are generally retained up to 30 days. ZDR/Modified Abuse Monitoring require eligibility/approval and have endpoint limitations. https://developers.openai.com/api/docs/guides/your-data
- Anthropic API retained data is not used for training without express permission; standard/feature-specific retention varies; ZDR for eligible Messages/Token Counting API features requires organization enablement, and some models/features are excluded. https://platform.claude.com/docs/en/manage-claude/api-and-data-retention
- Gemini API paid-tier documentation says paid-tier API logs are not used for product improvement by default; logging settings depend on project/API path. Separate abuse monitoring retains prompts, context and outputs up to 55 days and flagged material may be reviewed. https://ai.google.dev/gemini-api/docs/logs-policy and https://ai.google.dev/gemini-api/docs/usage-policies

## Cost arithmetic for the draft

Proposed max workload: 30 tasks × 7 text candidates × 3 paired repeats = 630 model-direct requests/provider, plus 6 malformed/unknown cases × 3 repeats = 18/provider, plus 30 gateway controls × 3 repeats = 90/provider; total 738 calls/provider. Per-call caps: 4,096 input and 512 output tokens. At the listed current standard rates above, cap arithmetic is about $0.4912 OpenAI, $0.4912 Anthropic, and $1.8515 Gemini (3.5 Flash-Lite), total $2.8339. Add 30% for pricing overhead/usage details = $3.6841, still under proposed hard ceiling $5.00 total. Use current prices only as an estimate; refresh before any separately approved live run. Do not use free tiers or opt-in data-sharing tiers. Never exceed API-side output caps/call counts; provider charges and taxes may differ.
