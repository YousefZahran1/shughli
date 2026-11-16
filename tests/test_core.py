import json
from pathlib import Path

import pytest

from core import policy, config, cv_coach, engine, filters, profile as P, scoring, sources, tailor, textutil


# ---------------------------------------------------------------- 1.2 clean ----
def test_clean_html():
    out = sources.clean_text("<p>Hi <b>there</b> &amp; you</p><ul><li>one</li><li>two</li></ul><p>x</p>")
    assert "<" not in out and ">" not in out
    assert "Hi there & you" in out and "- one\n- two" in out


def test_clean_markdown():
    out = sources.clean_text("**Bold** and *it* __b__\n## Head\n* item\n[link](http://x.y) snake_case_name")
    assert "**" not in out and "##" not in out and "](" not in out
    assert "snake_case_name" in out and "- item" in out and "link" in out


# --------------------------------------------------- 1.3 request counts ----
class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_arbeitnow_single_request(monkeypatch):
    calls = []
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: calls.append((a, k)) or _Resp(
        {"data": [{"title": "Data Scientist", "company_name": "A", "location": "Berlin", "slug": "s",
                   "url": "https://www.arbeitnow.com/jobs/companies/a/s-1", "description": "<p>x</p>"}]}))
    jobs = sources.from_arbeitnow({"role_keywords": ["a", "b", "c", "d"], "target_city": "berlin"}, log=lambda *_: None)
    assert len(calls) == 1 and "search" not in (calls[0][1].get("params") or {})
    assert jobs[0]["url"].endswith("/s-1")


def test_remotive_single_request(monkeypatch):
    calls = []
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: calls.append(1) or _Resp({"jobs": []}))
    sources.from_remotive({"role_keywords": ["a", "b", "c", "d", "e"]}, log=lambda *_: None)
    assert len(calls) == 1


# ------------------------------------------------------------ 3.8 country ----
@pytest.mark.parametrize("text,expected", [("Germany", "germany"), ("UAE", "united arab emirates"),
                                           ("KSA", "saudi arabia"), ("Deutschland", "germany"),
                                           ("United States", "usa"), ("Atlantis", None), ("", None)])
def test_map_country(text, expected):
    assert sources.map_country(text) == expected


# -------------------------------------------------------- 3.9 / 3.10 filter ----
@pytest.mark.parametrize("title,ok", [("Data Science Consultant", True), ("Data Science Manager", True),
                                      ("Senior Machine Learning Scientist", False),
                                      ("Junior Data Scientist (m/w/d)", True),
                                      ("Analytics Engineer", False)])
def test_title_relevant(title, ok):
    assert filters.title_relevant(title, {"role_keywords": ["data scientist"]}) is ok


def test_title_relevant_stems_engineer_analyst():
    assert filters.title_relevant("Data Engineering Intern", {"role_keywords": ["data engineer"]})
    assert filters.title_relevant("Business Analytics Analyst", {"role_keywords": ["business analyst"]})


def test_level_from_description_years():
    assert filters.job_level("Data Scientist", "We need 5+ years of experience in ML.") == "senior"
    assert filters.job_level("Data Scientist", "Requires 3 years experience with Python") == "mid"
    assert filters.job_level("Data Scientist", "Mindestens 6 Jahre Erfahrung") == "senior"
    assert filters.job_level("Data Scientist", "Join our 10 years old startup") == "general"
    assert filters.job_level("Data Scientist", "") == "general"


# ----------------------------------------------------------- 1.12 profile ----
def test_normalize_profile_malformed():
    p = P.normalize_profile({
        "personal": {"name": "A", "links": "github.com/a"},
        "certifications": [{"name": "AWS"}, "X", ""],
        "languages": "English\nArabic",
        "experience": [{"title": "T", "bullets": "- a\n- b"}, {}, "junk"],
        "projects": [{"name": "P", "bullets": ["x", None, ""]}],
        "skills": [["Lang: ", "py"], {"category": "x", "items": ["a", "b"]}, "Tools: git"],
        "preferences": {"role_keywords": "data scientist, ml", "seniority": None},
    })
    assert p["personal"]["links"] == ["github.com/a"]
    assert p["certifications"] == ["AWS", "X"] and p["languages"] == ["English", "Arabic"]
    assert p["experience"][0]["bullets"] == ["a", "b"] and len(p["experience"]) == 1
    assert p["projects"][0]["bullets"] == ["x"]
    assert p["skills"][1] == {"category": "x", "items": "a, b"}
    assert p["preferences"]["role_keywords"] == ["data scientist", "ml"]
    P.as_cv_text(p)   # must not raise


