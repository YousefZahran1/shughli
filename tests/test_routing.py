import json
import subprocess
from core import engine, policy, config, cv_import, cv_coach, scoring, tailor, profile as P

def test_task_routing():
    assert policy.TASK_TIERS=={'score':'fast','import':'fast','review':'main','improve':'main','tailor':'main'}
    cfg={'openai_model':'big','openai_model_fast':'small'}
    assert engine._model_for(cfg,'openai_model','score')=='small'
    assert engine._model_for(cfg,'openai_model','tailor')=='big'

def test_settings_effective_fallback(client):
    c,_=client
    config.save_config({'backend':'openai-api','openai_model':'chosen','openai_model_fast':''})
    routing=c.get('/api/status').get_json()['model_routing']
    assert routing['tiers']['fast']=='chosen' and routing['tiers']['main']=='chosen'
    assert routing['tasks']['score']=='chosen'
    response=c.post('/api/config',json={'openai_model_fast':'cheap'}).get_json()
    assert response['model_routing']['tiers']['fast']=='cheap'

def test_claude_actual_model_trace(monkeypatch,tmp_data):
    monkeypatch.setattr(engine,'_which',lambda _:'claude')
    payload={'result':'{}','modelUsage':{'actual-haiku-version':{'inputTokens':100}}}
    monkeypatch.setattr(engine,'_generation_process',lambda *a,**k:subprocess.CompletedProcess([],0,json.dumps(payload),''))
    engine.generate_json('p',cfg={'backend':'claude-cli','claude_model_fast':'haiku'},task='import')
    rows=[json.loads(line) for line in (tmp_data/'ai_runs.jsonl').read_text().splitlines()]
    assert any(r.get('models')==['actual-haiku-version'] for r in rows)

def test_named_task_callers(stub_generate):
    stub_generate.reply='{}'
    cv_import.structure_cv('Python developer')
    assert stub_generate.calls[-1]['task']=='import'
    stub_generate.reply='[]'
    scoring.score_jobs([{'id':'j','title':'t','company':'c','location':'l'}],P.normalize_profile({}),log=lambda *_:None)
    assert stub_generate.calls[-1]['task']=='score'
