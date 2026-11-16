"""Score jobs against the user's profile using their AI backend. Batched to
respect subscription usage limits (all jobs in one call per batch).

Cost controls: jobs scored earlier against the same CV are reused, only the
best `max_ai_scored` new jobs (ranked locally by skill overlap) go to the AI,
and jobs that were not scored stay `score: None` — never a fake 0.
"""
from __future__ import annotations

import math
import hashlib
import json
import re

from . import engine, policy, profile as profile_mod
from .textutil import excerpt

BATCH = 18
PROMPT_VERSION = "v2"   # bump when the scoring prompt changes -> cached scores are re-done

_PROMPT = """You are screening job postings for one candidate. Score how worthwhile it is for THIS candidate to apply.

<cv>
{cv}
</cv>

Target seniority levels: {levels}
Work authorization: {wa}

Score each job 0-100:
- 85-100: strong match on skills and level, and eligible to work there. Apply first.
- 70-84: good match with one notable gap.
- 50-69: a stretch. Partial skill match, or one level above or below the target.
- 25-49: weak. Wrong level or mostly different skills.
- 0-24: not viable. A hard requirement the candidate cannot meet.

Weigh in this order:
1. Overlap between the skills the job requires and what the CV shows.
2. Seniority. A role asking for clearly more years than the CV shows scores below 50.
3. Eligibility. If the posting requires work authorization the candidate lacks, or says no sponsorship, score below 25. If work authorization is "not specified", do not penalize.
4. Language. A required language that the CV does not list scores below 40.

If a job has an empty "desc", judge from the title alone, cap the score at 70, and begin the reason with "title only".
Use the whole range. Jobs that differ should get different scores.

Return only a JSON array with one object per job, using the same ids:
[{{"id":"<id>","score":<int>,"reason":"<12 words or fewer: the deciding factor>"}}]

Jobs:
{jobs_json}"""


def _prompt(cv: str, work_auth: list, jobs: list[dict], seniority: list) -> str:
    rows = [{"id": j["id"], "title": j["title"], "company": j["company"],
             "location": j["location"], "level": j.get("level", ""),
             "desc": excerpt(j.get("description") or "", 900)} for j in jobs]
    wa = "; ".join(f"{w.get('region','')}: {w.get('status','')}" for w in work_auth) or "not specified"
    levels = ", ".join(seniority) or "any"
    return _PROMPT.format(cv=cv, levels=levels, wa=wa,
                          jobs_json=json.dumps(rows, ensure_ascii=False))