def test_save_load_roundtrip_and_corrupt(tmp_data):
    P.save_profile({"personal": {"name": "Zed"}, "nationality": "X"})
    assert P.load_profile()["personal"]["name"] == "Zed"
    config.PROFILE_PATH.write_text("{ truncated", encoding="utf-8")
    assert P.load_profile()["personal"]["name"] == ""
    assert config.PROFILE_PATH.with_name("profile.json.corrupt").exists()


def test_backup_profile(tmp_data):
    P.save_profile({"personal": {"name": "Zed"}})
    P.backup_profile()
    assert json.loads((tmp_data / "profile.prev.json").read_text(encoding="utf-8"))["personal"]["name"] == "Zed"


# ------------------------------------------------------ 1.11 / 3.11 tailor ----
PROFILE = P.normalize_profile({
    "personal": {"name": "Sam Test", "email": "s@example.com"},
    "nationality": "X", "summary": "s",
    "skills": [{"category": "Programming", "items": "Python, SQL"}],
    "projects": [{"name": "Alpha RAG", "stack": "Python", "dates": "2025", "bullets": ["built it"]}],
    "experience": [{"title": "Intern", "org": "Acme", "bullets": ["did things"]}],
    "education": [{"degree": "BSc", "institution": "Uni"}],
    "work_authorization": [{"region": "Germany", "status": "student visa"}],
})
JOB = {"id": "j1", "title": "Data Scientist", "company": "Co", "location": "Berlin, Germany",
       "region": "Germany", "description": "We use Python, Kubernetes and Spark."}


def test_tailor_dict_shaped_skills_and_foreign_project(stub_generate, tmp_data):
    stub_generate.reply = json.dumps({
        "summary": "A — summary", "cover_letter": "Dear Team,\n\nOne.\n\nTwo.\n\nThree.\n\nSincerely,\nSam",
        "skills": [{"category": "Programming", "items": ["Python", "Kubernetes"]}, "Misc: SQL"],
        "projects": [{"header": "Alpha RAG | Python", "date": "1999", "bullets": "- x\n- y"},
                     {"header": "Invented Thing", "bullets": ["z"]}],
        "fit": {"matches": ["m"], "gaps": "oops"}})
    gen = tailor.generate(JOB, PROFILE)
    assert gen["skills"][0] == ["Programming: ", "Python"]          # Kubernetes: in posting, not in CV -> dropped
    assert gen["skills"][1] == ["Misc: ", "SQL"]
    assert [p["header"] for p in gen["projects"]] == ["Alpha RAG | Python"]
    assert gen["projects"][0]["date"] == "2025" and gen["projects"][0]["bullets"] == ["x", "y"]
    assert gen["summary"] == "A, summary" and gen["fit"]["gaps"] == []
    assert gen["cover_letter"] == "One.\n\nTwo.\n\nThree."
    out = tailor.build_package(JOB, PROFILE)
    from docx import Document
    text = "\n".join(p.text for p in Document(out["cv_docx"]).paragraphs)
    assert "{" not in text and "[" not in text
    letter = [p.text for p in Document(out["cover_docx"]).paragraphs]
    assert "Dear Hiring Team," in letter and "Sincerely," in letter and letter[-1] == "Sam Test"


def test_package_cached_until_force(stub_generate, tmp_data):
    stub_generate.reply = json.dumps({"summary": "s", "skills": [["A: ", "b"]], "projects": [],
                                      "cover_letter": "x", "fit": {}})
    tailor.build_package(JOB, PROFILE)
    tailor.build_package(JOB, PROFILE)
    assert len(stub_generate.calls) == 1
    tailor.build_package(JOB, PROFILE, force=True)
    assert len(stub_generate.calls) == 2
    changed = P.normalize_profile({**PROFILE, "summary": "different"})
    tailor.build_package(JOB, changed)
    assert len(stub_generate.calls) == 3
    assert tailor.package_folder(JOB).name.endswith("-j1")


def test_clean_prose():
    assert tailor.clean_prose("It’s “great” — really **bold**  text – ok") == 'It\'s "great", really bold text - ok'


def test_strip_letter_frame():
    f = tailor.strip_letter_frame
    assert f("Dear X,\n\nA.\n\nB.\nBest regards,\nSam") == "A.\n\nB."
    long_thanks = "Thanks for reading all of this and considering me for the role, I look forward to talking."
    assert f("A.\n\n" + long_thanks).endswith(long_thanks)


