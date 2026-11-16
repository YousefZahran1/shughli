import json
import subprocess
import pytest
from core import engine

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
