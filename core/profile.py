"""The user's profile — the single source of truth for everything the app does.

Populated by importing a CV (core.cv_import) and then edited/confirmed in the
Profile tab. Saved to data/profile.json (gitignored). Nothing here is specific
to any one person.
"""
from __future__ import annotations

import json
import re

from . import config

EMPTY_PROFILE = {
    "personal": {
        "name": "",
        "email": "",
        "phone": "",
        "location": "",      # current city/country
        "links": [],         # ["linkedin.com/in/…", "github.com/…", portfolio]
    },
    "nationality": "",
    "summary": "",
    "skills": [],            # [{"category": "Programming", "items": "Python, SQL, …"}]
    "experience": [],        # [{"title","org","location","dates","bullets":[]}]
    "projects": [],          # [{"name","stack","dates","bullets":[]}]
    "education": [],         # [{"degree","institution","dates","detail"}]
    "certifications": [],    # ["…"]
    "languages": [],         # ["English (native)", …]

    # Work authorization per region — drives cover-letter claims and filtering.
    # status examples: "citizen", "permanent resident", "work visa held",
    # "student visa — part-time/intern eligible", "needs sponsorship", "none"
    "work_authorization": [],  # [{"region": "Germany", "status": "student visa — Werkstudent/intern eligible"}]

    # What/where to search — fully user-controlled in the Profile tab.
    "preferences": {
        "target_country": "",        # MANDATORY
        "target_city": "",
        "also_remote": True,
        "seniority": ["internship", "working student", "junior"],  # checkbox set
        "role_keywords": [],         # MANDATORY (>=1): ["data scientist", …]
    },

    "meta": {"imported": False, "source_cv": ""},
}


def _deep_default(obj, default):
    """Fill missing keys from default so old/partial profiles stay valid."""
    if isinstance(default, dict):
        out = dict(default)
        if isinstance(obj, dict):
            for k, v in obj.items():
                out[k] = _deep_default(v, default.get(k, v))
        return out
    return obj if obj is not None else default


# ------------------------------------------------------------ normalize ----
def _s(v) -> str:
    if v is None or isinstance(v, (dict, list, tuple, set)):
        return ""
    return str(v).strip()


def _flat_str(v) -> str:
    """Best-effort string from anything (a dict becomes its first useful text)."""
    if isinstance(v, dict):
        for k in ("name", "title", "text", "value", "label"):
            if _s(v.get(k)):
                return _s(v[k])
        return " ".join(_s(x) for x in v.values() if _s(x))
    if isinstance(v, (list, tuple)):
        return ", ".join(x for x in (_flat_str(i) for i in v) if x)
    return _s(v)


def _str_list(v, split_commas: bool = False) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        parts = re.split(r"[\n,]+" if split_commas else r"\n+", v)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(v, dict):
        v = [v]
    if isinstance(v, (list, tuple, set)):
        return [x for x in (_flat_str(i) for i in v) if x]
    return [_s(v)] if _s(v) else []


def _bullets(v) -> list[str]:
    out = []
    for b in _str_list(v):
        b = re.sub(r"^[\s\-•*·▪●]+", "", b).strip()
        if b:
            out.append(b)
    return out


def _entries(v, keys: tuple, required_any: tuple, bullet_keys=("bullets",)) -> list[dict]:
    if isinstance(v, dict):
        v = [v]
    out = []
    for it in v if isinstance(v, list) else []:
        if not isinstance(it, dict):
            continue
        e = {k: (_bullets(it.get(k)) if k in bullet_keys else _flat_str(it.get(k))) for k in keys}
        if any(e.get(k) for k in required_any):
            out.append(e)
    return out


def _skills(v) -> list[dict]:
    if isinstance(v, str):
        v = _str_list(v)
    out = []
    for it in v if isinstance(v, list) else []:
        if isinstance(it, dict):
            cat, items = _flat_str(it.get("category")), _flat_str(it.get("items"))
        elif isinstance(it, (list, tuple)):
            cat, items = (_flat_str(it[0]).rstrip(": ") if it else ""), (_flat_str(it[1]) if len(it) > 1 else "")
        else:
            txt = _s(it)
            cat, _, items = txt.partition(":") if ":" in txt else ("", "", txt)
            cat, items = cat.strip(), items.strip()
        if cat or items:
            out.append({"category": cat, "items": items})
    return out