# ------------------------------------------------------------ 2.5 excerpt ----
def test_excerpt_picks_requirements():
    desc = ("About us\nWe are a company with a long intro " + "blah " * 80 + "\n\nWhat you will do\n- build things\n\n"
            "Requirements\n- Python\n- 3 years of experience\n- Fluent German\n\nBenefits\n- free coffee")
    ex = textutil.excerpt(desc, 300)
    assert "Fluent German" in ex and "3 years of experience" in ex and "free coffee" not in ex
    assert textutil.excerpt("short text only", 900) == "short text only"
    assert textutil.excerpt("", 900) == ""


# ------------------------------------------- 2.3 / 2.4 / 1.10 scoring flow ----
def _job(i, desc="python"):
    return {"id": f"id{i}", "title": "Data Scientist", "company": f"C{i}", "location": "Berlin",
            "description": desc}


SCORE_PROFILE = P.normalize_profile({"personal": {"name": "n"}, "skills": [{"category": "x", "items": "Python, SQL"}],
                                     "preferences": {"role_keywords": ["data scientist"]}})


def test_second_fetch_reuses_scores(stub_generate):
    jobs = [_job(i) for i in range(3)]
    stub_generate.reply = lambda prompt: json.dumps(
        [{"id": f"id{i}", "score": 60 + i, "reason": "r"} for i in range(3)])
    cfg = {"max_ai_scored": 40}
    first = scoring.run([dict(j) for j in jobs], SCORE_PROFILE, [], cfg, log=lambda *_: None)
    assert len(stub_generate.calls) == 1 and first[0]["score"] == 62
    second = scoring.run([dict(j) for j in jobs], SCORE_PROFILE, first, cfg, log=lambda *_: None)
    assert len(stub_generate.calls) == 1 and second[0]["score"] == 62
    assert all(j["score_key"] == scoring.score_key(SCORE_PROFILE) for j in second)
    # changing the CV invalidates the cache
    other = P.normalize_profile({**SCORE_PROFILE, "summary": "new summary"})
    scoring.run([dict(j) for j in jobs], other, first, cfg, log=lambda *_: None)
    assert len(stub_generate.calls) == 2


def test_cap_and_unscored_are_null(stub_generate):
    jobs = [_job(i, desc="python sql " * (5 - i if i < 5 else 1)) for i in range(5)]
    stub_generate.reply = lambda prompt: json.dumps(
        [{"id": j["id"], "score": 50, "reason": "r"} for j in jobs])
    out = scoring.run(jobs, SCORE_PROFILE, [], {"max_ai_scored": 2}, log=lambda *_: None)
    assert [j["score"] for j in out].count(None) == 3
    assert all(j["score"] is not None for j in out[:2])           # scored first
    assert all(j["score_reason"] == "not scored: low keyword overlap" for j in out[2:])
    assert all(c["task"] == "score" and policy.tier_for(c["task"]) == "fast" for c in stub_generate.calls)


def test_failed_batch_leaves_null_not_zero(stub_generate):
    def boom(prompt):
        raise engine.EngineError("down")
    stub_generate.reply = boom
    out = scoring.run([_job(1)], SCORE_PROFILE, [], {"max_ai_scored": 40}, log=lambda *_: None)
    assert out[0]["score"] is None and out[0]["score_reason"] == "not scored"


def test_scoring_prompt_has_rubric_and_json_braces():
    pr = scoring._prompt("CV TEXT", [], [_job(1)], ["junior"])
    assert "85-100" in pr and '[{"id":"<id>","score":<int>' in pr and "CV TEXT" in pr


