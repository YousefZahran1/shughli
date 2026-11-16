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
