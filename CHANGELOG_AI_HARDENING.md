# AI hardening change log

Date: 2026-11-16
Status: Implemented locally; automated checks pass. No GitHub push or live provider generation was performed.

## Changes implemented

### Central execution policy — core/policy.py, core/engine.py
- Central immutable policy: 300-second overall deadline checked before attempts and after generation, 80,000 input characters, 60,000 output characters, 4,096 requested API output tokens and two application-level attempts.
- Limit concurrent generation runs to two, including JSON repair; reject extra work with a retry message.
- Share attempt/deadline accounting across transient API retries and JSON repair. Remove Claude compatibility retries that weakened isolation.
- Treat unsupported backends/task tiers and oversized input/output as explicit errors.
- Bound CLI model identifiers to safe characters before Windows shell invocation.
- Use a fresh temporary directory for each CLI generation call.
- Claude always requires safe mode, disabled tools/MCP/customizations and no session persistence.
- Codex explicitly requests read-only sandbox, no approvals, ignored user config/rules, ephemeral sessions, disabled web search, shell/unified execution, code mode, hooks and multi-agent features.
- Fix Codex fast-model routing to honor codex_model_fast.
- Do not expose raw provider responses or generated text in generation error messages.

### Untrusted input and validation — core/validation.py, AI task modules
- Shared text-only system instruction treats job descriptions, CVs and answers as untrusted data rather than executable instructions.
- Native strict OpenAI schemas for CV import, review, improvement and tailoring.
- Local normalized schemas for every content-producing task; preserve existing safe normalization compatibility with CLI/custom response shapes.
- Validate CV review scores/lists and bound candidate answer count/length.
- Scoring rejects unknown and duplicate IDs, handles non-finite scores and caps explicitly empty-description jobs at 70.
- Skills absent from candidate text are removed even when also absent from the job posting.
- No source projects means no generated projects; tighten source project-name matching.
- Preserve Unicode/Arabic names when matching project/employer identities. The old ASCII-only normalizer could collapse different Arabic names to the same empty key.
- Source checks flag unsupported numeric tokens, record a source hash and explicitly require human review. Tailored output shows these warnings in the UI.

### Human review — app.py, static/app.js, static/index.html
- CV improvement returns a proposal without saving the profile.
- Display original/proposed fields side by side with Accept and save / Reject changes.
- Acceptance backs up the previous profile and saves the reviewed proposal.
- A source-profile hash rejects stale acceptance if the profile changed during review.
- Reject malformed non-object profile/preview request bodies.
- Bound uploads/request bodies to 10 MB.

### Cache correctness — scoring.py, tailor.py, cv_coach.py
- Include policy version, backend/model identity, relevant prompt versions/text and candidate inputs in cache identity.
- Scoring also compares job descriptions and relevant posting metadata; work authorization affects the score key.
- Tailored package identity includes full job content, profile, prompt and model/backend.
- Existing caches with old keys regenerate when next requested; no migration or deletion of personal files.

### Observability and evaluations
- Rotating data/ai_runs.jsonl traces contain run ID, UTC timestamp, backend/task/model fingerprint, duration, application attempt count, JSON parse outcome and numeric provider usage where returned.
- Trace files omit prompts, CVs, generated prose, keys and provider error bodies. Storage failures do not discard successful generation.
- core/evals.py provides an explicit --live synthetic English/Arabic CV and prompt-injection quality check. Results and generated synthetic output go to data/eval_report.json for manual inspection.
- Add regression tests covering budgets, CLI failure/isolation arguments, safe model names, malformed reviews, fabricated skills/projects, Arabic identity matching, cache identity, preview/accept/stale-save behavior, provider schema payloads, source checks and privacy-safe traces.
- Add jsonschema dependency; document controls, usage and limitations in README.md and PROMPTS.md. Ignore work/ test and implementation scratch files.

## Verification
- Baseline before edits: 77 tests passed.
- Final: python -m pytest -q --basetemp=work/pytest-complete — 97 passed in 9.12 seconds.
- node --check static/app.js — passed.
- python -m compileall -q core app.py — passed.
- git diff --check — passed (Git reported normal LF/CRLF conversion notices).
- python -m core.evals --help — passed; live generation deliberately not run.
- Installed Claude/Codex command help inspected to confirm top-level isolation flags. CLI generation commands are tested with mocked subprocesses; no authenticated generation was launched.
- All test profile/cache/package operations use temporary data directories. Existing personal CV/profile/config files were not edited by this work.

