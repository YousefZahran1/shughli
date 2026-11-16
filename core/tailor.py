"""Tailor a CV + cover letter for one job, grounded in the user's profile.

The model may reorder/reframe/re-emphasize and write a natural cover letter, but
never invent skills, metrics, employers, or dates. Fixed sections (experience,
education, certifications, languages) are rendered straight from the profile so
they can't be dropped or fabricated. Work-authorization claims in the cover
letter use only what the profile states for the job's region.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor

from . import policy, validation, config, engine, profile as profile_mod
from .textutil import excerpt

DARK, ACCENT, MUTED = RGBColor(0x1F, 0x29, 0x37), RGBColor(0x0F, 0x4C, 0x81), RGBColor(0x4B, 0x55, 0x63)
ARIAL = "Arial"


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower())
    return re.sub(r"-+", "-", s).strip("-")[:60] or "job"


def _str(v) -> str:
    return str(v).strip() if v is not None else ""


# Region aliases for work-authorization matching (profile region -> terms).
_REGION_ALIASES = [
    {"us", "usa", "united states", "united states of america", "america"},
    {"uk", "united kingdom", "great britain", "england", "britain"},
    {"uae", "united arab emirates", "emirates"},
    {"germany", "deutschland"},
    {"ksa", "saudi arabia", "saudi"},
]


def _region_terms(region: str) -> set[str]:
    r = region.strip().lower()
    for group in _REGION_ALIASES:
        if r in group:
            return group
    return {r}


def _work_auth_for(job: dict, profile: dict) -> str:
    """Return the profile's work-auth status matching this job's region, if any.
    Whole-word, case-insensitive ('US' must not match 'Sydney, Australia')."""
    haystack = (_str(job.get("region")) + " " + _str(job.get("location"))).lower()
    for w in profile.get("work_authorization", []):
        reg = _str(w.get("region"))
        if not reg:
            continue
        for term in _region_terms(reg):
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack):
                return _str(w.get("status"))
    return ""


_PROMPT = """You tailor job applications. Ground EVERYTHING in the candidate's real CV.
Reorder, reframe, and re-emphasize to fit the target job. NEVER invent skills,
metrics, employers, dates, or projects. Keep all facts (including the exact
university/employer names) exactly as written in the CV. Use only projects that
appear in the CV, and start each project header with the project's exact name.

<master_cv>
{cv}
</master_cv>

<target_job>
Title: {title}
Company: {company}
Location: {location}
Description:
{desc}
</target_job>

{wa_line}

Cover letter: 3 short paragraphs, 180-260 words, first person, plain text. Write the body only: no greeting, no sign-off, no date, no address. The app adds those.
- Paragraph 1: this specific role, and the one or two things in the candidate's background that fit it best. Open with something concrete from the CV, not with enthusiasm.
- Paragraph 2: one project or job from the CV told with detail: what was built, with what tools, and the result as the CV states it.
- Paragraph 3: why this company or role, tied to something specific in the job description, then one closing sentence.
Write the way a capable graduate writes an email to a hiring manager: plain words, sentences of mixed length, contractions allowed. Do not use: "I am writing to", "I am excited", "thrilled", "passionate", "delve", "leverage", "fast-paced", "proven track record", "great fit", "aligns perfectly", three adjectives in a row, em dashes, or sentences that repeat the job description back.
Every claim must be traceable to the CV. If the CV gives no number for something, do not supply one.