def normalize_profile(profile) -> dict:
    """Coerce every field to the shape documented in EMPTY_PROFILE and drop empty
    entries, so malformed AI output can never break rendering later."""
    prof = _deep_default(profile if isinstance(profile, dict) else {}, EMPTY_PROFILE)
    pers = prof.get("personal") if isinstance(prof.get("personal"), dict) else {}
    prof["personal"] = {**pers,
                        **{k: _s(pers.get(k)) for k in ("name", "email", "phone", "location")},
                        "links": _str_list(pers.get("links"))}
    prof["nationality"] = _s(prof.get("nationality"))
    prof["summary"] = _s(prof.get("summary"))
    prof["skills"] = _skills(prof.get("skills"))
    prof["experience"] = _entries(prof.get("experience"), ("title", "org", "location", "dates", "bullets"),
                                  ("title", "org", "bullets"))
    prof["projects"] = _entries(prof.get("projects"), ("name", "stack", "dates", "bullets"),
                                ("name", "bullets"))
    prof["education"] = _entries(prof.get("education"), ("degree", "institution", "dates", "detail"),
                                 ("degree", "institution"), bullet_keys=())
    prof["certifications"] = _str_list(prof.get("certifications"))
    prof["languages"] = _str_list(prof.get("languages"))
    prof["work_authorization"] = _entries(prof.get("work_authorization"), ("region", "status"),
                                          ("region", "status"), bullet_keys=())
    prefs = prof.get("preferences") if isinstance(prof.get("preferences"), dict) else {}
    prof["preferences"] = {**prefs,
                           "target_country": _s(prefs.get("target_country")),
                           "target_city": _s(prefs.get("target_city")),
                           "also_remote": bool(prefs.get("also_remote", True)),
                           "seniority": _str_list(prefs.get("seniority"), split_commas=True),
                           "role_keywords": _str_list(prefs.get("role_keywords"), split_commas=True)}
    meta = prof.get("meta") if isinstance(prof.get("meta"), dict) else {}
    prof["meta"] = {**meta, "imported": bool(meta.get("imported", False)), "source_cv": _s(meta.get("source_cv"))}
    return prof


def load_profile() -> dict:
    data = config.read_json(config.PROFILE_PATH, None)
    if isinstance(data, dict):
        return normalize_profile(data)
    return json.loads(json.dumps(EMPTY_PROFILE))


def save_profile(profile: dict) -> dict:
    merged = normalize_profile(profile)
    config.atomic_write_json(config.PROFILE_PATH, merged)
    return merged


def backup_profile() -> None:
    """Rolling one-deep backup (data/profile.prev.json) taken before AI output
    overwrites the profile (CV import / CV improve)."""
    import shutil
    if config.PROFILE_PATH.exists():
        shutil.copy2(config.PROFILE_PATH, config.PROFILE_PATH.with_name("profile.prev.json"))


def missing_required(profile: dict) -> list[str]:
    """Mandatory fields that are still empty. Drives validation + the setup gate."""
    p = profile.get("personal", {})
    prefs = profile.get("preferences", {})
    miss = []
    if not p.get("name"):
        miss.append("name")
    if not profile.get("nationality"):
        miss.append("nationality")
    if not prefs.get("target_country"):
        miss.append("target_country")
    if not prefs.get("role_keywords"):
        miss.append("role_keywords")
    return miss


def is_ready(profile: dict) -> bool:
    return not missing_required(profile)


def as_cv_text(profile: dict) -> str:
    """Render the profile as plain text — the grounding context for the LLM."""
    p = profile.get("personal", {})
    lines = [p.get("name", ""), p.get("location", ""),
             " • ".join(filter(None, [p.get("email", ""), p.get("phone", "")])),
             " • ".join(profile.get("personal", {}).get("links", []))]
    if profile.get("nationality"):
        lines.append(f"Nationality: {profile['nationality']}")
    if profile.get("summary"):
        lines += ["", "SUMMARY", profile["summary"]]
    if profile.get("skills"):
        lines += ["", "SKILLS"]
        for s in profile["skills"]:
            lines.append(f"{s.get('category','')}: {s.get('items','')}")
    for section, key in (("EXPERIENCE", "experience"), ("PROJECTS", "projects")):
        items = profile.get(key, [])
        if items:
            lines += ["", section]
            for it in items:
                head = it.get("title") or it.get("name") or ""
                extra = it.get("org") or it.get("stack") or ""
                lines.append(f"{head} | {extra} | {it.get('dates','')}".strip(" |"))
                for b in it.get("bullets", []):
                    lines.append(f"  - {b}")
    if profile.get("education"):
        lines += ["", "EDUCATION"]
        for e in profile["education"]:
            lines.append(f"{e.get('degree','')} — {e.get('institution','')} "
                         f"({e.get('dates','')}) {e.get('detail','')}".strip())
    if profile.get("certifications"):
        lines += ["", "CERTIFICATIONS", " • ".join(profile["certifications"])]
    if profile.get("languages"):
        lines += ["", "LANGUAGES", " • ".join(profile["languages"])]
    if profile.get("work_authorization"):
        lines += ["", "WORK AUTHORIZATION"]
        for w in profile["work_authorization"]:
            lines.append(f"{w.get('region','')}: {w.get('status','')}")
    return "\n".join(filter(lambda x: x is not None, lines))