## Remaining limits and follow-up usage
- Restart the local Flask app and refresh the browser to load these changes. Install updated requirements if using a different Python environment.
- No live provider quality/cost check or visual browser review has been performed. The frontend passed syntax checks and preview/save endpoints passed regression tests.
- A source hash and numeric warnings are conservative checks, not sentence-level proof or a semantic fact verifier. Review employers, metrics, qualifications and eligibility claims before submitting.
- CLI restrictions are not an OS/container isolation guarantee; managed policies and CLI implementation/version behavior may differ. Unsupported flags fail instead of falling back. API backends avoid running a local coding agent.
- API token limits, job caps and application attempt budgets are not exact currency caps. Provider/CLI internal retries are not counted as application attempts. CLI output limits are checked after the process returns.
- Requests timeout semantics are transport-level; the overall deadline is checked at control boundaries and is not a guarantee against every trickling server. Concurrency limits apply within one app process.
- Source checks may flag legitimate rephrasing, and strict literal skill matching may omit equivalent aliases. These tradeoffs favor conservative candidate claims.
- Trace outcome json_parsed means parsing succeeded; task validation follows in the caller. It must not be interpreted as a full quality or factuality verdict.
- The opt-in eval dataset is intentionally small and not a comprehensive safety or ranking benchmark. Run python -m core.evals --live to obtain real-provider output, then review data/eval_report.json.
- RAG/vector databases, multi-agent orchestration and fine-tuning were not added: this app already supplies the small CV/posting context directly, and these additions would not resolve the reliability problems identified.

## Follow-up control review — 2026-11-16
- Re-read current policy, validation, engine, task controls and existing verification record.
- Created AI_CONTROL_REVIEW.md with current implemented/partial controls and an ordered next-work assessment.
- Clarified per-generation versus whole-workflow limits, configured versus runtime-verified isolation, and normalized validation versus strict raw validation.
- This follow-up was a read-only code review plus documentation; no source changes, live calls, or new test runs.

## Requested policy corrections — completed locally on ai-policy-fixes

This section supersedes earlier limitations addressed below. Existing uncommitted work was preserved first as ade9018. All commits use the repository identity Youssef Ibrahim <youssefzahran.y@gmail.com>, without co-author trailers. Nothing was pushed and no GitHub tools were used.

### Test-first evidence and commits

| Fix | Observed red test result | Green result and local commit |
|---|---|---|
| Preserve existing changes | No modifications discarded | ade9018 — Preserve existing AI hardening work |
| 1. CLI errors | 16 new failures | 36 relevant tests passed; ef4e88d — Classify CLI errors and retain private diagnostics |
| 2. Gate waiting | 3 new failures | 3 new tests passed; 0093eaf — Wait for AI capacity within task deadlines |
| 3. Output budgets/truncation | 5 new failures | 50 relevant tests passed; 600efd1 — Budget outputs per task and detect truncated replies |
| 4. Named routing/effective models | 4 new failures | 49 relevant tests passed; 20f00ba — Route named tasks and expose effective model selection |
| 5. Workflow ID/budget/cancel | 4 new failures | 52 relevant tests passed; 42c4315 — Bound search workflows and add cancellation |
| 6. System/unused plumbing | 3 new failures | 64 relevant tests passed; 751ed3b — Send system instructions once and remove unused plumbing |
| 7. Cover-letter copy | 1 new failure | 1 renderer test passed; 48c2f08 — Keep review notes outside copied cover letter |
| 8. Grounding/removal disclosure | 4 new failures (prior renderer test still passed) | 50 relevant tests passed; 3fca430 — Normalize grounding aliases and disclose removed items |
| Workflow blocking-work follow-up | 2 additional failures | 6 workflow tests passed; 0c939c8 — Stop cancelled or expired searches during blocking work |
| CLI limit/model metadata follow-up | 3 additional failures | 23 relevant tests passed; 702ac7d — Handle CLI limit wording and capture Codex model metadata |

### Implemented behavior

