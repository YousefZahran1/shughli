import threading
import time
from core import policy, engine, scoring, profile as P

def test_waits_for_slot():
    policy._gate.acquire(); policy._gate.acquire()
    entered=threading.Event()
    def work():
        with policy.scope(timeout=2):
            entered.set()
    thread=threading.Thread(target=work)
    thread.start()
    time.sleep(.05)
    assert not entered.is_set()
    policy._gate.release()
    thread.join(2)
    policy._gate.release()
    assert entered.is_set()

def test_interactive_wait_limit(monkeypatch):
    waits=[]
    class Gate:
        def acquire(self, **kw):
            waits.append(kw); return False
    monkeypatch.setattr(policy,'_gate',Gate())
    try:
        with policy.scope(timeout=60): pass
    except policy.PolicyError: pass
    assert 19 <= waits[0].get('timeout',0) <= 20

def test_background_waits_until_deadline(monkeypatch):
    waits=[]
    class Gate:
        def acquire(self, **kw):
            waits.append(kw); return False
    monkeypatch.setattr(policy,'_gate',Gate())
    try:
        with policy.scope(timeout=45, background=True): pass
    except policy.PolicyError: pass
    assert 44 <= waits[0]['timeout'] <= 45
