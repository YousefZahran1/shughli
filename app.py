# شغلي (Job Hunt Cockpit) — built by jo builds
"""Job Hunt Cockpit — local dashboard server.

Run:  python app.py   ->  http://127.0.0.1:5000

Upload a CV, confirm your profile (country, city, visa/work-authorization,
preferences), then fetch + tailor — all on your own Claude or Codex
subscription (no API key needed) or an API key if you prefer.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from core import (config, cv_coach, cv_import, engine, profile as profile_mod,
                  scoring, sources, tailor)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

app = Flask(__name__, static_folder="static", static_url_path="")

_fetch = {"running": False, "log": [], "count": 0, "error": None}
_lock = threading.Lock()


def _log(msg: str) -> None:
    with _lock:
        _fetch["log"].append(f"{time.strftime('%H:%M:%S')}  {msg}")
    print(msg, flush=True)


def _load_cache() -> list:
    data = config.read_json(config.JOBS_CACHE, [])
    return data if isinstance(data, list) else []


def _save_cache(jobs: list) -> None:
    config.atomic_write_json(config.JOBS_CACHE, jobs)


@app.before_request
def _reject_cross_origin_posts():
    """Any web page can POST to 127.0.0.1; refuse requests whose Origin is not this server."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"ok": False, "msg": "Cross-origin request refused."}), 403


def _run_fetch(do_score: bool) -> None:
    # `running` is set by start_fetch under the lock, before this thread starts.
    try:
        cfg, prof = config.load_config(), profile_mod.load_profile()
        old_jobs = _load_cache()
        _log("Fetching jobs…")
        jobs = sources.collect(prof, cfg, log=_log)
        if do_score and jobs:
            if engine.resolve_backend(cfg) == "none":
                _log("⚠ No AI backend — skipping scoring (jobs still listed).")
                jobs = scoring.apply_scores(jobs, {})
            else:
                _log(f"Scoring via {engine.resolve_backend(cfg)}…")
                jobs = scoring.run(jobs, prof, old_jobs, cfg, log=_log)
        else:
            jobs = scoring.apply_scores(jobs, {})
        _save_cache(jobs)
        with _lock:
            _fetch["count"] = len(jobs)
        _log(f"Done. {len(jobs)} jobs cached.")
    except Exception as e:  # noqa: BLE001
        _log(f"ERROR: {e}")
        with _lock:
            _fetch["error"] = str(e)
    finally:
        with _lock:
            _fetch["running"] = False


# ---------------------------------------------------------------- routes ----
@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/status")
def status():
    cfg = config.load_config()
    prof = profile_mod.load_profile()
    safe_cfg = {k: v for k, v in cfg.items() if not k.endswith("_api_key")}
    return jsonify({
        "backends": engine.available_backends(cfg),
        "active_backend": engine.resolve_backend(cfg),
        "keys": {"anthropic": bool(cfg.get("anthropic_api_key")),
                 "openai": bool(cfg.get("openai_api_key"))},
        "profile_ready": profile_mod.is_ready(prof),
        "missing_required": profile_mod.missing_required(prof),
        "profile_imported": prof.get("meta", {}).get("imported", False),
        "cached_jobs": len(_load_cache()),
        "config": safe_cfg,
        "app_title": cfg.get("app_title", "شغلي"),
        "fetch": {k: _fetch[k] for k in ("running", "count", "error")},
    })


@app.get("/api/auth/status")
def auth_status():
    cfg = config.load_config()
    fresh = bool(request.args.get("fresh"))     # set by the UI while a login is being polled
    return jsonify({
        "claude": engine.auth_status("claude", fresh=fresh),
        "codex": engine.auth_status("codex", fresh=fresh),
        "anthropic_key_set": bool(cfg.get("anthropic_api_key")),
        "openai_key_set": bool(cfg.get("openai_api_key")),
        "custom_key_set": bool(cfg.get("custom_base_url") and cfg.get("custom_model")),
        "custom_base_url": cfg.get("custom_base_url", ""),
        "custom_model": cfg.get("custom_model", ""),
    })


