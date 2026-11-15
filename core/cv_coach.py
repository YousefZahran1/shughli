"""AI CV coach: rate the profile's CV quality, and improve it by asking the
candidate targeted questions and rewriting with their answers.

Grounded: improvements may sharpen wording and add impact the candidate supplies
in their answers, but never invent facts.
"""
from __future__ import annotations

import json

from . import engine, profile as profile_mod

# Below this score, the UI shows the red "improve your CV" prompt.
WEAK_THRESHOLD = 65


def _hash(profile: dict) -> str:
    import hashlib
    return hashlib.sha1(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def review(profile: dict, cfg=None) -> dict:
    """Cached in data/cv_review.json by profile hash: an unchanged profile costs no AI call."""
    from . import config
    h = _hash(profile)
    saved = config.read_json(config.CV_REVIEW_PATH, None)
    if isinstance(saved, dict) and saved.get("hash") == h and isinstance(saved.get("review"), dict):
        return saved["review"]
    data = _review_uncached(profile, cfg)
    config.atomic_write_json(config.CV_REVIEW_PATH, {"hash": h, "review": data})
    return data


def _review_uncached(profile: dict, cfg=None) -> dict:
    cv = profile_mod.as_cv_text(profile)
    prompt = f"""You are a senior technical recruiter. Assess this candidate's CV for
how well it will perform in job applications. Be honest and specific.

<cv>
{cv}
</cv>

Return ONLY JSON:
{{
  "score": <int 0-100, overall CV strength>,
  "verdict": "<one blunt sentence>",
  "issues": ["<specific weakness>", "..."],
  "missing": ["<important thing absent: metrics, a summary, links, etc.>", "..."],
  "questions": ["<targeted question whose answer would most improve the CV>", "..."]
}}
Give 3-6 issues, 2-5 missing items, and 4-6 questions. Questions should pull out
quantified impact, scope, tools, and outcomes the CV is missing."""
    data = engine.generate_json(prompt, cfg=cfg, task="main")
    if not isinstance(data, dict):
        raise engine.EngineError("CV review returned an unexpected shape.")
    data.setdefault("score", 0)
    for k in ("issues", "missing", "questions"):
        data.setdefault(k, [])
    return data


def improve(profile: dict, qa: list[dict], cfg=None) -> dict:
    """qa = [{"question": "...", "answer": "..."}]. Returns improved profile."""
    cv = profile_mod.as_cv_text(profile)
    answers = "\n".join(f"Q: {x.get('question','')}\nA: {x.get('answer','')}" for x in qa)
    prompt = f"""Improve this candidate's CV content. You may sharpen wording, add
quantified impact, and restructure bullets — but use ONLY facts from the CV and
the candidate's answers below. NEVER invent employers, dates, metrics, or skills
they did not state. Keep names (companies, universities) exactly as given.

<current_cv>
{cv}
</current_cv>

<candidate_answers>
{answers}
</candidate_answers>

Return ONLY JSON with improved content for these fields (same shapes as a profile):
{{
  "summary": "<stronger 2-3 sentence professional summary>",
  "skills": [{{"category":"","items":""}}],
  "experience": [{{"title":"","org":"","location":"","dates":"","bullets":[]}}],
  "projects": [{{"name":"","stack":"","dates":"","bullets":[]}}]
}}
Rewrite bullets to lead with impact and include numbers where the candidate gave
them. Keep every real role/project; do not drop any."""
    data = engine.generate_json(prompt, cfg=cfg, timeout=300, task="main")
    if not isinstance(data, dict):
        raise engine.EngineError("CV improve returned an unexpected shape.")
    out = json.loads(json.dumps(profile))  # copy
    if data.get("summary"):
        out["summary"] = data["summary"]
    if data.get("skills"):
        out["skills"] = data["skills"]
    out["experience"] = _merge(profile.get("experience", []), data.get("experience"),
                               lambda e: (_nm(e.get("org")), _nm(e.get("title"))), ("bullets",))
    out["projects"] = _merge(profile.get("projects", []), data.get("projects"),
                             lambda e: (_nm(e.get("name")),), ("bullets", "stack"))
    out.setdefault("meta", {})["improved"] = True
    return profile_mod.normalize_profile(out)


def _nm(v) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()


def _merge(existing: list, returned, keyf, take: tuple) -> list:
    """Match returned entries to existing ones by normalized key (never by
    position: the model may reorder). Matched entries keep their identity fields
    (title/org/location/dates/name) and take only `take` fields from the model.
    Unmatched existing entries stay as they were; unmatched returned ones are dropped."""
    got = {}
    for r in returned if isinstance(returned, list) else []:
        if isinstance(r, dict):
            got.setdefault(keyf(r), r)
    out = []
    for e in existing:
        e = dict(e)
        r = got.get(keyf(e))
        if r:
            for k in take:
                if r.get(k):
                    e[k] = r[k]
        out.append(e)
    return out
