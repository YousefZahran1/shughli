import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config, engine  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.tempfile, "tempdir", str(tmp_path))
    d = tmp_path / "data"
    for name in ("applications", "uploads"):
        (d / name).mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", d)
    monkeypatch.setattr(config, "APPS_DIR", d / "applications")
    monkeypatch.setattr(config, "UPLOADS_DIR", d / "uploads")
    monkeypatch.setattr(config, "PROFILE_PATH", d / "profile.json")
    monkeypatch.setattr(config, "JOBS_CACHE", d / "jobs_cache.json")
    monkeypatch.setattr(config, "CV_REVIEW_PATH", d / "cv_review.json")
    monkeypatch.setattr(config, "CONFIG_PATH", d / "config.json")
    return d


@pytest.fixture
def stub_generate(monkeypatch):
    """Replace engine.generate. Set stub.reply (str or callable(prompt, **kw))."""
    class Stub:
        reply = "[]"
        calls = []

    s = Stub()
    s.calls = []

    def fake(prompt, timeout=300, cfg=None, task="main", **kw):
        s.calls.append({"prompt": prompt, "task": task})
        r = s.reply(prompt) if callable(s.reply) else s.reply
        return engine.EngineResult(text=r, via="stub")

    monkeypatch.setattr(engine, "generate", fake)
    monkeypatch.setattr(engine, "resolve_backend", lambda cfg=None: "stub")
    return s


@pytest.fixture
def client(tmp_data):
    import app as appmod
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), appmod
