"""Job sourcing — driven entirely by the user's profile preferences.

Sources (all optional, toggled in config/Settings):
  - JobSpy -> Indeed / LinkedIn / Glassdoor for the user's target city,
    plus an optional remote pass.
  - Remotive API  -> remote roles (best-effort; the free feed is small and its
    `search`/`category` filters are unreliable, so we make ONE request and let
    the title filter decide).
  - Arbeitnow API -> used only when the target country is Germany. Its `search`
    parameter is ignored by the API, so one request, title filter does the rest.

One dead board never kills a run; every row is NaN-safe.
"""
from __future__ import annotations

import hashlib
import html as _html
import re
import traceback

import requests

from . import policy, filters


def _str(v) -> str:
    if v is None or v != v:  # NaN != NaN
        return ""
    return str(v).strip()


# ------------------------------------------------------------ cleaning ----
_LI_OPEN = re.compile(r"<li\b[^>]*>", re.I)
_BLOCK_TAGS = re.compile(r"</?(?:p|div|br|ul|ol|h[1-6]|tr|table|section|article)\b[^>]*>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")


def clean_text(s) -> str:
    """HTML/markdown -> plain text. Keeps line breaks for block elements and
    list items, drops tags, unescapes entities, strips emphasis markers."""
    s = _str(s)
    if not s:
        return ""
    s = _LI_OPEN.sub("\n- ", s)
    s = _BLOCK_TAGS.sub("\n", s)
    s = _ANY_TAG.sub("", s)
    s = _html.unescape(s)
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|])", r"\1", s)               # markdown backslash escapes
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s, flags=re.S)                 # bold
    s = re.sub(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])", r"\1", s)  # italics
    s = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*", "", s, flags=re.M)             # headings
    s = re.sub(r"^[ \t]*[*+][ \t]+", "- ", s, flags=re.M)                 # bullets
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)                        # links -> text
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _mk_id(url: str, title: str, company: str) -> str:
    basis = (url or f"{title}|{company}").strip().lower()
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def _normalize(*, title, company, location, region, source, url,
               description="", salary="", date="") -> dict:
    title, company, location, url = _str(title), _str(company), _str(location), _str(url)
    return {"id": _mk_id(url, title, company), "title": title, "company": company,
            "location": location, "region": region, "source": source, "url": url,
            "description": clean_text(description), "salary": _str(salary), "date": _str(date)}


def _search_term(prefs: dict) -> str:
    kws = prefs.get("role_keywords") or ["data scientist", "software engineer"]
    return " OR ".join(f"({k})" for k in kws[:6])


def _salary(r) -> str:
    def num(v):
        try:
            return int(float(v)) if v is not None and v == v else None
        except (TypeError, ValueError):
            return None
    lo, hi = num(r.get("min_amount")), num(r.get("max_amount"))
    cur = r.get("currency") if isinstance(r.get("currency"), str) else ""
    if lo and hi:
        return f"{cur}{lo:,}-{hi:,}".strip()
    return f"{cur}{lo:,}+".strip() if lo else ""


# --------------------------------------------------------------- country ----
# User text -> a value JobSpy's Country enum accepts. Unknown names make
# scrape_jobs raise for the WHOLE call, so we map first and skip when unmapped.
_COUNTRY_ALIASES = {
    "uae": "united arab emirates", "emirates": "united arab emirates",
    "ksa": "saudi arabia", "saudi": "saudi arabia", "kingdom of saudi arabia": "saudi arabia",
    "deutschland": "germany", "bundesrepublik deutschland": "germany",
    "us": "usa", "u.s.": "usa", "u.s.a.": "usa", "america": "usa",
    "united states": "usa", "united states of america": "usa",
    "uk": "uk", "united kingdom": "uk", "great britain": "uk", "england": "uk", "britain": "uk",
    "holland": "netherlands", "the netherlands": "netherlands",
    "turkey": "turkey", "türkiye": "turkey", "turkiye": "turkey",
    "czechia": "czech republic",
}


