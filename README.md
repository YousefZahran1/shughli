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

Generation uses the central policy in `core/policy.py`: two concurrent slots, a 300-second generation deadline and two application-level attempts shared by transient API retries, JSON repair and custom token-parameter compatibility retries. Interactive tasks wait up to 20 seconds for capacity; background scoring waits against its deadline.

A whole job search has one workflow ID, a 600-second budget and at most 12 scoring batches. Reaching either limit stops scoring but discards nothing: every fetched job and every score already returned is saved, the remaining jobs are saved as "not scored", and the search log says which limit was hit and how many jobs it left. Use **Cancel search** to stop waiting and discard the results; cancelling is the only thing that discards them. A provider/library request already in progress may continue until it returns or times out, and may still consume credit; it retains its generation slot until it returns.

The named tasks `score`, `import`, `review`, `improve`, `tailor` route through one table to fast/main tiers. Settings shows the effective tier models, including fallback when a fast model is unset. Requested API token budgets are 2,048 / 8,192 / 3,072 / 12,288 / 8,192 respectively; OpenAI reasoning models receive additional completion headroom. Explicit provider truncation raises a cut-off error instead of spending the JSON retry.

Claude requires disabled tools/MCP/customizations and safe mode. Codex requests read-only, no-shell/no-web/no-hooks execution. Both generate from fresh directories. These are CLI restrictions, not an OS/container isolation guarantee. Unsupported options fail without a weaker fallback. CLI errors distinguish authentication, limits, unsupported flags, timeouts and other failures. Truncated raw diagnostic text is stored only in gitignored `data/private_debug/`; treat those files as sensitive.

Shared instructions classify CVs, answers and postings as untrusted data. APIs receive the instruction once in the system role; Claude receives it once as its system prompt and Codex once in stdin. Strict native OpenAI schemas and normalized local validation check response structure. These controls do not prove factuality or injection immunity.

CV coach changes require a before/after review and explicit acceptance. Tailoring normalizes skill names and selected aliases, preserves supported project identities/dates, and shows removed skills/projects separately. Numeric warnings and review notes are outside the cover letter so copying it includes only the letter.

Caches incorporate relevant input, prompt and model/backend identity. `data/ai_runs.jsonl` rotates private run metadata including workflow IDs, timings, attempts and provider-reported model/usage data when supplied. It excludes prompts, CV text, keys and raw error bodies. Missing actual-model metadata is labeled rather than guessed. Application usage limits are not a currency spending cap.

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q --basetemp=work/pytest
node --check static/app.js
```

Optional synthetic English/Arabic quality checks use the selected backend and subscription/API credit:

```powershell
python -m core.evals --live
```

Review `data/eval_report.json`. See `AI_CONTROL_REVIEW.md` and `CHANGELOG_AI_HARDENING.md` for verification and remaining limitations.
