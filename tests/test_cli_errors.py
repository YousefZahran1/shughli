import json
import subprocess
import pytest
from core import engine

# Wording observed with installed Claude 2.1.268 and Codex 0.169.0.
@pytest.mark.parametrize('backend', ['claude', 'codex'])
@pytest.mark.parametrize('code', [0, 1])
@pytest.mark.parametrize('task', ['review', 'score'])
def test_unavailable_model_names_effective_setting_and_keeps_details_private(
        monkeypatch, tmp_data, backend, code, task):
    model = 'no-such-model-xyz'
    detail = (f"There's an issue with the selected model ({model}). It may not exist or you may not have access to it. Run --model to pick a different model."
              if backend == 'claude' else
              f"The '{model}' model is not supported when using Codex with a ChatGPT account.")
    payload = ({'is_error': True, 'result': detail, 'api_error_status': 404}
               if backend == 'claude' else {'type': 'turn.failed', 'error': {'message': detail}})
    monkeypatch.setattr(engine, '_which', lambda _: backend)
    monkeypatch.setattr(engine, '_generation_process', lambda *a, **k:
                        subprocess.CompletedProcess([], code, json.dumps(payload), 'private diagnostic'))
    cfg = {backend + '_model': model if task == 'review' else 'main-model',
           backend + '_model_fast': model if task == 'score' else 'fast-model'}
    with pytest.raises(engine.EngineError) as exc:
        engine.generate_json('p', cfg={**cfg, 'backend': backend + '-cli'}, task=task)
    assert exc.value.category == 'model_unavailable'
    assert model in str(exc.value)
    assert 'Settings' in str(exc.value)
    assert detail not in str(exc.value) and 'private diagnostic' not in str(exc.value)
    debug = json.loads(next((tmp_data / 'private_debug').glob('*.json')).read_text())
    assert detail in debug['stdout'] and debug['stderr'] == 'private diagnostic'
    trace = (tmp_data / 'ai_runs.jsonl').read_text()
    assert detail not in trace and 'private diagnostic' not in trace


@pytest.mark.parametrize('backend', ['claude', 'codex'])
@pytest.mark.parametrize('detail', [
    'model_not_found: private provider detail',
    'The requested model does not exist or you do not have access to it.',
    'The selected model is not available.',
])
def test_model_errors_in_plain_stderr(monkeypatch, backend, detail):
    monkeypatch.setattr(engine, '_which', lambda _: backend)
    monkeypatch.setattr(engine, '_generation_process', lambda *a, **k:
                        subprocess.CompletedProcess([], 1, '', detail))
    with pytest.raises(engine.EngineError) as exc:
        getattr(engine, '_run_' + backend)('p', 1, {backend + '_model': 'configured-model'})
    assert exc.value.category == 'model_unavailable'
    assert 'configured-model' in str(exc.value) and 'Settings' in str(exc.value)
    assert detail not in str(exc.value)

@pytest.mark.parametrize('backend', ['claude', 'codex'])
@pytest.mark.parametrize('code', [0,1])
@pytest.mark.parametrize('detail,category,message', [
    ('OAuth authentication token expired', 'auth_expired', 'session expired'),
    ('usage limit reached 429', 'rate_limit', 'usage or rate limit'),
    ('private secret detail', 'other', 'generation failed'),
])
def test_cli_error_categories(monkeypatch,tmp_data,backend,code,detail,category,message):
    monkeypatch.setattr(engine,'_which',lambda _:backend)
    payload={'is_error':True,'result':detail} if backend=='claude' else {'type':'error','message':detail}
    monkeypatch.setattr(engine,'_generation_process',lambda *a,**k:subprocess.CompletedProcess([],code,json.dumps(payload),''))
    with pytest.raises(engine.EngineError) as exc:
        getattr(engine,'_run_'+backend)('p',1,{})
    assert message in str(exc.value).lower()
    assert detail not in str(exc.value)
    assert exc.value.category==category
    debug=list((tmp_data/'private_debug').glob('*.json'))
    assert debug and detail in debug[0].read_text()

@pytest.mark.parametrize('backend',['claude','codex'])
def test_unknown_option_only_from_stderr(monkeypatch,backend):
    monkeypatch.setattr(engine,'_which',lambda _:backend)
    monkeypatch.setattr(engine,'_generation_process',lambda *a,**k:subprocess.CompletedProcess([],1,'','unknown option --safe-mode'))
    with pytest.raises(engine.EngineError) as exc:
        getattr(engine,'_run_'+backend)('p',1,{})
    assert exc.value.category=='unsupported_flag'
    assert 'isolation' in str(exc.value)

@pytest.mark.parametrize('backend',['claude','codex'])
def test_cli_timeout_debug(monkeypatch,tmp_data,backend):
    monkeypatch.setattr(engine,'_which',lambda _:backend)
    def timeout(*a,**k):
        raise subprocess.TimeoutExpired('cli',1,output='private partial')
    monkeypatch.setattr(engine,'_generation_process',timeout)
    with pytest.raises(engine.EngineError) as exc:
        getattr(engine,'_run_'+backend)('p',1,{})
    assert exc.value.category=='timeout'
    assert 'private partial' not in str(exc.value)
    assert list((tmp_data/'private_debug').glob('*.json'))

@pytest.mark.parametrize('backend',['claude','codex'])
def test_hit_limit_wording_classified(monkeypatch,backend):
    monkeypatch.setattr(engine,'_which',lambda _:backend)
    payload={'is_error':True,'result':"You've hit your limit - resets later"}
    monkeypatch.setattr(engine,'_generation_process',lambda *a,**k:subprocess.CompletedProcess([],1,json.dumps(payload),''))
    with pytest.raises(engine.EngineError) as exc:
        getattr(engine,'_run_'+backend)('p',1,{})
    assert exc.value.category=='rate_limit'


def test_codex_reported_model_is_traced(monkeypatch,tmp_data):
    monkeypatch.setattr(engine,'_which',lambda _:'codex')
    output='\n'.join(json.dumps(e) for e in [
        {'type':'turn.started','model':'actual-codex-model'},
        {'type':'item.completed','item':{'type':'agent_message','text':'{}'}},
        {'type':'turn.completed','usage':{'input_tokens':123,'output_tokens':5}},
    ])
    monkeypatch.setattr(engine,'_generation_process',lambda *a,**k:subprocess.CompletedProcess([],0,output,''))
    engine.generate_json('p',cfg={'backend':'codex-cli'},task='review')
    rows=[json.loads(line) for line in (tmp_data/'ai_runs.jsonl').read_text().splitlines()]
    assert any(row.get('models')==['actual-codex-model'] for row in rows)
    assert any(row.get('usage',{}).get('input_tokens')==123 for row in rows)