# ------------------------------------------------------------- parsing ----
def _coerce_score(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        m = re.search(r"-?\d+(?:\.\d+)?", str(v))
        if not m:
            return None
        n = float(m.group(0))
    if not math.isfinite(n):
        return None
    return max(0, min(100, int(round(n))))


def _items(data) -> list:
    """Accept a list, or a dict wrapping one list (or a single item)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("jobs", "results", "scores", "items", "data"):
            if isinstance(data.get(k), list):
                return data[k]
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
        if "id" in data:
            return [data]
    return []


# ----------------------------------------------------------- local rank ----
def _skill_terms(profile: dict) -> list[str]:
    terms: set[str] = set()
    for s in profile.get("skills", []):
        for t in re.split(r"[,;/|•\n]|\(|\)", str(s.get("items", ""))):
            t = t.strip().lower()
            if 2 <= len(t) <= 30:
                terms.add(t)
    for kw in profile.get("preferences", {}).get("role_keywords", []):
        terms.add(str(kw).strip().lower())
    return sorted(terms)


def _overlap(job: dict, terms: list[str]) -> float:
    title = (job.get("title") or "").lower()
    body = (job.get("description") or "").lower()
    n = 0.0
    for t in terms:
        rx = re.compile(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])")
        if rx.search(title):
            n += 3
        if rx.search(body):
            n += 1
    return n


def rank_local(jobs: list[dict], profile: dict) -> list[dict]:
    terms = _skill_terms(profile)
    return sorted(jobs, key=lambda j: (_overlap(j, terms), len(j.get("description") or "")), reverse=True)


def score_key(profile: dict, cfg=None) -> str:
    """Identifies what a score was computed against: CV text, target levels, prompt version."""
    seniority = profile.get("preferences", {}).get("seniority", [])
    basis = profile_mod.as_cv_text(profile) + "\n" + ",".join(sorted(seniority)) + "\n" + PROMPT_VERSION
    return policy.digest([basis, profile.get("work_authorization", []), _PROMPT, policy.fingerprint(cfg, "score")])


# --------------------------------------------------------------- score ----
def score_jobs(jobs: list[dict], profile: dict, cfg=None, log=print) -> dict[str, dict]:
    """AI-score `jobs`. Jobs the AI did not score are simply absent from the result."""
    cv = profile_mod.as_cv_text(profile)
    wa = profile.get("work_authorization", [])
    seniority = profile.get("preferences", {}).get("seniority", [])
    results: dict[str, dict] = {}
    for i in range(0, len(jobs), BATCH):
        policy.check_workflow()
        if policy.workflow():
            policy.workflow().next_batch()
        batch = jobs[i:i + BATCH]
        try:
            data = engine.generate_json(_prompt(cv, wa, batch, seniority), cfg=cfg, task="score")
            for item in _items(data):
                if not isinstance(item, dict):
                    continue
                jid = str(item.get("id", ""))
                score = _coerce_score(item.get("score"))
                if jid in {str(j["id"]) for j in batch} and jid not in results and score is not None:
                    source_job = next(j for j in batch if str(j["id"]) == jid)
                    reason = " ".join(str(item.get("reason", "")).split()[:12])[:120]
                    if "description" in source_job and not source_job.get("description"):
                        score = min(score, 70)
                        reason = "title only: " + reason
                    results[jid] = {"score": score, "reason": reason}
            log(f"Scored batch {i // BATCH + 1} ({len(batch)}).")
        except policy.PolicyError as e:
            policy.check_workflow()
            log(f"Scoring batch {i // BATCH + 1} failed: {e}")
        except Exception as e:  # noqa: BLE001
            log(f"Scoring batch {i // BATCH + 1} failed: {e}")
    return results


def apply_scores(jobs: list[dict], scores: dict[str, dict], key: str | None = None) -> list[dict]:
    for j in jobs:
        s = scores.get(j["id"])
        if s:
            j["score"], j["score_reason"] = s["score"], s["reason"]
            if key:
                j["score_key"] = key
        else:
            j.setdefault("score", None)
            j.setdefault("score_reason", "not scored")
    return sort_jobs(jobs)


def sort_jobs(jobs: list[dict]) -> list[dict]:
    jobs.sort(key=lambda x: (x.get("score") is None, -(x.get("score") or 0)))
    return jobs


def run(jobs: list[dict], profile: dict, old_jobs: list[dict], cfg: dict, log=print) -> list[dict]:
    """Full scoring pass for a fetch: reuse, local pre-rank + cap, AI score."""
    key = score_key(profile, cfg)
    old = {j.get("id"): j for j in old_jobs}
    todo: list[dict] = []
    reused = 0
    for j in jobs:
        o = old.get(j["id"])
        if o and o.get("score") is not None and o.get("score_key") == key and policy.digest(o.get("description", "")) == policy.digest(j.get("description", "")) and all(o.get(k) == j.get(k) for k in ("title", "company", "location", "level")):
            j["score"], j["score_reason"], j["score_key"] = o["score"], o.get("score_reason", ""), key
            reused += 1
        else:
            todo.append(j)
    cap = max(1, min(500, int(cfg.get("max_ai_scored", 40) or 40)))
    ranked = rank_local(todo, profile)
    send, skipped = ranked[:cap], ranked[cap:]
    for j in skipped:
        j["score"], j["score_reason"] = None, "not scored: low keyword overlap"
    scores = score_jobs(send, profile, cfg=cfg, log=log) if send else {}
    for j in send:
        s = scores.get(j["id"])
        if s:
            j["score"], j["score_reason"], j["score_key"] = s["score"], s["reason"], key
        else:
            j["score"], j["score_reason"] = None, "not scored"
    log(f"{len(scores)} scored, {reused} reused, {len(skipped)} left unscored (low keyword overlap), "
        f"{len(send) - len(scores)} failed.")
    return sort_jobs(jobs)
