"""Pluggable generation engine.

Runs text generation on whichever AI the user has, in priority order:
  1. Claude Code CLI   (`claude` on PATH)     -> their Claude subscription  [TESTED]
  2. Codex CLI         (`codex` on PATH)      -> their OpenAI/Codex sub      [best-effort]
  3. Anthropic API     (ANTHROPIC_API_KEY)    -> billed per use
  4. OpenAI API        (OPENAI_API_KEY)       -> billed per use

No API key is required for the CLI paths — they use the subscription the user is
already logged into. CLI calls run from a neutral working dir with tools
disabled, so they don't inherit any surrounding project's instructions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

from . import config

_NEUTRAL_CWD = tempfile.gettempdir()
_CLAUDE_NO_TOOLS = "Bash Read Edit Write WebFetch WebSearch Task Glob Grep NotebookEdit"
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class EngineError(RuntimeError):
    pass


@dataclass
class EngineResult:
    text: str
    via: str
    raw: dict | None = None


# ----------------------------------------------------------- detection ----
def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for ext in (".cmd", ".exe", ""):
            p = os.path.expanduser(fr"~\AppData\Roaming\npm\{name}{ext}")
            if os.path.exists(p):
                return p
    return None


def available_backends(cfg: dict | None = None) -> dict[str, bool]:
    cfg = cfg or config.load_config()
    return {
        "claude-cli": _which("claude") is not None,
        "codex-cli": _which("codex") is not None,
        "anthropic-api": bool(os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key")),
        "openai-api": bool(os.environ.get("OPENAI_API_KEY") or cfg.get("openai_api_key")),
        "custom-api": bool(cfg.get("custom_base_url") and cfg.get("custom_model")),
    }


def resolve_backend(cfg: dict | None = None) -> str:
    cfg = cfg or config.load_config()
    avail = available_backends(cfg)
    choice = cfg.get("backend", "auto")
    if choice != "auto":
        return choice  # honor explicit choice even if detection is unsure
    for name in ("claude-cli", "codex-cli", "anthropic-api", "openai-api", "custom-api"):
        if avail[name]:
            return name
    return "none"


# ------------------------------------------------------------- Claude ----
# Lean flags: the CLI otherwise loads the user's whole Claude Code setup (tools,
# plugins, hooks, MCP servers, memory files) into every call (~40k tokens) and
# their own instructions can leak into generated CVs. Measured on CLI 2.1.268.
# Not --bare: that disables OAuth and would break subscription login.
_CLAUDE_SYSTEM = "You are a text-generation backend. Output only what is asked, nothing else."
_CLAUDE_LEAN = ["--tools", "", "--strict-mcp-config", "--safe-mode", "--no-session-persistence",
                "--disable-slash-commands", "--system-prompt", _CLAUDE_SYSTEM]
_claude_lean_ok = True   # flips to False for the life of the process if the CLI rejects the flags


def _model_for(cfg: dict, main_key: str, task: str) -> str:
    """Model for this task. 'fast' tasks use the <backend>_model_fast key if set,
    else the main model."""
    if task == "fast":
        fast = cfg.get(main_key.replace("_model", "_model_fast"))
        if fast:
            return fast
    return cfg.get(main_key) or ""


def _claude_cmd(claude: str, cfg: dict, task: str, lean: bool) -> list[str]:
    cmd = [claude, "-p", "--output-format", "json"]
    cmd += _CLAUDE_LEAN if lean else ["--disallowed-tools", _CLAUDE_NO_TOOLS]
    model = _model_for(cfg, "claude_model", task)
    if model:
        cmd += ["--model", model]
    return cmd


def _run_claude(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    global _claude_lean_ok
    claude = _which("claude")
    if not claude:
        raise EngineError("`claude` CLI not found on PATH.")

    def call(lean: bool):
        try:
            return subprocess.run(_claude_cmd(claude, cfg, task, lean), input=prompt,
                                  capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace",
                                  cwd=_NEUTRAL_CWD, shell=(os.name == "nt"))
        except subprocess.TimeoutExpired as e:
            raise EngineError(f"Claude CLI timed out after {timeout}s.") from e

    proc = call(_claude_lean_ok)
    if _claude_lean_ok and proc.returncode != 0 and not (proc.stdout or "").strip() \
            and re.search(r"unknown (?:option|argument)|unrecognized|unexpected argument|invalid option",
                          (proc.stderr or ""), re.I):
        _claude_lean_ok = False          # older CLI: retry once with the old flag set
        proc = call(False)
    out = (proc.stdout or "").strip()
    if not out:
        raise EngineError(f"Claude CLI returned nothing. stderr: {(proc.stderr or '')[:300]}")
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}\s*$", out, re.DOTALL)
        if not m:
            raise EngineError(f"Could not parse Claude CLI output: {out[:300]}")
        env = json.loads(m.group(0))
    if env.get("is_error"):
        res = str(env.get("result", ""))
        if "oauth" in res.lower() or "authenticate" in res.lower():
            raise EngineError("Claude session expired — run `claude auth login` "
                              "(or `claude setup-token`) in a terminal, then retry.")
        raise EngineError(f"Claude CLI error: {res[:300]}")
    text = str(env.get("result", "")).strip()
    if not text:
        raise EngineError("Claude CLI produced an empty result.")
    return EngineResult(text=text, via="claude-cli", raw=env)


# -------------------------------------------------------------- Codex ----
def _run_codex(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    """Best-effort: OpenAI Codex CLI non-interactive. Untested in this build —
    if your Codex version expects a different invocation, adjust here."""
    codex = _which("codex")
    if not codex:
        raise EngineError("`codex` CLI not found on PATH.")
    cmd = [codex, "exec", "--skip-git-repo-check"]
    if cfg.get("codex_model"):
        cmd += ["-m", cfg["codex_model"]]
    cmd += ["-"]  # read prompt from stdin
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace",
                              cwd=_NEUTRAL_CWD, shell=(os.name == "nt"))
    except subprocess.TimeoutExpired as e:
        raise EngineError(f"Codex CLI timed out after {timeout}s.") from e
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        err = (proc.stderr or "").strip()
        if "login" in err.lower() or "auth" in err.lower():
            raise EngineError("Codex session not authenticated — run `codex login` in a terminal.")
        raise EngineError(f"Codex CLI error: {err[:300]}")
    text = _ANSI.sub("", proc.stdout or "").strip()
    if not text:
        raise EngineError("Codex CLI produced no output.")
    return EngineResult(text=text, via="codex-cli")


# ------------------------------------------------------------- APIs ----
def _post(url: str, budget: dict | None, **kw):
    """requests.post with at most ONE retry (shared with the JSON-retry budget) on
    timeout, HTTP 429 or 5xx, after a short pause."""
    import requests
    budget = budget if budget is not None else {"retry": 1}
    while True:
        try:
            r = requests.post(url, **kw)
        except requests.Timeout as e:
            if budget["retry"] > 0:
                budget["retry"] -= 1
                time.sleep(2)
                continue
            raise EngineError("API request timed out.") from e
        if (r.status_code == 429 or r.status_code >= 500) and budget["retry"] > 0:
            budget["retry"] -= 1
            time.sleep(2)
            continue
        return r


def _run_anthropic(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key")
    if not key:
        raise EngineError("No Anthropic API key set.")
    model = _model_for(cfg, "anthropic_model", task) or "claude-sonnet-5-5"
    r = _post("https://api.anthropic.com/v1/messages", budget,
              headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"},
              json={"model": model, "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}]},
              timeout=timeout)
    if r.status_code != 200:
        raise EngineError(f"Anthropic API {r.status_code}: {r.text[:200]}")
    text = "".join(b.get("text", "") for b in r.json().get("content", [])
                   if b.get("type") == "text").strip()
    if not text:
        raise EngineError("Anthropic API returned empty text.")
    return EngineResult(text=text, via="anthropic-api")


def _run_openai(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    key = os.environ.get("OPENAI_API_KEY") or cfg.get("openai_api_key")
    if not key:
        raise EngineError("No OpenAI API key set.")
    model = _model_for(cfg, "openai_model", task) or "gpt-5"
    r = _post("https://api.openai.com/v1/chat/completions", budget,
              headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
              json={"model": model, "messages": [{"role": "user", "content": prompt}]},
              timeout=timeout)
    if r.status_code != 200:
        raise EngineError(f"OpenAI API {r.status_code}: {r.text[:200]}")
    text = (r.json()["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise EngineError("OpenAI API returned empty text.")
    return EngineResult(text=text, via="openai-api")


def _run_custom(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    """Any OpenAI-compatible endpoint (OpenRouter, Groq, Together, a local
    server, …). User supplies base URL + model + key."""
    key = os.environ.get("CUSTOM_API_KEY") or cfg.get("custom_api_key")
    base = (cfg.get("custom_base_url") or "").rstrip("/")
    model = _model_for(cfg, "custom_model", task)
    if not (base and model):
        raise EngineError("Custom provider needs a base URL and model.")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    r = _post(url, budget, headers=headers,
              json={"model": model, "messages": [{"role": "user", "content": prompt}]},
              timeout=timeout)
    if r.status_code != 200:
        raise EngineError(f"Custom API {r.status_code}: {r.text[:200]}")
    text = (r.json()["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise EngineError("Custom API returned empty text.")
    return EngineResult(text=text, via="custom-api")


_RUNNERS = {
    "claude-cli": _run_claude,
    "codex-cli": _run_codex,
    "anthropic-api": _run_anthropic,
    "openai-api": _run_openai,
    "custom-api": _run_custom,
}


# ----------------------------------------------------------- public ----
def generate(prompt: str, timeout: int = 300, cfg: dict | None = None,
             task: str = "main", _budget: dict | None = None) -> EngineResult:
    """task: "fast" (scoring, CV import) uses the cheaper model tier; "main" the strong one."""
    cfg = cfg or config.load_config()
    backend = resolve_backend(cfg)
    if backend == "none":
        raise EngineError(
            "No AI backend available. Install Claude Code (`claude`) or Codex "
            "(`codex`) and log in, or set ANTHROPIC_API_KEY / OPENAI_API_KEY.")
    return _RUNNERS[backend](prompt, timeout, cfg, task, _budget)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str):
    m = _FENCE.search(text)
    cand = (m.group(1) if m else text).strip()
    try:
        return json.loads(cand)
    except json.JSONDecodeError:
        pass
    spans = []
    for pat in (r"\{.*\}", r"\[.*\]"):
        mm = re.search(pat, cand, re.DOTALL)
        if mm:
            spans.append((mm.start(), mm.group(0)))
    for _, blob in sorted(spans):
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            continue
    raise EngineError(f"Model did not return valid JSON. Got: {text[:200]}")


_JSON_REMINDER = ("\n\nYour previous reply was not valid JSON. Reply again with ONLY the JSON, "
                  "no commentary and no code fences.")


def generate_json(prompt: str, timeout: int = 300, cfg: dict | None = None, task: str = "main"):
    """One retry in total per call (API retry OR bad-JSON retry, whichever fires
    first), so the worst case is two calls and cost stays bounded."""
    budget = {"retry": 1}
    text = generate(prompt, timeout=timeout, cfg=cfg, task=task, _budget=budget).text
    try:
        return extract_json(text)
    except EngineError:
        if budget["retry"] <= 0:
            raise
        budget["retry"] -= 1
        return extract_json(generate(prompt + _JSON_REMINDER, timeout=timeout, cfg=cfg,
                                     task=task, _budget=budget).text)


# ---------------------------------------------------- account / auth ----
# Per-provider CLI login + status. Claude is tested; Codex is best-effort.
_AUTH = {
    "claude": {"bin": "claude", "status": ["auth", "status"], "login": ["auth", "login"]},
    "codex": {"bin": "codex", "status": ["login", "status"], "login": ["login"]},
}


_AUTH_CACHE: dict[str, tuple[float, dict]] = {}
_AUTH_TTL = 60.0


def auth_status(provider: str, fresh: bool = False) -> dict:
    """Cached ~60s (each check runs a CLI subprocess, ~0.7s); `fresh` bypasses
    the cache — used while a login is being polled."""
    hit = _AUTH_CACHE.get(provider)
    if hit and not fresh and time.time() - hit[0] < _AUTH_TTL:
        return hit[1]
    res = _auth_status_uncached(provider)
    _AUTH_CACHE[provider] = (time.time(), res)
    return res


def _auth_status_uncached(provider: str) -> dict:
    """Is this provider's subscription logged in? {authenticated, detail}."""
    spec = _AUTH.get(provider)
    if not spec:
        return {"authenticated": False, "detail": "unknown provider"}
    binpath = _which(spec["bin"])
    if not binpath:
        return {"authenticated": False, "detail": f"{spec['bin']} CLI not installed"}
    try:
        p = subprocess.run([binpath] + spec["status"], capture_output=True, text=True,
                           timeout=20, encoding="utf-8", errors="replace",
                           cwd=_NEUTRAL_CWD, shell=(os.name == "nt"))
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        m = re.search(r"\{.*\}", out, re.DOTALL)
        if m:
            try:
                j = json.loads(m.group(0))
                if "loggedIn" in j:
                    return {"authenticated": bool(j["loggedIn"]),
                            "detail": j.get("authMethod") or j.get("apiProvider") or provider}
            except json.JSONDecodeError:
                pass
        low = out.lower()
        authed = p.returncode == 0 and "not logged in" not in low and (
            "logged in" in low or "authenticated" in low or "active" in low or "@" in out)
        return {"authenticated": bool(authed), "detail": out[:200]}
    except Exception as e:  # noqa: BLE001
        return {"authenticated": False, "detail": str(e)[:200]}


