"""Search limits stop scoring but never discard work; only a user cancel does."""
import json
import threading
import time

from core import config, engine, policy, scoring, profile as P

PROFILE = P.normalize_profile({})


def _jobs(n):
    return [{"id": f"j{i}", "title": "Data Scientist", "company": f"C{i}", "location": "Berlin",
             "description": "python"} for i in range(n)]


def _score_all(prompt):
    rows = json.loads(prompt[prompt.rindex("Jobs:\n") + 6:])
    return json.dumps([{"id": r["id"], "score": 60, "reason": "ok"} for r in rows])


def _start(appmod, flow):
    appmod._fetch.update(running=True, workflow=flow, error=None, log=[], count=0)
    thread = threading.Thread(target=appmod._run_fetch, args=(True,))
    thread.start()
    return thread


def _limit_lines(appmod, word):
    return [line for line in appmod._fetch["log"] if word in line.lower()]


# ------------------------------------------------------------ scoring.py ----
def test_batch_budget_keeps_returned_scores(monkeypatch, stub_generate):
    monkeypatch.setattr(policy, "SEARCH_MAX_BATCHES", 1)
    stub_generate.reply = _score_all
    flow = policy.Workflow()
    with policy.workflow_scope(flow):
        scores = scoring.score_jobs(_jobs(19), PROFILE, log=lambda *_: None)
    assert len(scores) == 18 and len(stub_generate.calls) == 1
    assert flow.limit_hit == "batch budget"


def test_deadline_mid_scoring_keeps_scores_and_marks_the_rest(stub_generate):
    flow = policy.Workflow()

    def reply(prompt):
        if len(stub_generate.calls) == 2:      # the budget runs out while batch 2 is in flight
            flow.deadline = time.monotonic() - 1
        return _score_all(prompt)

    stub_generate.reply = reply
    with policy.workflow_scope(flow):
        out = scoring.run(_jobs(40), PROFILE, [], {"max_ai_scored": 40}, log=lambda *_: None)
    assert len(out) == 40 and len(stub_generate.calls) == 2
    assert sum(j["score"] == 60 for j in out) == 36
    assert [j["score_reason"] for j in out if j["score"] is None] == ["not scored"] * 4
    assert flow.limit_hit == "deadline"


# ---------------------------------------------------------------- app.py ----
def test_deadline_during_blocked_batch_saves_partial_results(client, monkeypatch, stub_generate):
    _, appmod = client
    monkeypatch.setattr(appmod.sources, "collect", lambda *a, **kw: _jobs(40))
    entered, release = threading.Event(), threading.Event()

    def reply(prompt):
        if len(stub_generate.calls) > 1:       # batch 2 hangs inside the provider call
            entered.set()
            release.wait(5)
        return _score_all(prompt)

    stub_generate.reply = reply
    flow = policy.Workflow()
    thread = _start(appmod, flow)
    try:
        assert entered.wait(2)
        flow.deadline = time.monotonic() - 1
        thread.join(1)
        assert not thread.is_alive()
        assert appmod._fetch["error"] is None
        saved = config.read_json(config.JOBS_CACHE, [])
        assert len(saved) == 40
        assert sum(j["score"] == 60 for j in saved) == 18
        assert sum(j["score"] is None and j["score_reason"] == "not scored" for j in saved) == 22
        lines = _limit_lines(appmod, "deadline")
        assert len(lines) == 1 and "22" in lines[0]
        log_before = list(appmod._fetch["log"])
    finally:
        release.set()
        thread.join(2)
    time.sleep(.3)   # the abandoned batch returns now; it must not reach the cache or the log
    assert sum(j["score"] == 60 for j in config.read_json(config.JOBS_CACHE, [])) == 18
    assert appmod._fetch["log"] == log_before


def test_deadline_spent_by_the_fetch_still_saves_every_job(client, monkeypatch, stub_generate):
    _, appmod = client
    jobs = _jobs(3)
    key = scoring.score_key(PROFILE, config.load_config())
    config.atomic_write_json(config.JOBS_CACHE, [{**jobs[0], "score": 77, "score_reason": "kept", "score_key": key}])
    monkeypatch.setattr(appmod.sources, "collect", lambda *a, **kw: [dict(j) for j in jobs])
    flow = policy.Workflow()

    def backend(cfg=None):                     # first asked right after the fetch returns
        flow.deadline = time.monotonic() - 1   # the fetch itself used up the whole budget
        return "stub"

    monkeypatch.setattr(engine, "resolve_backend", backend)
    thread = _start(appmod, flow)
    thread.join(2)
    assert not thread.is_alive()
    assert appmod._fetch["error"] is None and not stub_generate.calls
    saved = {j["id"]: j for j in config.read_json(config.JOBS_CACHE, [])}
    assert set(saved) == {"j0", "j1", "j2"}
    assert saved["j0"]["score"] == 77          # a score already held is not thrown away either
    assert saved["j1"]["score"] is None and saved["j1"]["score_reason"] == "not scored"
    lines = _limit_lines(appmod, "deadline")
    assert len(lines) == 1 and "2 of 3" in lines[0]


def test_cancel_during_scoring_still_discards(client, monkeypatch, stub_generate):
    _, appmod = client
    monkeypatch.setattr(appmod.sources, "collect", lambda *a, **kw: _jobs(40))
    entered, release = threading.Event(), threading.Event()

    def reply(prompt):
        if len(stub_generate.calls) > 1:
            entered.set()
            release.wait(5)
        return _score_all(prompt)

    stub_generate.reply = reply
    flow = policy.Workflow()
    thread = _start(appmod, flow)
    try:
        assert entered.wait(2)
        flow.cancel.set()
        thread.join(1)
        assert not thread.is_alive()
        assert "cancel" in appmod._fetch["error"].lower()
        assert not config.JOBS_CACHE.exists()
    finally:
        release.set()
        thread.join(2)
