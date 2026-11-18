import inspect
import json
import subprocess
from core import engine, policy

def test_no_lean_parameter():
    assert 'lean' not in inspect.signature(engine._claude_cmd).parameters


def test_api_system_instruction_once(monkeypatch):
    import requests
    sent=[]
    class Response:
        status_code=200
        def json(self): return {'choices':[{'message':{'content':'{}'}}]}
    monkeypatch.setattr(requests,'post',lambda *a,**kw:sent.append(kw['json']) or Response())
    engine.generate_json('task-data',cfg={'backend':'openai-api','openai_api_key':'fake'},task='review')
    payload=json.dumps(sent[0])
    assert payload.count(policy.SYSTEM)==1
    assert sent[0]['messages'][1]['content']=='task-data'


def test_cli_system_instruction_once_and_no_overridden_cwd(monkeypatch):
    sent=[]
    monkeypatch.setattr(engine,'_which',lambda _:'claude')
    def call(cmd,**kw):
        sent.append((cmd,kw))
        return subprocess.CompletedProcess([],0,'{"result":"{}"}','')
    monkeypatch.setattr(engine,'_generation_process',call)
    engine.generate_json('task-data',cfg={'backend':'claude-cli'},task='review')
    cmd,kwargs=sent[0]
    assert 'cwd' not in kwargs
    assert (' '.join(cmd)+' '+kwargs['input']).count(policy.SYSTEM)==1
    assert kwargs['input']=='task-data'
