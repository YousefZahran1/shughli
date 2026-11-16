import json
import threading
import pytest
from core import policy, scoring, engine, profile as P

def test_workflow_trace_and_batch_cap(monkeypatch,tmp_data):
    monkeypatch.setattr(policy,'SEARCH_MAX_BATCHES',1)
    monkeypatch.setattr(engine,'resolve_backend',lambda cfg=None:'stub')
    monkeypatch.setitem(engine._RUNNERS,'stub',lambda *a:engine.EngineResult('[]','stub'))
    jobs=[{'id':str(i),'title':'t','company':'c','location':'l'} for i in range(19)]
    workflow=policy.Workflow()
    with policy.workflow_scope(workflow):
        with pytest.raises(policy.PolicyError,match='batch'):
            scoring.score_jobs(jobs,P.normalize_profile({}),log=lambda *_:None)
    rows=[json.loads(line) for line in (tmp_data/'ai_runs.jsonl').read_text().splitlines()]
    assert rows and all(r['workflow_id']==workflow.id for r in rows)
    assert workflow.batches==1

def test_search_deadline(monkeypatch):
    flow=policy.Workflow()
    monkeypatch.setattr(policy.time,'monotonic',lambda:flow.deadline+1)
    with pytest.raises(policy.PolicyError,match='deadline'):
        flow.check()

def test_cancel_prevents_scoring(stub_generate):
    flow=policy.Workflow(); flow.cancel.set()
    with policy.workflow_scope(flow),pytest.raises(policy.PolicyError,match='cancel'):
        scoring.score_jobs([{'id':'j','title':'t','company':'c','location':'l'}],P.normalize_profile({}),log=lambda *_:None)
    assert not stub_generate.calls

def test_cancel_endpoint(client):
    c,appmod=client
    flow=policy.Workflow()
    appmod._fetch.update(running=True,workflow=flow)
    try:
        r=c.post('/api/fetch/cancel',json={'workflow_id':flow.id})
        assert r.status_code==200 and flow.cancel.is_set()
        assert c.get('/api/fetch/log').get_json()['workflow_id']==flow.id
    finally:
        appmod._fetch.update(running=False,workflow=None)

def test_cancel_interrupts_source_collection(client,monkeypatch):
    c,appmod=client
    flow=policy.Workflow()
    entered=threading.Event(); release=threading.Event()
    def collect(*a,**kw):
        entered.set(); release.wait(2); return []
    monkeypatch.setattr(appmod.sources,'collect',collect)
    appmod._fetch.update(running=True,workflow=flow,error=None)
    thread=threading.Thread(target=appmod._run_fetch,args=(False,))
    thread.start()
    assert entered.wait(1)
    flow.cancel.set()
    thread.join(.5)
    try:
        assert not thread.is_alive()
        assert 'cancel' in appmod._fetch['error'].lower()
    finally:
        release.set(); thread.join(2)


def test_deadline_interrupts_source_collection(client,monkeypatch):
    import time
    c,appmod=client
    flow=policy.Workflow(deadline=time.monotonic()+.1)
    release=threading.Event()
    monkeypatch.setattr(appmod.sources,'collect',lambda *a,**kw:release.wait(2) or [])
    appmod._fetch.update(running=True,workflow=flow,error=None)
    thread=threading.Thread(target=appmod._run_fetch,args=(False,)); thread.start(); thread.join(.5)
    try:
        assert not thread.is_alive()
        assert 'deadline' in appmod._fetch['error'].lower()
    finally:
        release.set(); thread.join(2)
