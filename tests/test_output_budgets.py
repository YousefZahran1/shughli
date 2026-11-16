import pytest
from core import engine, policy

class Response:
    def __init__(self, data, status=200, text=''):
        self.data=data; self.status_code=status; self.text=text
    def json(self): return self.data

@pytest.mark.parametrize('backend,data,cfg',[
    ('anthropic',{'stop_reason':'max_tokens','content':[{'type':'text','text':'{'}]}, {'anthropic_api_key':'fake'}),
    ('openai',{'choices':[{'finish_reason':'length','message':{'content':'{'}}]}, {'openai_api_key':'fake'}),
    ('custom',{'choices':[{'finish_reason':'length','message':{'content':'{'}}]}, {'custom_base_url':'http://local','custom_model':'m'}),
])
def test_cutoff_never_retried(monkeypatch,backend,data,cfg):
    calls=[]
    import requests
    monkeypatch.setattr(requests,'post',lambda *a,**kw:calls.append(kw) or Response(data))
    with pytest.raises(engine.EngineError,match='reply was cut off'):
        engine.generate_json('p',cfg={**cfg,'backend':backend+'-api'})
    assert len(calls)==1

def test_task_output_budgets():
    assert policy.output_budget('score') < policy.output_budget('tailor')
    assert policy.output_budget('score') < policy.output_budget('import')
    assert policy.output_budget('score') < policy.output_budget('improve')
    assert policy.output_budget('tailor','gpt-5') > policy.output_budget('tailor','ordinary-model')

def test_custom_token_parameter_fallback_uses_retry(monkeypatch):
    import requests
    calls=[]
    def post(*a,**kw):
        calls.append(dict(kw['json']))
        return Response({},400,'unsupported parameter: max_completion_tokens') if len(calls)==1 else Response({'choices':[{'message':{'content':'not json'}}]})
    monkeypatch.setattr(requests,'post',post)
    with pytest.raises(engine.EngineError,match='valid JSON'):
        engine.generate_json('p',cfg={'backend':'custom-api','custom_base_url':'http://local','custom_model':'m'})
    assert len(calls)==2
    assert 'max_completion_tokens' in calls[0]
    assert 'max_tokens' in calls[1] and 'max_completion_tokens' not in calls[1]