@app.post("/api/auth/<provider>/login")
def auth_login(provider):
    try:
        return jsonify(engine.install_and_login(provider))
    except engine.EngineError as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.get("/api/auth/<provider>/install")
def auth_install_status(provider):
    return jsonify(engine.install_status(provider))


@app.post("/api/keys")
def save_keys():
    body = request.get_json(silent=True) or {}
    cfg = config.load_config()
    # secret keys: empty string clears them
    for field in ("anthropic_api_key", "openai_api_key", "custom_api_key"):
        if field in body:
            val = (body[field] or "").strip()
            if val:
                cfg[field] = val
            else:
                cfg.pop(field, None)
    # non-secret custom settings
    for field in ("custom_base_url", "custom_model"):
        if field in body:
            cfg[field] = (body[field] or "").strip()
    config.save_config(cfg)
    return jsonify({"ok": True})


@app.post("/api/cv/review")
def cv_review():
    try:
        return jsonify({"ok": True, "review": cv_coach.review(profile_mod.load_profile())})
    except engine.EngineError as e:
        return jsonify({"ok": False, "msg": str(e)}), 502


@app.post("/api/cv/improve")
def cv_improve():
    qa = (request.get_json(silent=True) or {}).get("qa", [])
    try:
        improved = cv_coach.improve(profile_mod.load_profile(), qa)
        profile_mod.backup_profile()
        saved = profile_mod.save_profile(improved)
        return jsonify({"ok": True, "profile": saved})
    except engine.EngineError as e:
        return jsonify({"ok": False, "msg": str(e)}), 502


@app.get("/api/profile")
def get_profile():
    return jsonify(profile_mod.load_profile())


@app.post("/api/profile")
def save_profile():
    body = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "profile": profile_mod.save_profile(body)})


@app.post("/api/profile/import")
def import_profile():
    if "cv" not in request.files:
        return jsonify({"ok": False, "msg": "No file uploaded."}), 400
    f = request.files["cv"]
    name = secure_filename(f.filename or "cv")
    if not name:
        return jsonify({"ok": False, "msg": "Bad filename."}), 400
    dest = config.UPLOADS_DIR / name
    f.save(str(dest))
    try:
        prof = cv_import.import_cv_file(dest)
        # Preserve any preferences/work-auth the user already set.
        existing = profile_mod.load_profile()
        prof["preferences"] = existing.get("preferences", prof["preferences"])
        prof["work_authorization"] = existing.get("work_authorization", [])
        # CVs rarely state nationality (a required field): do not blank it on re-import.
        # Personal details are NOT carried over; the CV may belong to someone else.
        if not prof.get("nationality"):
            prof["nationality"] = existing.get("nationality", "")
        profile_mod.backup_profile()
        saved = profile_mod.save_profile(prof)
        return jsonify({"ok": True, "profile": saved})
    except engine.EngineError as e:
        return jsonify({"ok": False, "msg": str(e)}), 502
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "msg": f"Could not import CV: {e}"}), 500


_BACKENDS = {"auto", "claude-cli", "codex-cli", "anthropic-api", "openai-api", "custom-api"}
_STR_KEYS = ("claude_model", "codex_model", "anthropic_model", "openai_model", "claude_model_fast",
             "codex_model_fast", "anthropic_model_fast", "openai_model_fast", "custom_model_fast",
             "custom_base_url", "custom_model", "app_title")
_BOARDS = {"indeed", "glassdoor", "linkedin", "zip_recruiter", "google"}


def _public_cfg(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if not k.endswith("_api_key")}


def _int_in(body: dict, key: str, lo: int, hi: int) -> int:
    try:
        v = int(body[key])
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number.")
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}.")
    return v