Return ONLY valid JSON:
{{
  "summary": "<2-3 sentence tailored summary>",
  "skills": [["<Category>: ","<comma-separated items>"]],
  "projects": [{{"header":"<project | stack>","date":"<dates>","bullets":["",""]}}],
  "cover_letter": "<full text, real newlines between paragraphs>",
  "fit": {{"matches":["<strength vs JD>"],"gaps":["<honest gap>"]}}
}}
Order skills and projects by relevance to the target job."""


def _gen_prompt(cv: str, job: dict, profile: dict) -> str:
    wa = _work_auth_for(job, profile)
    if wa:
        wa_line = (f"Candidate work authorization for this location: \"{wa}\". "
                   "If genuinely relevant, mention eligibility naturally and only "
                   "to the extent this status supports. Do not overclaim.")
    else:
        wa_line = ("Make no claim about visa, work-permit, or sponsorship status "
                   "unless the CV explicitly supports it for this location.")
    return _PROMPT.format(cv=cv, title=job.get("title"), company=job.get("company"),
                          location=job.get("location"),
                          desc=excerpt(job.get("description") or "", 3000), wa_line=wa_line)


# ------------------------------------------------------- output cleanup ----
def clean_prose(t) -> str:
    """Local cleanup for model prose: dashes, markdown markers, curly quotes,
    double spaces. Never deletes phrases (that breaks sentences)."""
    t = _str(t)
    t = re.sub(r"\s*—\s*", ", ", t)          # em dash -> comma
    t = t.replace("–", "-")                    # en dash -> hyphen
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t, flags=re.S)
    t = re.sub(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])", r"\1", t)
    t = t.replace("`", "")
    t = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", t, flags=re.M)
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


_GREETING = re.compile(r"^(dear|hi|hello|to whom|good (?:morning|afternoon))\b", re.I)
_SIGNOFF = re.compile(r"^(sincerely|best regards|kind regards|warm regards|regards|best|yours|"
                      r"respectfully|thank you|thanks|cheers)\b", re.I)


def strip_letter_frame(text: str) -> str:
    """Remove a greeting / sign-off the model wrote anyway (the app adds its own)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    if paras and _GREETING.match(paras[0]) and len(paras[0]) < 90:
        paras = paras[1:]
    if paras:
        lines = paras[-1].splitlines()
        idx = next((i for i, l in enumerate(lines) if _SIGNOFF.match(l.strip())), None)
        # a sign-off is the last 1-3 short lines; a long line starting with "Thanks..." is prose
        if idx is not None and len(lines) - idx <= 3 and all(len(l) < 60 for l in lines[idx:]):
            head = "\n".join(lines[:idx]).strip()
            paras = paras[:-1] + ([head] if head else [])
    return "\n\n".join(paras)


# --------------------------------------------------- shape normalizing ----
def _split_items(items: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in items:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]


def _norm_skills(raw) -> list[list[str]]:
    """-> [[label, items]]; accepts pairs, {category, items} dicts, or plain strings."""
    out = []
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, dict):
            label = _str(it.get("category") or it.get("label") or it.get("name"))
            items = it.get("items")
            items = ", ".join(map(_str, items)) if isinstance(items, list) else _str(items)
        elif isinstance(it, (list, tuple)):
            label = _str(it[0]) if it else ""
            items = it[1] if len(it) > 1 else ""
            items = ", ".join(map(_str, items)) if isinstance(items, list) else _str(items)
        else:
            txt = _str(it)
            label, sep, items = txt.partition(":")
            if not sep:
                label, items = "", txt
        label = label.strip().rstrip(":").strip()
        if label or items.strip():
            out.append([f"{label}: " if label else "", items.strip()])
    return out


def _norm_projects(raw) -> list[dict]:
    out = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        header = _str(it.get("header") or it.get("name"))
        bullets = it.get("bullets")
        if isinstance(bullets, str):
            bullets = [b for b in re.split(r"\n+", bullets)]
        bullets = [re.sub(r"^[\s\-•*]+", "", _str(b)) for b in (bullets or []) if _str(b)]
        if header or bullets:
            out.append({"header": header, "date": _str(it.get("date") or it.get("dates")),
                        "bullets": bullets})
    return out


def _nm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", _str(s).lower()).strip()


def _ground_projects(projects: list[dict], profile: dict) -> list[dict]:
    """Keep only projects that match a profile project by name; take dates from the profile."""
    prof_projects = [p for p in profile.get("projects", []) if _str(p.get("name"))]
    if not prof_projects:
        return []
    kept = []
    for pr in projects:
        h = _nm(pr["header"])
        match = next((pp for pp in prof_projects
                      if _nm(pp["name"]) and (h == _nm(pp["name"]) or h.startswith(_nm(pp["name"]) + " "))), None)
        if match:
            pr = dict(pr, date=_str(match.get("dates")))
            kept.append(pr)
    if kept:
        return kept
    return [{"header": f"{p.get('name','')} | {p.get('stack','')}".strip(" |"),
             "date": _str(p.get("dates")), "bullets": list(p.get("bullets", []))}
            for p in prof_projects]


def _ground_skills(skills: list[list[str]], cv_text: str, job_desc: str, log=print) -> list[list[str]]:
    """Keep only skill items explicitly present in candidate source text."""
    cv_l = cv_text.lower()
    out = []
    for label, items in skills:
        keep = []
        for item in _split_items(items):
            low = item.lower()
            if low not in cv_l:
                log("tailor: dropped an unsupported skill")
                continue
            keep.append(item)
        if keep:
            out.append([label, ", ".join(keep)])
    return out


def generate(job: dict, profile: dict, cfg=None) -> dict:
    cv = profile_mod.as_cv_text(profile)
    data = engine.generate_json(_gen_prompt(cv, job, profile), cfg=cfg, timeout=300, task="main", schema=validation.TAILOR_SCHEMA)
    if not isinstance(data, dict):
        raise engine.EngineError("Generation returned an unexpected shape.")
    for key in ("summary", "skills", "cover_letter"):
        if key not in data:
            raise engine.EngineError(f"Generation missing '{key}'.")
    validation.content(data)
    data["source_checks"] = validation.source_checks(json.dumps(data, ensure_ascii=False), cv)
    data["summary"] = clean_prose(data.get("summary"))
    data["cover_letter"] = clean_prose(strip_letter_frame(_str(data.get("cover_letter"))))
    data["skills"] = _ground_skills(_norm_skills(data.get("skills")), cv, job.get("description", ""))
    data["projects"] = _ground_projects(_norm_projects(data.get("projects")), profile)
    fit = data.get("fit") if isinstance(data.get("fit"), dict) else {}
    data["fit"] = {k: [_str(x) for x in (fit.get(k) or []) if _str(x)] if isinstance(fit.get(k), list) else []
                   for k in ("matches", "gaps")}
    validation.schema_check({k: data[k] for k in validation.TAILOR_SCHEMA["properties"]}, validation.TAILOR_SCHEMA)
    return data


# --------------------------------------------------------------- render ----
def _p(doc, text="", *, size=9.5, bold=False, italic=False, color=DARK, before=0, after=0):
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(before)
    para.paragraph_format.space_after = Pt(after)
    run = para.add_run(text)
    run.font.name, run.font.size = ARIAL, Pt(size)
    run.bold, run.italic, run.font.color.rgb = bold, italic, color
    return para


def _heading(doc, text):
    _p(doc, text, size=11, bold=True, color=ACCENT, before=6, after=2)


def _bullets(doc, items):
    for b in items:
        bp = doc.add_paragraph(style="List Bullet")
        bp.paragraph_format.space_after = Pt(0)
        r = bp.add_run(str(b)); r.font.name, r.font.size = ARIAL, Pt(9.5)


def render_cv_docx(gen: dict, job: dict, profile: dict, out: Path) -> Path:
    p = profile.get("personal", {})
    doc = Document()
    s = doc.sections[0]
    s.page_width, s.page_height = Inches(8.5), Inches(11)
    s.top_margin = s.bottom_margin = Inches(0.4)
    s.left_margin = s.right_margin = Inches(0.55)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = ARIAL, Pt(9.5)
    style.paragraph_format.space_after = Pt(0)

    _p(doc, p.get("name", ""), size=18, bold=True, color=ACCENT)
    contact = " • ".join(filter(None, [p.get("location", ""), p.get("email", ""),
                                        p.get("phone", "")] + list(p.get("links", []))))
    _p(doc, contact, size=8.5, color=MUTED, after=6)

    _heading(doc, "SUMMARY")
    _p(doc, gen.get("summary", "") or profile.get("summary", ""), after=4)

    skills = _norm_skills(gen.get("skills")) or _norm_skills(profile.get("skills", []))
    if skills:
        _heading(doc, "CORE SKILLS")
        for label, body in skills:
            para = doc.add_paragraph(); para.paragraph_format.space_after = Pt(1)
            r1 = para.add_run(label); r1.bold = True; r1.font.name, r1.font.size = ARIAL, Pt(9.5)
            r2 = para.add_run(body); r2.font.name, r2.font.size = ARIAL, Pt(9.5)

    if profile.get("experience"):
        _heading(doc, "EXPERIENCE")
        for e in profile["experience"]:
            para = doc.add_paragraph(); para.paragraph_format.space_before = Pt(2)
            r = para.add_run(f"{e.get('title','')} — {e.get('org','')}".strip(" —"))
            r.bold = True; r.font.name, r.font.size = ARIAL, Pt(9.5)
            if e.get("dates"):
                rd = para.add_run(f"   {e['dates']}"); rd.font.size = Pt(8.5)
                rd.font.color.rgb = MUTED; rd.font.name = ARIAL
            _bullets(doc, e.get("bullets", []))

    projects = _norm_projects(gen.get("projects")) or [
        {"header": f"{pr.get('name','')} | {pr.get('stack','')}".strip(" |"),
         "date": pr.get("dates", ""), "bullets": pr.get("bullets", [])}
        for pr in profile.get("projects", [])]
    if projects:
        _heading(doc, "PROJECTS")
        for pr in projects:
            para = doc.add_paragraph(); para.paragraph_format.space_before = Pt(2)
            r = para.add_run(pr.get("header", "")); r.bold = True; r.italic = True
            r.font.name, r.font.size = ARIAL, Pt(9.5)
            if pr.get("date"):
                rd = para.add_run(f"   {pr['date']}"); rd.font.size = Pt(8.5)
                rd.font.color.rgb = MUTED; rd.font.name = ARIAL
            _bullets(doc, pr.get("bullets", []))

    if profile.get("education"):
        _heading(doc, "EDUCATION")
        for e in profile["education"]:
            _p(doc, f"{e.get('degree','')} — {e.get('institution','')}".strip(" —"), bold=True)
            detail = " • ".join(filter(None, [e.get("dates", ""), e.get("detail", "")]))
            if detail:
                _p(doc, detail, italic=True, color=MUTED)

    if profile.get("certifications"):
        _heading(doc, "CERTIFICATIONS")
        _p(doc, " • ".join(profile["certifications"]))
    if profile.get("languages"):
        _heading(doc, "LANGUAGES")
        _p(doc, " • ".join(profile["languages"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def render_cover_docx(cover_text: str, job: dict, profile: dict, out: Path) -> Path:
    p = profile.get("personal", {})
    doc = Document()
    s = doc.sections[0]
    s.top_margin = s.bottom_margin = Inches(0.8)
    s.left_margin = s.right_margin = Inches(0.9)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = ARIAL, Pt(11)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.25

    _p(doc, p.get("name", ""), size=15, bold=True, color=ACCENT)
    _p(doc, " • ".join(filter(None, [p.get("email", ""), p.get("location", "")])),
       size=10, color=MUTED, after=10)
    _p(doc, _dt.date.today().strftime("%B %d, %Y"), size=10, color=MUTED, after=8)
    if job.get("company"):
        _p(doc, job["company"], bold=True, after=2)
    if job.get("title"):
        _p(doc, f"Re: {job['title']}", color=MUTED, after=10)
    _p(doc, "Dear Hiring Team,", size=11, after=6)
    body = strip_letter_frame(cover_text or "")
    for para in [x for x in re.split(r"\n\s*\n", body) if x.strip()]:
        _p(doc, para.strip(), size=11)
    _p(doc, "Sincerely,", size=11, before=6, after=2)
    _p(doc, p.get("name", ""), size=11)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


# -------------------------------------------------------------- package ----
def profile_hash(profile: dict) -> str:
    return hashlib.sha1(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def package_folder(job: dict) -> Path:
    comp = _slug(job.get("company")) or "company"
    return config.APPS_DIR / f"{comp}-{_slug(job.get('title'))}-{_slug(job.get('id'))}"


def load_saved_package(job: dict, profile: dict, cfg=None) -> dict | None:
    f = package_folder(job) / "package.json"
    saved = config.read_json(f, None)
    if isinstance(saved, dict) and saved.get("cache_key") == package_key(job, profile, cfg) \
            and isinstance(saved.get("package"), dict):
        pkg = saved["package"]
        if Path(pkg.get("cv_docx", "")).exists() and Path(pkg.get("cover_docx", "")).exists():
            return pkg
    return None


def build_package(job: dict, profile: dict, cfg=None, force: bool = False) -> dict:
    """Generate (or return the saved) package. A saved one is reused when the
    profile is unchanged, unless force=True."""
    if not force:
        saved = load_saved_package(job, profile, cfg)
        if saved:
            return saved
    gen = generate(job, profile, cfg=cfg)
    comp = _slug(job.get("company")) or "company"
    folder = package_folder(job)
    cv_path = folder / f"CV-{comp}.docx"
    cl_path = folder / f"Cover-Letter-{comp}.docx"
    render_cv_docx(gen, job, profile, cv_path)
    render_cover_docx(gen.get("cover_letter", ""), job, profile, cl_path)
    config.atomic_write_json(folder / "job.json", job)
    pkg = {"source_checks": gen["source_checks"], "summary": gen.get("summary", ""), "skills": gen.get("skills", []),
           "projects": gen.get("projects", []), "cover_letter": gen.get("cover_letter", ""),
           "fit": gen.get("fit", {}), "cv_docx": str(cv_path), "cover_docx": str(cl_path),
           "folder": str(folder)}
    config.atomic_write_json(folder / "package.json",
                             {"cache_key": package_key(job, profile, cfg), "profile_hash": profile_hash(profile), "job_id": job.get("id"), "package": pkg})
    return pkg


def package_key(job, profile, cfg=None):
    return policy.digest([profile, job, _PROMPT, policy.fingerprint(cfg)])
