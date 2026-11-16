# AI control review — after policy fixes

Reviewed: 2026-11-16. Branch: ai-policy-fixes. All changes and commits are local; nothing was pushed and no GitHub tools were used for this task.

## Current controls

| Control | Current behavior | Remaining limits |
|---|---|---|
| Execution policy | Central per-generation deadline (300 seconds), input/output character limits, two application attempts, two concurrent generation slots. | Provider/CLI internal retries are outside application accounting; API transport timeout semantics still apply. |
| Concurrency | Interactive tasks wait up to 20 seconds; background scoring waits until its deadline. Busy capacity alone does not immediately discard a scoring batch. | A task whose own deadline expires still fails. Limits are per app process. |
| Workflow budget | One ID spans the whole search and all its trace rows. Twelve scoring batches and 600 seconds per search. | These are fixed policy defaults, not currency budgets. |
| Cancellation | UI Cancel search endpoint targets the current workflow ID. Gate waiting and workflow waiting stop; late collection/scoring results cannot overwrite cache. | A library/provider request already in flight may finish later and consume credit. Its AI slot stays held until it returns. |
| CLI error reporting | Parse JSON/JSONL before exit status; classify auth expired, usage/rate limit, unsupported flag, timeout, other. Unknown isolation options are blamed only when stderr identifies one. | Authentication is not refreshed automatically. |
| Diagnostics | User messages and normal traces omit raw CLI error text. Truncated diagnostics go to gitignored data/private_debug/*.json (at most 8,000 characters per stream). | Local debug files may contain sensitive content; they inherit the user's local filesystem protections. No debug-file retention UI. |
| Output budgets | score: 2,048; import: 8,192; review: 3,072; improve: 12,288; tailor: 8,192 requested API completion tokens. OpenAI reasoning families get max(16,384, 3x base) completion headroom. | Reasoning can still exhaust its budget; CLI provider output limits are not controlled by API parameters. Character limit checked after CLI completion. |
| Truncation | Anthropic max_tokens and OpenAI/custom length stops produce a clear cut-off error with no JSON-repair retry. | Increasing a budget does not guarantee a response will fit. |
| Custom compatibility | HTTP 400 rejecting max_completion_tokens can retry with max_tokens. This shares the single allowed retry with transport and JSON repair. | Providers with other incompatible response shapes/options may still fail explicitly. |
| Task routing | One table maps score/import to fast and review/improve/tailor to main. All application callers use task names. Status/settings API and Settings UI show effective tiers including fallback. | Legacy tier inputs remain supported at the engine boundary for existing integrations. CLI-default model version cannot be known before the CLI reports it. |
| Model tracing | API response model, Claude modelUsage keys and Codex model/usage metadata are recorded when returned. Missing provider model metadata is labeled not_reported. | Codex models are read from JSONL model fields or an explicit startup model line when supplied; absent IDs are not guessed. |
| System instructions | Shared instruction is sent once: API system role, Claude system prompt, or Codex stdin prefix. | Instructions alone do not establish injection immunity. |
| CLI restrictions | Fresh working directories, required Claude isolation flags, Codex read-only/no-shell/no-web/no-hooks restrictions. No weaker compatibility fallback. | Successful live generation verifies invocation compatibility, not full OS/filesystem/network isolation. No container/VM. |
| Validation | Native OpenAI content schemas, normalized local schemas and scoring bounds/ID checks. | Some malformed nested fields can still be normalized instead of rejected; not semantic factuality verification. |
| Grounding | Unicode/case/punctuation normalization, selected aliases (Postgres/PostgreSQL, JS/JavaScript, etc.), word boundaries, source project identities/dates, numeric warnings. | Aliases are curated; no sentence-level source-evidence proof. |
| Removal transparency | Unsupported skills and projects appear in package.dropped_items, saved packages and the review screen. | Removal warnings are conservative; users should inspect omissions. |
| Human review | Before/after CV proposals, explicit accept/reject, backup and stale-profile check. Applications remain manually submitted. | Stale check plus save is not a database transaction across simultaneous writers. |
| Copy safety | Cover letter text contains only the letter. Review notes/warnings and removal disclosures use separate elements. | No visual browser review performed. |
| Caching | Relevant prompt/profile/job/backend/model identity drives reuse; grounding change invalidates old tailored packages. | Aliased model names can change without changing the requested model string. |
| Cost/privacy | Batching, scoring caps, fast tier, caching, no prompt/CV/key content in normal run traces, local ignored personal data. | No monetary spending cap, encrypted key store or retention/delete UI; LAN mode still needs authentication before sharing. |

## Verification

- Original preserved baseline: 97 tests. Final full suite: **142 passed in 31.30 seconds**.
- Each numbered fix had new tests run and observed failing before implementation. Two additional failing workflow tests and three failing CLI limit/model-metadata tests drove follow-up fixes. The workflow tests drove prompt cancellation/deadline handling during blocking work.
- Existing safety assertions were retained. Task-name assertions and removed-parameter call sites were migrated to the new API without loosening tier/isolation checks.
- JavaScript syntax, Python compilation and git diff whitespace checks passed.
- Renderer tests execute the real tailor renderer with a minimal DOM and verify copied letter text excludes notes and removals.
- Claude: python -m core.evals --live — **2/2 passed**, synthetic English and Arabic cases. Report: data/eval_report.json.
- Codex: same evaluation main with an in-memory backend override and separate data/live-codex output directory — **2/2 passed**. Real config/profile was not changed. Report: data/live-codex/eval_report.json.
- Anthropic API/OpenAI API/custom API: not configured; payload, truncation and compatibility behaviors tested using mocks, not live endpoints.
- Claude actual modelUsage keys were captured in traces. Codex metadata parsing was strengthened after the successful live run and verified with a JSONL fixture; an actual Codex model version was not established from that live run. The small live eval checks configured forbidden claims; it is not comprehensive evidence of factuality or injection resistance.
- Some Python test exits emit an existing pyreadline3 Console destructor OSError after successful tests; final pytest exit status was zero.

## Most useful remaining work, in priority order

1. Visual browser acceptance checks and adversarial runtime isolation tests with synthetic files/network targets.
2. Strict raw nested validation before normalization, and broader real-model evals for CV import, eligibility uncertainty, ranking quality and injection variants.
3. Claim-level evidence IDs and review UI links to the supporting candidate facts.
4. Per-job single-flight generation and transactional stale-check/save/package persistence.
5. Monetary usage accounting, configurable policy controls, key storage and retention controls. Authentication becomes first priority before LAN/hosted sharing.

RAG/vector databases, fine-tuning and multi-agent orchestration remain lower priority for this small CV-plus-posting workflow.