def _validate_config(body: dict) -> dict:
    """Only keys the Settings tab / model settings use; types and ranges checked."""
    out: dict = {}
    if "backend" in body:
        if body["backend"] not in _BACKENDS:
            raise ValueError("Unknown backend.")
        out["backend"] = body["backend"]
    if "results_per_board" in body:
        out["results_per_board"] = _int_in(body, "results_per_board", 5, 100)
    if "hours_old" in body:
        out["hours_old"] = _int_in(body, "hours_old", 24, 2160)
    if "max_ai_scored" in body:
        out["max_ai_scored"] = _int_in(body, "max_ai_scored", 1, 500)
    for k in _STR_KEYS:
        if k in body:
            if not isinstance(body[k], str):
                raise ValueError(f"{k} must be text.")
            out[k] = body[k].strip()[:200]
    if "sources" in body:
        src = body["sources"]
        if not isinstance(src, dict):
            raise ValueError("sources must be an object.")
        clean: dict = {}
        for k in ("jobspy", "remotive", "arbeitnow"):
            if k in src:
                clean[k] = bool(src[k])
        if "jobspy_boards" in src:
            b = src["jobspy_boards"]
            if not isinstance(b, list) or not all(isinstance(x, str) for x in b):
                raise ValueError("jobspy_boards must be a list of names.")
            clean["jobspy_boards"] = [x for x in b if x in _BOARDS]
        out["sources"] = clean
    return out


@app.post("/api/config")
def set_config():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "msg": "Expected a JSON object."}), 400
    cfg = config.load_config()
    try:
        updates = _validate_config(body)
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    for k, v in updates.items():
        if k == "sources":
            cfg["sources"] = {**cfg.get("sources", {}), **v}
        else:
            cfg[k] = v
    config.save_config(cfg)
    return jsonify({"ok": True, "config": _public_cfg(cfg)})


@app.post("/api/fetch")
def start_fetch():
    miss = profile_mod.missing_required(profile_mod.load_profile())
    if miss:
        return jsonify({"ok": False, "msg": "Complete required profile fields: " + ", ".join(miss)}), 400
    body = request.get_json(silent=True) or {}
    with _lock:   # check-and-set in one block so two clicks cannot both start a fetch
        if _fetch["running"]:
            return jsonify({"ok": False, "msg": "Fetch already running."}), 409
        _fetch.update(running=True, log=[], count=0, error=None)
    threading.Thread(target=_run_fetch, kwargs={"do_score": body.get("score", True)},
                     daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/fetch/log")
def fetch_log():
    with _lock:
        return jsonify({"running": _fetch["running"], "log": _fetch["log"][-100:],
                        "error": _fetch["error"], "count": _fetch["count"]})


@app.get("/api/jobs")
def jobs():
    return jsonify(_load_cache())


@app.post("/api/tailor")
def tailor_job():
    body = request.get_json(silent=True) or {}
    jid = body.get("id")
    job = next((j for j in _load_cache() if j["id"] == jid), None)
    if not job:
        return jsonify({"ok": False, "msg": "Job not found."}), 404
    try:
        pkg = tailor.build_package(job, profile_mod.load_profile(), force=bool(body.get("force")))
        return jsonify({"ok": True, "package": pkg, "job": job})
    except engine.EngineError as e:
        return jsonify({"ok": False, "msg": str(e)}), 502
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "msg": f"Unexpected error: {e}"}), 500


@app.get("/api/download")
def download():
    p = Path(request.args.get("path", "")).resolve()
    if not p.is_relative_to(config.APPS_DIR.resolve()) or not p.is_file():
        return jsonify({"ok": False, "msg": "Forbidden."}), 403
    return send_file(str(p), as_attachment=True, download_name=p.name)


if __name__ == "__main__":
    import os
    import socket
    cfg = config.load_config()
    # Host/port are env-configurable. Default stays localhost-only (safe);
    # set COCKPIT_HOST=0.0.0.0 to reach it from other devices on your network.
    host = os.environ.get("COCKPIT_HOST", "127.0.0.1")
    port = int(os.environ.get("COCKPIT_PORT", "5000"))
    title = cfg.get("app_title", "شغلي")
    print("\n  Job Hunt Cockpit - built by jo builds")  # ASCII only: Windows consoles choke on the Arabic title
    print(f"  On this machine:  http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        print("  WARNING: listening on all interfaces; this server has no authentication.")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
            print(f"  On your network:  http://{lan_ip}:{port}   (open this on your phone, same Wi-Fi)")
        except Exception:
            pass
    print(f"  Active AI backend: {engine.resolve_backend(cfg)}\n")
    app.run(host=host, port=port, debug=False)
