# Updated AI control review

Reviewed: 2026-11-16. Scope: current local source after AI hardening. No source-code changes or live provider calls in this review. Last recorded verification: 97 tests passed, JavaScript syntax and Python compilation passed. Automated tests do not establish real-model quality or effective runtime sandbox isolation.

## Existing controls

| Control | Status | What exists and what remains |
|---|---|---|
| Execution policy | Implemented, bounded scope | Central fixed limits, two concurrent runs, two application attempts, deadline and size limits. Limits apply per generation operation, not one complete multi-batch job search. |
| Model routing | Implemented | Main/fast tiers across backends; Codex fast setting fixed. Effective model versions behind aliases/defaults are not pinned. |
| Prompt injection defenses | Partial | Untrusted-input instruction, API system messages, task delimiters, restricted CLI capabilities, validation and human review. No measured adversarial robustness. |
| Least privilege / sandboxing | CLI restrictions implemented; runtime unverified | Fresh directories, required Claude isolation flags and explicit Codex restrictions. No container/VM boundary or verified filesystem/network isolation test. |
| Structured outputs | Implemented with gaps | Native OpenAI schemas for four content tasks; local normalized schemas. Scoring uses permissive parsing. Not all raw malformed nested fields are rejected before normalization. |
| Grounding / provenance | Partial | Source project identities/dates, literal skill filtering, source hash and numeric warnings. No claim-to-source evidence mapping or semantic verification. |
| Human review | Implemented | CV improvement preview, accept/reject, backups and stale-source checks. Application submission stays manual. |
| Cache invalidation | Improved | Model/backend/prompt/profile/work authorization/job-aware reuse. Provider aliases may change without changing cache identity. |
| Cost controls | Partial | Fast tier, batching, scoring cap, caching, attempts and API output limits. No daily/monthly currency budget or complete CLI token accounting. |
| Observability | Partial | Rotating private run metadata, timing, attempts, parse status and numeric API usage. No complete end-to-end workflow trace or task-validation outcome correlation. |
| Tests / evals | Partial | 97 recorded passing regression tests; tiny opt-in English/Arabic live eval command. Live evals and visual browser review not performed. |
| Privacy / secrets | Basic | Local data, ignored personal files, sanitized generation errors and private trace fields. API keys can still live in plaintext config; no data retention/delete interface. |
| Reliability / persistence | Basic | Atomic JSON writes, fetch guard, backups and generation concurrency limit. No per-job single-flight generation, durable queue, cancellation, or transactional stale-save protection. |
| Network exposure | Local default | Loopback default and cross-origin write check. LAN mode remains unauthenticated. |

## Most useful next work, in order

1. Real backend and browser acceptance checks. Use synthetic inputs to verify each backend actually starts, native schemas work, the review flow works visually, and isolation denies test file/network access. Existing subprocess tests verify arguments rather than enforcement.
2. Stronger validation before normalization and before saving/rendering. Enforce nested types, array lengths, skill-pair length, finite numeric bounds and exact scoring ID coverage; distinguish missing, refused, truncated and invalid responses.
3. Claim-level provenance. Give source CV facts stable IDs; require generated claims to cite those IDs and show the evidence in the review UI. Keep numerical/identity checks deterministic and unsupported claims visible. A second model is not proof.
4. Representative quality and injection evals. Expand beyond two cases to CV import accuracy, unsupported skills/metrics, multilingual names, missing descriptions, eligibility uncertainty, scoring usefulness and multiple attack styles. Establish a baseline before changing prompts/models.
5. Per-job idempotency and transactional persistence. Reuse an in-flight request for the same job/profile; lock stale-check plus save; prevent concurrent package writes and unnecessary duplicate billing.
6. Whole-workflow budgets and cancellation. Bound a complete fetch/scoring workflow, expose cancellation, reserve daily usage and show estimates where pricing is known. Distinguish estimates, provider-reported usage and hard enforced limits.
7. Privacy and deployment controls. Prefer OS key storage/environment secrets, add retain/delete controls and redact unnecessary personal fields from scoring inputs. Require authentication before LAN exposure. Raise this priority to first before sharing/hosting.
8. Operator controls and complete traces. Add a settings view for limits, active backend and policy health; correlate fetch/batch/generation/validation/export under one workflow ID. Record effective model IDs where supplied.

## Scope decisions

Full OS/container isolation is useful if continuing to run coding-agent CLIs, but should follow runtime verification and a clear compatibility design. It is not needed to make a plain text API request.

RAG/vector databases, multi-agent orchestration and fine-tuning remain lower priority for this small CV-plus-posting workflow. Revisit retrieval when users supply many documents and relevant evidence no longer fits comfortably in context.

## References

- Evaluation practice: https://developers.openai.com/api/docs/guides/evaluation-best-practices
- Structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Prompt injection defenses: https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks
