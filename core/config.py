"""Paths and app configuration for Job Hunt Cockpit.

Nothing here is personal — all candidate data lives in data/profile.json, which
is gitignored. config.json (also gitignored) holds the user's runtime choices;
config.example.json is the committed template.

Set COCKPIT_DATA_DIR to run against a scratch directory (config.json then also
lives inside it, so a scratch run never touches the real files).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_override = os.environ.get("COCKPIT_DATA_DIR")
DATA_DIR = Path(_override).resolve() if _override else ROOT / "data"
APPS_DIR = DATA_DIR / "applications"
UPLOADS_DIR = DATA_DIR / "uploads"
PROFILE_PATH = DATA_DIR / "profile.json"
JOBS_CACHE = DATA_DIR / "jobs_cache.json"
CV_REVIEW_PATH = DATA_DIR / "cv_review.json"
CONFIG_PATH = (DATA_DIR / "config.json") if _override else ROOT / "config.json"
CONFIG_EXAMPLE = ROOT / "config.example.json"

for _d in (DATA_DIR, APPS_DIR, UPLOADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    # Which AI backend to use: "auto" | "claude-cli" | "codex-cli" | "anthropic-api" | "openai-api" | "custom-api"
    "backend": "auto",
    # Optional model overrides ("" = backend default)
    "claude_model": "",
    "codex_model": "",
    "anthropic_model": "claude-sonnet-5-5",
    "openai_model": "gpt-5",
    # Cheaper tier for scoring + CV import. "" = use the main model.
    "claude_model_fast": "haiku",
    "codex_model_fast": "",
    "anthropic_model_fast": "claude-haiku-5-5",
    "openai_model_fast": "",
    "custom_model_fast": "",
    # Custom OpenAI-compatible provider (OpenRouter, Groq, local, …)
    "custom_base_url": "",
    "custom_model": "",
    # Job sources to use
    "sources": {
        "jobspy": True,
        "jobspy_boards": ["indeed", "glassdoor", "linkedin"],
        "remotive": True,
        "arbeitnow": True,
    },
    # How many results to pull per board on a fetch
    "results_per_board": 25,
    "hours_old": 336,
    # At most this many new jobs go to the AI scorer per fetch (rest ranked locally)
    "max_ai_scored": 40,
    "app_title": "شغلي",
}


def read_json(path: Path, default=None):
    """Read JSON. A file that fails to parse is moved to <name>.corrupt (not
    silently replaced by defaults) so it can still be recovered."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        bad = path.with_name(path.name + ".corrupt")
        try:
            os.replace(path, bad)
            print(f"WARNING: {path.name} could not be parsed ({e}); moved to {bad.name}", flush=True)
        except OSError:
            print(f"WARNING: {path.name} could not be parsed ({e})", flush=True)
        return default


def atomic_write_json(path: Path, data) -> None:
    """Write via a temp file in the same directory + os.replace, so a crash
    mid-write can never leave a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    user = read_json(CONFIG_PATH, None)
    if isinstance(user, dict):
        cfg.update(user)
        # shallow-merge nested 'sources'
        if isinstance(user.get("sources"), dict):
            merged = dict(DEFAULT_CONFIG["sources"])
            merged.update(user["sources"])
            cfg["sources"] = merged
    return cfg


def save_config(cfg: dict) -> None:
    atomic_write_json(CONFIG_PATH, cfg)