# ---------------------------------------------------------- 3.11 improve ----
def test_improve_matches_by_key_not_position(stub_generate):
    prof = P.normalize_profile({
        "summary": "old",
        "experience": [{"title": "Intern", "org": "Acme", "dates": "2024", "bullets": ["a"]},
                       {"title": "Dev", "org": "Beta", "dates": "2025", "bullets": ["b"]},
                       {"title": "Solo", "org": "Gamma", "bullets": ["keep"]}],
        "projects": [{"name": "Alpha", "stack": "py", "dates": "2023", "bullets": ["p"]}]})
    stub_generate.reply = json.dumps({
        "summary": "new",
        "experience": [{"title": "Dev", "org": "Beta", "dates": "1999", "bullets": ["b2"]},         # reordered + tampered dates
                       {"title": "Intern", "org": "Acme", "bullets": ["a2"]},
                       {"title": "Ghost", "org": "Nowhere", "bullets": ["x"]}],
        "projects": [{"name": "Alpha", "stack": "py, sql", "dates": "1", "bullets": ["p2"]}]})
    out = cv_coach.improve(prof, [{"question": "q", "answer": "a"}])
    assert out["summary"] == "new"
    by = {e["org"]: e for e in out["experience"]}
    assert by["Beta"]["bullets"] == ["b2"] and by["Beta"]["title"] == "Dev" and by["Beta"]["dates"] == "2025"
    assert by["Acme"]["bullets"] == ["a2"] and by["Gamma"]["bullets"] == ["keep"] and "Nowhere" not in by
    assert out["projects"][0]["dates"] == "2023" and out["projects"][0]["stack"] == "py, sql"


def test_review_cached_by_profile_hash(stub_generate):
    stub_generate.reply = json.dumps({"score": 50, "verdict": "v", "issues": [], "missing": [], "questions": []})
    prof = P.normalize_profile({"personal": {"name": "n"}})
    cv_coach.review(prof)
    cv_coach.review(prof)
    assert len(stub_generate.calls) == 1
    cv_coach.review(P.normalize_profile({"personal": {"name": "other"}}))
    assert len(stub_generate.calls) == 2


# ------------------------------------------------------------ engine ----
def test_generate_json_one_retry_total(monkeypatch):
    replies = iter(["not json at all", '{"ok": 1}'])
    seen = []
    monkeypatch.setattr(engine, "resolve_backend", lambda cfg=None: "stub")
    monkeypatch.setitem(engine._RUNNERS, "stub", lambda prompt, timeout, cfg, task, budget:
                        seen.append(prompt) or engine.EngineResult(text=next(replies), via="stub"))
    assert engine.generate_json("give json", cfg={}) == {"ok": 1}
    assert len(seen) == 2 and "ONLY the JSON" in seen[1]


def test_generate_json_gives_up_after_one_retry(monkeypatch):
    n = []
    monkeypatch.setattr(engine, "resolve_backend", lambda cfg=None: "stub")
    monkeypatch.setitem(engine._RUNNERS, "stub", lambda prompt, timeout, cfg, task, budget:
                        n.append(1) or engine.EngineResult(text="nope", via="stub"))
    with pytest.raises(engine.EngineError):
        engine.generate_json("x", cfg={})
    assert len(n) == 2


def test_api_retry_shares_budget(monkeypatch):
    import requests

    class R:
        def __init__(self, code):
            self.status_code, self.text = code, ""

        def json(self):
            return {"content": [{"type": "text", "text": "{}"}]}
    codes = iter([429, 200])
    monkeypatch.setattr(requests, "post", lambda *a, **k: R(next(codes)))
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    budget = {"retry": 1}
    r = engine._run_anthropic("p", 5, {"anthropic_api_key": "k"}, "main", budget)
    assert r.text == "{}" and budget["retry"] == 0


def test_claude_command_lean_and_model_tiers():
    cfg = config.DEFAULT_CONFIG
    fast = engine._claude_cmd("claude", cfg, "fast")
    main = engine._claude_cmd("claude", cfg, "main")
    assert "--strict-mcp-config" in fast and "--safe-mode" in fast and "--bare" not in fast
    assert fast[fast.index("--model") + 1] == "haiku" and "--model" not in main
    old = engine._claude_cmd("claude", cfg, "main")
    assert "--tools" in old and "--safe-mode" in old  # isolation never downgraded


def test_model_for_defaults():
    assert config.DEFAULT_CONFIG["anthropic_model"] == "claude-sonnet-5-5"
    assert engine._model_for(config.DEFAULT_CONFIG, "anthropic_model", "fast") == "claude-haiku-5-5"
    assert engine._model_for(config.DEFAULT_CONFIG, "openai_model", "fast") == "gpt-5"   # empty fast -> main


def test_auth_status_cached(monkeypatch):
    n = []
    monkeypatch.setattr(engine, "_auth_status_uncached", lambda p: n.append(p) or {"authenticated": True})
    engine._AUTH_CACHE.clear()
    engine.auth_status("claude"); engine.auth_status("claude")
    assert len(n) == 1
    engine.auth_status("claude", fresh=True)
    assert len(n) == 2