_NPM_PKG = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
_install_state: dict[str, dict] = {}


def install_status(provider: str) -> dict:
    return _install_state.get(provider, {"state": "idle"})


def _install_then_login(provider: str) -> None:
    import threading  # noqa: F401
    st = _install_state[provider]
    try:
        npm = shutil.which("npm")
        if not npm:
            raise EngineError("Node.js/npm not found. Install Node from https://nodejs.org then retry.")
        r = subprocess.run([npm, "install", "-g", _NPM_PKG[provider]], capture_output=True,
                           text=True, timeout=600, shell=False)
        if r.returncode != 0:
            raise EngineError((r.stderr or r.stdout or "npm install failed").strip()[-300:])
        st["state"] = "logging_in"
        login_start(provider)
        st["state"] = "done"
    except Exception as e:  # noqa: BLE001
        st["state"] = "error"
        st["error"] = str(e)[:300]


def install_and_login(provider: str) -> dict:
    """If the CLI is missing: npm-install it in the background, then open the login flow."""
    import threading
    if provider not in _NPM_PKG:
        raise EngineError(f"Unknown provider: {provider}")
    if _which(_AUTH[provider]["bin"]):
        return login_start(provider)
    if _install_state.get(provider, {}).get("state") == "installing":
        return {"ok": True, "installing": True, "msg": "Install already running…"}
    _install_state[provider] = {"state": "installing"}
    threading.Thread(target=_install_then_login, args=(provider,), daemon=True).start()
    return {"ok": True, "installing": True,
            "msg": f"Installing {provider} CLI (1–2 min), then your browser will open to sign in."}


def login_start(provider: str) -> dict:
    """Launch `<cli> login` detached — opens the browser/device OAuth flow.
    The app then polls auth_status(provider) until authenticated."""
    spec = _AUTH.get(provider)
    if not spec:
        raise EngineError(f"Unknown provider: {provider}")
    binpath = _which(spec["bin"])
    if not binpath:
        raise EngineError(f"{spec['bin']} CLI not installed.")
    try:
        kwargs = {"cwd": _NEUTRAL_CWD, "shell": (os.name == "nt"),
                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
        subprocess.Popen([binpath] + spec["login"], **kwargs)
        return {"ok": True, "msg": f"{provider} login opened in your browser. Complete it, then this will connect."}
    except Exception as e:  # noqa: BLE001
        raise EngineError(f"Could not start {provider} login: {e}")