def map_country(text: str) -> str | None:
    """Return a JobSpy-accepted country string, or None if it can't be mapped."""
    t = _str(text).lower()
    if not t:
        return None
    t = _COUNTRY_ALIASES.get(t, t)
    try:
        from jobspy.model import Country
        return Country(t).value.split(",")[0]
    except Exception:  # noqa: BLE001  (ValueError for unknown / jobspy missing)
        return None


# ---------------------------------------------------------------- JobSpy ----
def _scrape(scrape_jobs, *, boards, term, location, n, hours, country, log, label):
    """One scrape_jobs call -> list of normalized jobs, with a per-board count."""
    out: list[dict] = []
    try:
        df = scrape_jobs(site_name=boards, search_term=term, location=location,
                         results_wanted=n, hours_old=hours, country_indeed=country,
                         description_format="markdown", fetch_description=True, verbose=0)
    except Exception as e:  # noqa: BLE001
        log(f"JobSpy {label} failed: {e}")
        log(traceback.format_exc().splitlines()[-1])
        return out
    counts = {b: 0 for b in boards}
    if df is not None and len(df):
        for _, r in df.iterrows():
            try:
                site = _str(r.get("site"))
                out.append(_normalize(
                    title=r.get("title", ""), company=r.get("company", ""),
                    location=_str(r.get("location")) or location,
                    region=label, source=f"JobSpy/{site}",
                    url=_str(r.get("job_url")) or _str(r.get("job_url_direct")),
                    description=_str(r.get("description"))[:6000],
                    salary=_salary(r), date=_str(r.get("date_posted"))[:10]))
                counts[site] = counts.get(site, 0) + 1
            except Exception as re_:  # noqa: BLE001
                log(f"  (skipped row: {re_})")
    log(f"JobSpy @ {location}: " + ", ".join(f"{b}={c}" for b, c in counts.items())
        + ("  (a board at 0 returned nothing)" if any(c == 0 for c in counts.values()) else ""))
    return out


def from_jobspy(prefs: dict, cfg: dict, log=print) -> list[dict]:
    jobs: list[dict] = []
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log("JobSpy not installed; skipping. (pip install python-jobspy)")
        return jobs

    boards = [b for b in cfg["sources"].get("jobspy_boards", ["indeed", "glassdoor"]) if b != "bayt"]
    n = cfg.get("results_per_board", 25)
    hours = cfg.get("hours_old", 336)
    term = _search_term(prefs)
    country_text = prefs.get("target_country") or "usa"
    city = prefs.get("target_city") or ""
    country = map_country(country_text)
    if country is None:
        # Indeed and Glassdoor need a JobSpy-known country; the rest don't.
        log(f"Country '{country_text}' isn't one JobSpy knows — skipping Indeed/Glassdoor "
            "for it (try the English name, e.g. 'Germany').")
        boards = [b for b in boards if b not in ("indeed", "glassdoor")]
        country = "usa"
    primary_loc = ", ".join(filter(None, [city, prefs.get("target_country", "")])) or country_text

    log("Fetching job details too (slower: about 1s per LinkedIn job)…")
    region = prefs.get("target_country") or "Target"
    # Glassdoor rejects "City, Country" ("location not found") — give it the city alone.
    main_boards = [b for b in boards if b != "glassdoor"]
    if main_boards:
        jobs += _scrape(scrape_jobs, boards=main_boards, term=term, location=primary_loc, n=n,
                        hours=hours, country=country, log=log, label=region)
    if "glassdoor" in boards:
        jobs += _scrape(scrape_jobs, boards=["glassdoor"], term=term, location=city or country_text,
                        n=n, hours=hours, country=country, log=log, label=region)
    if prefs.get("also_remote") and "indeed" in boards:
        jobs += _scrape(scrape_jobs, boards=["indeed"], term=term, location="Remote", n=n,
                        hours=hours, country=country, log=log, label="Remote")
    return jobs