# ------------------------------------------------------------ app routes ----
@pytest.fixture
def client(tmp_data):
    import app as appmod
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod


def test_config_whitelist_and_no_secrets(client):
    c, _ = client
    config.save_config({**config.DEFAULT_CONFIG, "anthropic_api_key": "sk-secret"})
    r = c.post("/api/config", json={"results_per_board": 30, "evil_key": 1, "backend": "auto"})
    body = r.get_json()
    assert r.status_code == 200 and "evil_key" not in body["config"] and "anthropic_api_key" not in body["config"]
    assert "evil_key" not in config.load_config() and config.load_config()["anthropic_api_key"] == "sk-secret"
    assert c.post("/api/config", json={"results_per_board": 3}).status_code == 400
    assert c.post("/api/config", json={"hours_old": 99999}).status_code == 400
    assert c.post("/api/config", json={"backend": "nope"}).status_code == 400


def test_download_path_prefix_bypass(client, tmp_data):
    c, _ = client
    ok = config.APPS_DIR / "a.txt"; ok.write_text("x")
    evil = config.DATA_DIR / "applications-evil.txt"; evil.write_text("secret")
    assert c.get("/api/download", query_string={"path": str(ok)}).status_code == 200
    assert c.get("/api/download", query_string={"path": str(evil)}).status_code == 403
    assert c.get("/api/download", query_string={"path": str(config.APPS_DIR)}).status_code == 403


def test_cross_origin_post_refused(client):
    c, _ = client
    r = c.post("/api/config", json={"hours_old": 100}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/config", json={"hours_old": 100}, headers={"Origin": "http://localhost"})
    assert r.status_code == 200
    assert c.post("/api/config", json={"hours_old": 100}).status_code == 200      # no Origin header


def test_fetch_flag_set_before_thread(client, monkeypatch):
    c, appmod = client
    P.save_profile({"personal": {"name": "n"}, "nationality": "x",
                    "preferences": {"target_country": "Germany", "role_keywords": ["data scientist"]}})
    started = []
    monkeypatch.setattr(appmod.threading.Thread, "start", lambda self: started.append(1))
    appmod._fetch.update(running=False)
    assert c.post("/api/fetch", json={"score": False}).status_code == 200
    assert appmod._fetch["running"] is True
    assert c.post("/api/fetch", json={"score": False}).status_code == 409
    appmod._fetch.update(running=False)


def test_reimport_keeps_nationality_not_personal(client, stub_generate):
    c, _ = client
    P.save_profile({"personal": {"name": "Old Name", "email": "old@x.com"}, "nationality": "Egyptian",
                    "preferences": {"target_country": "Germany", "role_keywords": ["ds"]}})
    stub_generate.reply = json.dumps({"personal": {"name": "New Person", "email": "new@x.com"},
                                      "nationality": "", "summary": "s"})
    import io
    r = c.post("/api/profile/import", data={"cv": (io.BytesIO(b"some cv text"), "cv.txt")},
               content_type="multipart/form-data")
    prof = r.get_json()["profile"]
    assert prof["nationality"] == "Egyptian" and prof["personal"]["name"] == "New Person"
    assert prof["personal"]["email"] == "new@x.com" and prof["preferences"]["target_country"] == "Germany"
    assert (config.DATA_DIR / "profile.prev.json").exists()
    assert all(call["task"] == "import" and policy.tier_for(call["task"]) == "fast" for call in stub_generate.calls)


def test_tailor_route_uses_saved_unless_force(client, stub_generate):
    c, appmod = client
    P.save_profile({"personal": {"name": "Sam"}, "nationality": "x"})
    appmod._save_cache([dict(JOB)])
    stub_generate.reply = json.dumps({"summary": "s", "skills": [], "projects": [], "cover_letter": "x", "fit": {}})
    assert c.post("/api/tailor", json={"id": "j1"}).get_json()["ok"]
    assert c.post("/api/tailor", json={"id": "j1"}).get_json()["ok"]
    assert len(stub_generate.calls) == 1
    assert c.post("/api/tailor", json={"id": "j1", "force": True}).get_json()["ok"]
    assert len(stub_generate.calls) == 2


def test_atomic_write_leaves_no_temp(tmp_data):
    config.atomic_write_json(tmp_data / "x.json", {"a": 1})
    assert [p.name for p in tmp_data.iterdir() if p.name.endswith(".tmp")] == []
    assert json.loads((tmp_data / "x.json").read_text(encoding="utf-8")) == {"a": 1}
