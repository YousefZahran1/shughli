# شغلي (Job Hunt Cockpit)

Built by **jo builds**.

شغلي يعني "شغلي أنا". هذي أداة تساعدك تدور وظيفة وتجهز ملفاتك بدون وجع راس.

I built this because job hunting is mostly repeat work: find jobs, check if they fit, rewrite the CV, write the cover letter, repeat. This app does the boring parts on your own machine. You still read every job and press submit yourself.

## What it does

- Reads your CV (PDF, DOCX or TXT) and turns it into a profile you can edit.
- Fetches jobs from Indeed, LinkedIn and Glassdoor (through JobSpy), plus Remotive and Arbeitnow.
- Filters them by your role keywords and seniority, then scores the best ones against your profile.
- Writes a tailored CV and cover letter for any job you pick, and exports both as `.docx`.
- Reviews your CV. If it is weak, it asks you questions and rewrites it using only the facts you give it.

Everything runs locally. The only thing that leaves your machine is the text sent to the AI backend you choose.

## Run it

You need Python 3.10 or newer.

```bash
git clone https://github.com/YousefZahran1/shughli.git
cd shughli
python -m pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

1. Profile tab: upload your CV, check what it extracted, add your target country, city, role keywords and work authorization. Save.
2. Settings tab: pick an AI backend and the job sources you want.
3. Jobs tab: fetch, browse the scored list, open a job and click "Tailor CV + cover letter". Download the files.

## AI backends

You pick one in Settings. The default is `auto`, which uses the first one it finds.

| Backend | Setup | Cost |
|---|---|---|
| Claude Code CLI | log in from the app | your existing subscription |
| Codex CLI | log in from the app | your existing subscription |
| Anthropic API | paste a key | pay per use |
| OpenAI API | paste a key | pay per use |
| Custom | base URL, model, optional key | depends on the provider |

Custom works with any OpenAI-compatible endpoint, like OpenRouter, Groq, or a local server such as Ollama. Keys you paste are saved in `config.json` on your machine and are only sent to that provider.

The Claude CLI path is the one I tested most. The Codex login is best effort. If your Codex version behaves differently, look at the `codex` entry in `core/engine.py`.

## Rules I built in

- No auto-apply. Bots get filtered and accounts get banned, so you submit each application.
- No made-up content. Tailoring reorders and rewords your real CV. It should not invent skills, numbers, employers or dates.
- Work authorization is per region. Cover letters only claim what your profile says for that job's location. Always check exact rules (like hour limits) with an official source.

## Layout

```
app.py          Flask server
core/
  engine.py     AI backends
  profile.py    profile schema and CV text rendering
  cv_import.py  CV file to structured profile
  cv_coach.py   CV review and rewrite
  sources.py    job sources
  filters.py    keyword and seniority filters
  scoring.py    job scoring
  tailor.py     CV and cover letter, .docx export
  config.py     paths and settings
static/         the dashboard
tests/          pytest suite
samples/        fake CVs for trying it out
```

`PROMPTS.md` lists every prompt the app sends to the AI.

Your profile, cached jobs and generated files live in `data/`, and your settings live in `config.json`. Both are gitignored. Copy `config.example.json` to `config.json` if you want to change defaults by hand.

## Limits

- Remotive and Arbeitnow return thin results. JobSpy does most of the work.
- LinkedIn may rate-limit you without a proxy. Indeed is the most reliable board.
- Fetching is slow because it also pulls job descriptions.
- Scoring and tailoring use your subscription or API credit, so tailor only the jobs you plan to apply for.
- To keep cost down, scoring and CV import use a cheaper model tier, and only the top `max_ai_scored` jobs (40 by default) go to the AI. The rest show as unscored.
- A tailored package is saved, so opening it again costs nothing until you hit Regenerate.
- I have run it on Windows. It should work on macOS and Linux too.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## License

MIT. See [LICENSE](LICENSE).

## AI execution and review controls

All generation goes through `core/engine.py` and the central `core/policy.py` policy:

- Two concurrent AI runs; excess requests fail with a retry message.
- Two application-level attempts shared by API retries and JSON repair.
- A 300-second overall generation deadline, checked before attempts and after responses. Each request/subprocess receives the remaining timeout. HTTP timeouts are transport timeouts, not a guarantee against every stalled or trickling server.
- 80,000 input characters, 60,000 returned characters, and 4,096 requested output tokens for API calls. CLI output is checked after the process finishes. Provider/CLI internal retries and billing are outside this application's attempt accounting.
- Claude requires safe mode, disabled tools/MCP/customizations and no session persistence. Unsupported flags fail without a weaker fallback.
- Codex runs in a fresh directory with read-only sandbox, no approvals, no user config/rules, no session persistence, shell/unified execution, code mode, hooks and multi-agent features disabled, and web search disabled. This is CLI-level restriction, not an OS/container guarantee. Managed policies can still apply; use an API backend for a plain text request with no local agent process.
- CV and job input are explicitly untrusted. System instructions forbid executing embedded instructions. These defenses reduce prompt injection risk; they do not prove immunity.

OpenAI requests use native strict JSON schemas for CV import, review, improvement and tailoring. All providers receive local normalized response validation. Scoring filters unknown/duplicate IDs, handles non-finite scores, and caps explicitly empty-description postings at 70. API/provider errors fail clearly; unsupported native schema models do not silently downgrade.

CV coach improvements now open a before/after preview. **Accept and save** writes the profile and backs up the old version; **Reject changes** leaves the saved profile alone. Acceptance checks the original profile hash so a stale preview cannot overwrite later changes.

Tailoring only retains skills explicitly present in the candidate source and projects matching source identities. Source checks flag numbers absent from candidate evidence, include a source hash, and require human review. These conservative checks can flag legitimate rephrasing and cannot establish the truth of every sentence.

Scoring cache keys include authorization, prompt text/version, model/backend identity and job-content comparisons. Tailored packages and CV reviews also invalidate when relevant prompt/model/input changes. The first use after this update can therefore regenerate old results.

`data/ai_runs.jsonl` stores run IDs, UTC timestamps, backend/task identity, durations, attempt counts, parse outcomes and numeric usage where available. It omits CV text, generated text, API keys and provider error bodies. It rotates after approximately 2 MB and retains one previous file. Usage limits are **not a currency spending cap**; model/provider pricing and subscription billing differ.

Run regression checks without using real profile data:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q --basetemp=work/pytest
node --check static/app.js
```

Optional live AI quality checks use synthetic English and Arabic CVs with malicious job instructions:

```powershell
python -m core.evals --live
```

This command uses the selected provider and may consume subscription/API credit. Inspect `data/eval_report.json` for generated claims as well as automated checks. A passing report is a small regression signal, not a broad quality or safety certification.
