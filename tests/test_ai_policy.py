import json
import subprocess
import pytest
from core import engine, policy, validation, tailor, scoring, cv_coach, profile as P

def test_total_deadline(monkeypatch):
    with policy.scope(1) as run:
        monkeypatch.setattr(policy.time, 'monotonic', lambda: run.deadline + 1)
        with pytest.raises(policy.PolicyError):
            run.remaining()

def test_attempt_budget():
    with policy.scope() as run:
        run.attempt(); run.attempt()
        with pytest.raises(policy.PolicyError):
            run.attempt()

def test_oversized_input():
    with pytest.raises(engine.EngineError):
        engine.generate('x' * (policy.POLICY.max_input_chars + 1), cfg={'backend':'claude-cli'})

def test_claude_fails_closed(monkeypatch):
    seen=[]
    monkeypatch.setattr(engine, '_which', lambda _: 'claude')
    def fail(*args, **kw):
        seen.append(args[0])
        return subprocess.CompletedProcess(args[0], 1, '', 'unknown option --safe-mode')
    monkeypatch.setattr(engine.subprocess, 'run', fail)
    with pytest.raises(engine.EngineError):
        engine._run_claude('p', 1, {})
    assert len(seen)==1 and '--safe-mode' in seen[0]

def test_codex_model_and_isolation(monkeypatch):
    seen=[]
    monkeypatch.setattr(engine, '_which', lambda _: 'codex')
    def run(*args, **kw):
        seen.append(args[0]); return subprocess.CompletedProcess(args[0], 0, '{}', '')
    monkeypatch.setattr(engine.subprocess, 'run', run)
    engine._run_codex('p', 1, {'codex_model':'main','codex_model_fast':'cheap'}, 'fast')
    cmd=seen[0]
    assert cmd[cmd.index('-m')+1]=='cheap'
    assert '--ignore-user-config' in cmd and 'read-only' in cmd and '--ephemeral' in cmd

def test_review_rejects_bad_score():
    with pytest.raises(engine.EngineError):
        validation.review({'score':True,'verdict':'x','issues':[],'missing':[],'questions':[]})

def test_empty_projects_cannot_be_invented():
    assert tailor._ground_projects([{'header':'Fake','date':'','bullets':[]}], {})==[]

def test_skill_absent_from_both_sources_removed():
    assert tailor._ground_skills([['Tools','Python, Magic']], 'Python', 'SQL')==[['Tools','Python']]

def test_source_checks_flag_numbers():
    assert validation.source_checks('Improved speed 90%', 'Improved speed 10%')['warnings']

def test_arabic_identity():
    assert cv_coach._nm('شركة ألف') != cv_coach._nm('شركة باء')
    assert tailor._nm('مشروع ألف') != tailor._nm('مشروع باء')

def test_cache_changes_with_model_and_auth():
    p=P.normalize_profile({})
    assert scoring.score_key(p, {'backend':'openai-api','openai_model':'a'}) != scoring.score_key(p, {'backend':'openai-api','openai_model':'b'})
    assert tailor.package_key({'description':'a'},p) != tailor.package_key({'description':'b'},p)

def test_improvement_preview_does_not_save(client, monkeypatch):
    c, appmod=client
    original=P.save_profile({'personal':{'name':'Sam'},'summary':'Old'})
    proposed=dict(original, summary='New')
    monkeypatch.setattr(cv_coach,'improve',lambda *a,**kw:proposed)
    r=c.post('/api/cv/improve',json={'qa':[{'question':'q','answer':'a'}]}).get_json()
    assert r['ok'] and r['profile']['summary']=='New'
    assert P.load_profile()['summary']=='Old'
    changed=dict(original,summary='Edited elsewhere'); P.save_profile(changed)
    assert c.post('/api/profile',json={**proposed,'_base_hash':r['base_hash']}).status_code==409

def test_trace_contains_no_cv_or_key(tmp_data, monkeypatch):
    monkeypatch.setattr(engine,'resolve_backend',lambda cfg=None:'stub')
    monkeypatch.setitem(engine._RUNNERS,'stub',lambda *a:engine.EngineResult('{}','stub'))
    engine.generate_json('private-cv',cfg={'secret_api_key':'secret-key'})
    text=(tmp_data/'ai_runs.jsonl').read_text()
    assert 'private-cv' not in text and 'secret-key' not in text
    assert json.loads(text.splitlines()[-1])['attempts']==1

def test_scoring_ignores_unknown_ids_and_caps_title_only(stub_generate):
    stub_generate.reply='[{"id":"real","score":99,"reason":"match"},{"id":"fake","score":100}]'
    result=scoring.score_jobs([{'id':'real','title':'Python','company':'Example','location':'Remote','description':''}],P.normalize_profile({}),log=lambda *_:None)
    assert set(result)=={'real'} and result['real']['score']==70

def test_nonfinite_scores():
    assert scoring._coerce_score(float('nan')) is None
    assert scoring._coerce_score(float('inf')) is None

def test_concurrency_limit():
    assert policy._gate.acquire(False) and policy._gate.acquire(False)
    try:
        with pytest.raises(policy.PolicyError):
            with policy.scope():
                pass
    finally:
        policy._gate.release(); policy._gate.release()

def test_accept_improvement_saves_and_backs_up(client, monkeypatch, tmp_data):
    c,_=client
    original=P.save_profile({'summary':'Old'})
    proposed=dict(original,summary='New')
    monkeypatch.setattr(cv_coach,'improve',lambda *a,**kw:proposed)
    preview=c.post('/api/cv/improve',json={'qa':[{'question':'q','answer':'a'}]}).get_json()
    assert c.post('/api/profile',json={**preview['profile'],'_base_hash':preview['base_hash']}).get_json()['ok']
    assert P.load_profile()['summary']=='New'
    assert json.loads((tmp_data/'profile.prev.json').read_text())['summary']=='Old'

def test_cli_model_rejects_shell_metacharacters(monkeypatch):
    monkeypatch.setattr(engine,'_which',lambda _:'codex')
    with pytest.raises(engine.EngineError):
        engine._run_codex('p',1,{'codex_model':'gpt & echo bad'})

def test_native_schema_is_sent(monkeypatch):
    import requests
    from core import config
    seen=[]
    class Response:
        status_code=200
        def json(self):
            return {'choices':[{'message':{'content':'{"score":50,"verdict":"ok","issues":[],"missing":[],"questions":[]}'}}], 'usage':{'prompt_tokens':10}}
    monkeypatch.setattr(requests,'post',lambda url,**kw:seen.append(kw['json']) or Response())
    result=engine.generate_json('Review synthetic CV',cfg={'backend':'openai-api','openai_api_key':'fake'},schema=validation.REVIEW_SCHEMA)
    assert result['score']==50
    assert seen[0]['response_format']['json_schema']['strict'] is True
    assert seen[0]['messages'][0]['role']=='system'

def test_profile_and_preview_reject_non_object_json(client):
    c,_=client
    assert c.post('/api/profile',json=['bad']).status_code==400
    assert c.post('/api/cv/improve',json=['bad']).status_code==400