- CLI JSON/JSONL envelopes are inspected before exit status. Errors are categorized and sanitized; auth failures restore the login instruction on any exit code. Only explicit unknown/unrecognized option errors in stderr blame required isolation flags.
- Truncated raw diagnostics are separated into local ignored data/private_debug/*.json, using restrictive creation modes where supported by the OS. They never enter user-visible error messages or normal traces.
- Interactive gate wait is 20 seconds maximum; scoring waits against its own deadline rather than failing immediately because two other runs are busy.
- API output budgets are task-specific (score 2,048; import 8,192; review 3,072; improve 12,288; tailor 8,192). OpenAI reasoning models have additional completion headroom. Explicit truncation raises a cut-off error and does not invoke JSON repair.
- Custom max_completion_tokens-to-max_tokens HTTP 400 compatibility retry consumes the same retry budget as API/JSON retries.
- All application callers use score/import/review/improve/tailor. A single routing table defines the tiers. Settings/API display fallback models. Provider-reported model metadata is recorded (Claude modelUsage keys specifically); missing metadata is labeled rather than invented.
- Searches share a workflow ID, 12-batch/600-second limits, and a UI cancellation endpoint. Waiting stops promptly during collection/scoring; late results cannot persist. Existing provider requests may continue until completion/timeout, retaining their AI slot.
- The shared system instruction is sent once. Removed lean and redundant generation cwd arguments; cleaned unused imports.
- Review notes and removal disclosures are outside #g-cover, keeping copied text clean.
- Grounding uses normalized word-boundary matching and selected common aliases. Removed skills/projects are returned, saved and shown to users.

### Final verification

- python -m pytest -q --basetemp=work/pytest-policy-final: **142 passed in 31.30 seconds**.
- node --check static/app.js: passed.
- python -m compileall -q core app.py: passed.
- git diff --check: passed.
- Claude live eval command: **2/2 passed**; local report data/eval_report.json.
- Codex live eval using the same evaluator with in-memory backend override: **2/2 passed**; local report data/live-codex/eval_report.json. No real config/profile changes.
- API backends were unconfigured and therefore not live-verified. Their request/truncation/retry behavior was mocked in tests.
- Codex JSONL model/usage and explicit startup model-line parsing were strengthened after the live run and tested using a fixture. The actual model version for that live Codex run remains unverified.
- Successful live CLI calls verify invocation compatibility, not full isolation. No visual browser review, OS/container isolation test or comprehensive AI quality certification.
- Existing test assertions were not loosened; task names and removed-parameter call sites were migrated while retaining their tier/isolation assertions.
- Existing Windows pyreadline3 cleanup warnings sometimes appear after pytest passes; final test exit status was zero.

See AI_CONTROL_REVIEW.md for the current control inventory and remaining priorities.

## Search and model-error follow-up ? 2026-11-17

Three fixes completed locally on ai-policy-fixes; nothing pushed:

1. **1809d2b** ? Preserve fetched jobs and finished scores when a search reaches its deadline or batch limit. Remaining jobs stay unscored; explicit user cancellation still discards the run, and late workers cannot overwrite saved results. This commit was already present when work resumed.
2. **46068d8** ? Align max_ai_scored with SEARCH_MAX_BATCHES x BATCH (12 x 18 = 216). Legacy values such as 500 are capped during scoring; saving an above-budget value stores the covered maximum. Status/config responses and Settings show the effective cap. Reviewed the inherited implementation and five new tests, then verified the full 152-test suite before committing; no new behavior was added during that review.
3. **4a0b776** ? Add model_unavailable classification for both CLIs. The user message names the effective configured model, including the fast-tier model for scoring, and directs the user to Settings. Raw provider messages remain only in ignored private diagnostics, not public errors or normal traces.

Verification:

- Before writing fix 3 tests, probed installed Claude Code 2.1.268 and Codex CLI 0.169.0 with the synthetic unknown model no-such-model-xyz using the application's isolated invocation. Claude reported that the selected model may not exist or be accessible; Codex reported that the model is unsupported with a ChatGPT account. Raw responses were retained in data/private_debug only.
- Fix 3's 14 new tests initially failed because the category was other. After implementation, all 33 CLI-error tests passed. Coverage includes both exit codes, main/fast routing, real provider wording, stderr variants, private diagnostic retention and normal-trace redaction. Existing tests were not weakened.
- Final full suite: python -m pytest -q --basetemp=work/pytest-three-fixes-final ? **166 passed in 42.41 seconds**.
- node --check static/app.js, python -m compileall -q core app.py, and git diff --check passed.
- The first default-temp pytest run failed at setup because the sandbox could not create its temporary directories; the workspace-temp runs passed. Initial restricted CLI probes could not complete; the successful real-wording probes used elevated execution. No personal profile/config files were edited.
- Existing pyreadline3 destructor warnings appeared after successful tests; pytest exited zero.
- No visual browser acceptance check, new successful-generation quality eval, live API check, or OS isolation audit was performed in this follow-up. The live checks verify unknown-model rejection wording only. Test-first evidence for the inherited fix 1/fix 2 changes was not re-established by reverting existing work.
