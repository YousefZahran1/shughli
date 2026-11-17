"""max_ai_scored and the per-search batch budget must describe the same ceiling."""
import json
import subprocess
from pathlib import Path

from core import config, policy, scoring, profile as P

PROFILE = P.normalize_profile({})


def _covered():
    return policy.SEARCH_MAX_BATCHES * scoring.BATCH


def _jobs(n):
    return [{"id": f"j{i}", "title": "Data Scientist", "company": f"C{i}", "location": "Berlin",
             "description": "python"} for i in range(n)]


def _score_all(prompt):
    rows = json.loads(prompt[prompt.rindex("Jobs:\n") + 6:])
    return json.dumps([{"id": r["id"], "score": 60, "reason": "ok"} for r in rows])


def test_effective_cap_is_what_the_batch_budget_covers():
    assert scoring.cap_info({"max_ai_scored": 500}) == {
        "configured": 500, "effective": _covered(), "max": _covered(),
        "batches": policy.SEARCH_MAX_BATCHES, "batch_size": scoring.BATCH}
    assert scoring.cap_info({"max_ai_scored": 40})["effective"] == 40
    assert scoring.cap_info({"max_ai_scored": "junk"})["effective"] == 40   # falls back to the default


def test_cap_keeps_scoring_inside_the_batch_budget(monkeypatch, stub_generate):
    monkeypatch.setattr(policy, "SEARCH_MAX_BATCHES", 1)
    stub_generate.reply = _score_all
    flow = policy.Workflow()
    with policy.workflow_scope(flow):
        out = scoring.run(_jobs(30), PROFILE, [], {"max_ai_scored": 500}, log=lambda *_: None)
    assert len(stub_generate.calls) == 1
    assert sum(j["score"] == 60 for j in out) == 18
    assert sum(j["score_reason"] == "not scored: low keyword overlap" for j in out) == 12
    assert flow.limit_hit == ""        # the cap stops at the budget, so the budget is never overrun


def test_saving_a_cap_above_the_budget_stores_the_covered_maximum(client):
    c, _ = client
    r = c.post("/api/config", json={"max_ai_scored": 500})
    assert r.status_code == 200          # Settings re-posts the whole config; an old 500 must not block saving
    assert r.get_json()["config"]["max_ai_scored"] == _covered()
    assert config.load_config()["max_ai_scored"] == _covered()
    assert c.post("/api/config", json={"max_ai_scored": 0}).status_code == 400


def test_status_and_config_report_the_effective_cap(client):
    c, _ = client
    config.save_config({"max_ai_scored": 500})     # a config file written under the former 500 ceiling
    cap = c.get("/api/status").get_json()["scoring_cap"]
    assert cap["configured"] == 500 and cap["effective"] == _covered() and cap["max"] == _covered()
    assert c.post("/api/config", json={"max_ai_scored": 25}).get_json()["scoring_cap"]["effective"] == 25


def _render_cap(cap):
    source = Path("static/app.js").read_text(encoding="utf-8")
    function = source[source.index("function showScoringCap"):source.index("function renderSettings")]
    script = '''const vm=require('node:vm');
const nodes={};
const context={$:id=>nodes[id]??=({textContent:''})};
vm.createContext(context);vm.runInContext(FUNCTION,context);
context.showScoringCap(CAP);process.stdout.write(JSON.stringify(nodes));
'''.replace("CAP", json.dumps(cap)).replace("FUNCTION", json.dumps(function))
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)["#effective-cap"]["textContent"]


def test_settings_shows_the_effective_cap():
    text = _render_cap({"configured": 40, "effective": 40, "max": 216, "batches": 12, "batch_size": 18})
    assert "40" in text and "216" in text and "12" in text
    capped = _render_cap({"configured": 500, "effective": 216, "max": 216, "batches": 12, "batch_size": 18})
    assert "500" in capped and "216" in capped
    assert 'id="effective-cap"' in Path("static/index.html").read_text(encoding="utf-8")
    source = Path("static/app.js").read_text(encoding="utf-8")
    assert "showScoringCap(s.scoring_cap)" in source and "showScoringCap(r.scoring_cap)" in source