# ------------------------------------------------------------- Remotive ----
def from_remotive(prefs: dict, log=print) -> list[dict]:
    """One request per fetch. Remotive asks API users to stay under ~4 requests
    a day, and its `search`/`category` filters returned unrelated rows when
    tested, so the title filter does the work. Jobs link back to remotive.com."""
    jobs: list[dict] = []
    try:
        r = requests.get("https://remotive.com/api/remote-jobs", params={"limit": 200}, timeout=20)
        for j in r.json().get("jobs", []):
            jobs.append(_normalize(
                title=j.get("title", ""), company=j.get("company_name", ""),
                location=j.get("candidate_required_location", "Worldwide"),
                region="Remote", source="Remotive", url=j.get("url", ""),
                description=(j.get("description", "") or "")[:6000],
                salary=j.get("salary", ""), date=(j.get("publication_date", "") or "")[:10]))
        log(f"Remotive: {len(jobs)} listings (before title filter)")
    except Exception as e:  # noqa: BLE001
        log(f"Remotive failed: {e}")
    return jobs


# ------------------------------------------------------------ Arbeitnow ----
def from_arbeitnow(prefs: dict, log=print) -> list[dict]:
    """One request per fetch (the API's `search` parameter does nothing)."""
    jobs: list[dict] = []
    city = (prefs.get("target_city") or "").lower()
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api", timeout=20)
        for j in r.json().get("data", []):
            loc = j.get("location", "Germany")
            if city and city not in _str(loc).lower() and not j.get("remote"):
                continue
            jobs.append(_normalize(
                title=j.get("title", ""), company=j.get("company_name", ""),
                location=loc, region="Germany", source="Arbeitnow",
                url=j.get("url") or f"https://www.arbeitnow.com/jobs/{j.get('slug', '')}",
                description=(j.get("description", "") or "")[:6000],
                date=str(j.get("created_at", ""))[:10]))
        log(f"Arbeitnow: {len(jobs)} listings (before title filter)")
    except Exception as e:  # noqa: BLE001
        log(f"Arbeitnow failed: {e}")
    return jobs


# --------------------------------------------------------------- dedupe ----
_CO_SUFFIX = re.compile(r"\b(gmbh|ag|se|inc|llc|ltd|limited|co|corp|corporation|plc|bv|sa|kg)\b")


def _loose_key(j: dict) -> tuple:
    t = re.sub(r"[^a-z0-9]+", " ", _str(j.get("title")).lower()).strip()
    c = re.sub(r"[^a-z0-9]+", " ", _CO_SUFFIX.sub("", _str(j.get("company")).lower())).strip()
    loc = re.sub(r"[^a-z0-9]+", " ", _str(j.get("location")).lower().split(",")[0]).strip()
    return (t, c, loc)


def dedupe(jobs: list[dict]) -> list[dict]:
    """Drop duplicates by id (URL hash) and by normalized title+company+city —
    the same posting has different URLs on different boards. On a collision keep
    the row with the longer description."""
    best: dict[tuple, dict] = {}
    order: list[tuple] = []
    for j in jobs:
        key = _loose_key(j)
        if not any(key[:2]):
            key = ("id", j["id"])
        if key not in best:
            best[key] = j
            order.append(key)
        elif len(j.get("description", "")) > len(best[key].get("description", "")):
            best[key] = j
    seen_ids, out = set(), []
    for k in order:
        j = best[k]
        if j["id"] in seen_ids:
            continue
        seen_ids.add(j["id"])
        out.append(j)
    return out


# -------------------------------------------------------------- Collect ----
def collect(profile: dict, cfg: dict, log=print) -> list[dict]:
    prefs = profile.get("preferences", {})
    src = cfg.get("sources", {})
    raw: list[dict] = []
    policy.check_workflow()
    if src.get("jobspy", True):
        raw += from_jobspy(prefs, cfg, log=log)
    policy.check_workflow()
    if src.get("remotive", True):
        raw += from_remotive(prefs, log=log)
    policy.check_workflow()
    if src.get("arbeitnow", True) and map_country(prefs.get("target_country", "")) == "germany":
        raw += from_arbeitnow(prefs, log=log)

    policy.check_workflow()
    kept = [j for j in raw if filters.passes(j, prefs)]
    out = [filters.annotate(j) for j in dedupe(kept)]
    log(f"Collected {len(out)} eligible unique jobs (from {len(raw)} raw).")
    return out
