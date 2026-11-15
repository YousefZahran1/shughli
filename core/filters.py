"""Relevance + level filtering, driven by profile preferences.

Two independent gates, both from the user's profile:
  1. role_keywords  -> is the title about the kind of work they want?
  2. seniority      -> is the job at a level they ticked? A job whose detected
                       level (internship / working student / junior / mid /
                       senior) is NOT in their selected seniority is dropped.
                       Jobs with no detectable level ("general") always pass.

"Level" is what the UI used to call "Eligibility" — it's just the seniority
level of the role, now actually tied to what the user picked.

All matching is whole-word (so "Internal" is not an internship and "Staffing"
is not senior). A wrong level silently drops a job, so precision matters.
"""
from __future__ import annotations

import re

# --- title level vocabulary (whole words, with their normal inflections) ---
_SENIOR_WORDS = {"senior", "sr", "principal", "staff", "director", "vp", "chief"}
_SENIOR_PHRASES = [r"head of", r"head,"]
_ROLE_NOUNS = {"lead", "architect", "manager"}          # weaker than junior/intern words
_WS_WORDS = {"werkstudent", "werkstudentin", "werkstudenten"}
_WS_PHRASES = [r"working student", r"student assistant"]
_INTERN_WORDS = {"intern", "interns", "internship", "internships", "praktikant",
                 "praktikantin", "praktikanten", "praktikum", "trainee", "trainees"}
_JUNIOR_WORDS = {"junior", "graduate", "entry"}
_JUNIOR_PHRASES = [r"new grad", r"entry[- ]level"]
_MID_PHRASES = [r"mid[- ]level"]


def _norm(s) -> str:
    return s.lower() if isinstance(s, str) else ""


def _words(t: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿß]+", t.replace("sr.", "sr ")))


def _has_phrase(t: str, phrases: list[str]) -> bool:
    return any(re.search(rf"(?<![a-zà-ÿ]){p}(?![a-zà-ÿ])", t) for p in phrases)


# --- light stemming so science/scientist, engineer/engineering, … match ---
_STEM_PREFIXES = ("scien", "engineer", "analy", "develop")


def _stem(w: str) -> str:
    for p in _STEM_PREFIXES:
        if w.startswith(p):
            return p
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


def _stems(t: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-zà-ÿß0-9]+", t)}


def title_relevant(title: str, prefs: dict) -> bool:
    """Does the title match the user's role keywords? (keywords are mandatory)."""
    t = _norm(title)
    if not t:
        return False
    keywords = [k.lower() for k in prefs.get("role_keywords", [])]
    if not keywords:
        return True
    tstems = _stems(t)
    for kw in keywords:
        words = [w for w in re.findall(r"[a-zà-ÿß0-9]+", kw) if len(w) >= 2]
        if words and all(_stem(w) in tstems for w in words):
            return True
    return False


# --- years-of-experience requirement (description) ---
_YEARS_A = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?|jahre?n?)\b[^.\n]{0,40}?(?:experience|erfahrung)")
_YEARS_B = re.compile(r"(?:experience|erfahrung)[^.\n]{0,40}?(\d{1,2})\s*\+?\s*(?:years?|yrs?|jahre?n?)\b")


def _years_required(description: str) -> int | None:
    d = _norm(description)
    nums = [int(n) for rx in (_YEARS_A, _YEARS_B) for n in rx.findall(d)]
    nums = [n for n in nums if 1 <= n <= 40]
    return min(nums) if nums else None


def job_level(title: str, description: str = "") -> str:
    """Detected seniority level of a job, or 'general' if none is clear."""
    t = _norm(title)
    w = _words(t)
    if w & _SENIOR_WORDS or _has_phrase(t, _SENIOR_PHRASES):
        return "senior"
    if w & _WS_WORDS or _has_phrase(t, _WS_PHRASES):
        return "working student"
    if w & _INTERN_WORDS:
        return "internship"
    if w & _JUNIOR_WORDS or _has_phrase(t, _JUNIOR_PHRASES):
        return "junior"
    if w & _ROLE_NOUNS:
        return "senior"
    if _has_phrase(t, _MID_PHRASES):
        return "mid"
    # Title gives no level: fall back to an explicit years-of-experience ask.
    yrs = _years_required(description)
    if yrs is not None:
        if yrs >= 5:
            return "senior"
        if yrs >= 3:
            return "mid"
    return "general"


def passes(job: dict, prefs: dict) -> bool:
    if not title_relevant(job.get("title", ""), prefs):
        return False
    selected = {s.lower() for s in prefs.get("seniority", [])}
    level = job_level(job.get("title", ""), job.get("description", ""))
    # If the user picked specific levels, drop clearly-detected levels they
    # didn't pick. 'general' (undetectable) always passes.
    if selected and level != "general" and level not in selected:
        return False
    return True


def annotate(job: dict) -> dict:
    job["level"] = job_level(job.get("title", ""), job.get("description", ""))
    return job
