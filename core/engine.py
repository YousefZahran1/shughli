"""Pluggable generation engine.

Runs text generation on whichever AI the user has, in priority order:
  1. Claude Code CLI   (`claude` on PATH)     -> their Claude subscription  [TESTED]
  2. Codex CLI         (`codex` on PATH)      -> their OpenAI/Codex sub      [best-effort]
  3. Anthropic API     (ANTHROPIC_API_KEY)    -> billed per use
  4. OpenAI API        (OPENAI_API_KEY)       -> billed per use

No API key is required for the CLI paths — they use the subscription the user is
already logged into. CLI generation uses fresh working directories and explicit isolation flags.
Unsupported isolation flags fail closed; no weaker compatibility retry is used.
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
from contextvars import ContextVar

from . import config, policy

_RESPONSE_SCHEMA = ContextVar("response_schema", default=None)

_NEUTRAL_CWD = tempfile.gettempdir()
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class EngineError(policy.PolicyError):
    def __init__(self, message, category="other"):
        super().__init__(message)
        self.category = category


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


def _generation_process(cmd, **kwargs):
    # A fresh directory prevents discovery of unrelated files in the shared temp root.
    with tempfile.TemporaryDirectory(prefix="job-cockpit-ai-") as cwd:
        kwargs["cwd"] = cwd
        return subprocess.run(cmd, **kwargs)


def _cli_json(stdout):
    """Parse both a single envelope and Codex JSONL events before exit status."""
    try:
        value = json.loads(stdout)
        return [value] if isinstance(value, dict) else []
    except (ValueError, TypeError):
        events = []
        for line in (stdout or "").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    events.append(value)
            except ValueError:
                continue
        return events


def _cli_failure(provider, stdout="", stderr="", timed_out=False):
    raw = json.dumps(_cli_json(stdout), ensure_ascii=False) + " " + (stderr or "")
    low = raw.lower()
    if timed_out or re.search(r"timed? ?out|timeout", low):
        category = "timeout"
        message = f"{provider.title()} CLI timed out. Try again."
    elif re.search(r"oauth|authenticat|unauthorized|token.{0,30}expir|session.{0,30}expir|not logged in|please.{0,10}login|401", low):
        category = "auth_expired"
        login = "claude auth login" if provider == "claude" else "codex login"
        message = f"{provider.title()} session expired — run `{login}` in a terminal, then retry."
    elif re.search(r"rate.?limit|usage.?limit|quota|credit|billing|429|overloaded|too many requests", low):
        category = "rate_limit"
        message = f"{provider.title()} usage or rate limit reached. Wait for reset or check your plan."
    elif re.search(r"unknown (?:option|argument)|unrecognized (?:option|argument)|unexpected argument|invalid option", stderr or "", re.I):
        category = "unsupported_flag"
        message = f"{provider.title()} CLI does not support a required isolation option. Update the CLI or choose an API backend."
    else:
        category = "other"
        message = f"{provider.title()} generation failed. See the private local debug file for details."
    # Deliberately separate raw diagnostics from user-visible errors and normal traces.
    try:
        directory = config.DATA_DIR / "private_debug"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / (provider + "-" + policy.uuid.uuid4().hex + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"category": category, "stdout": str(stdout or "")[:8000],
                       "stderr": str(stderr or "")[:8000]}, f, ensure_ascii=False)
    except OSError:
        pass
    return EngineError(message, category)


def _cli_has_error(events):
    return any(e.get("is_error") or e.get("type") in {"error", "turn.failed"}
               or e.get("error") for e in events)


# ------------------------------------------------------------- Claude ----
# Lean flags: the CLI otherwise loads the user's whole Claude Code setup (tools,
# plugins, hooks, MCP servers, memory files) into every call (~40k tokens) and
# their own instructions can leak into generated CVs. Measured on CLI 2.1.268.
# Not --bare: that disables OAuth and would break subscription login.
_CLAUDE_SYSTEM = "You are a text-generation backend. Output only what is asked, nothing else."
_CLAUDE_LEAN = ["--tools", "", "--strict-mcp-config", "--safe-mode", "--no-session-persistence",
                "--disable-slash-commands", "--system-prompt", _CLAUDE_SYSTEM]



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
    cmd += _CLAUDE_LEAN  # Required isolation: never fall back to weaker flags.
    model = _model_for(cfg, "claude_model", task)
    if model and not re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,200}", model):
        raise EngineError("Invalid CLI model name.")
    if model:
        cmd += ["--model", model]
    return cmd


def _run_claude(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    claude = _which("claude")
    if not claude:
        raise EngineError("`claude` CLI not found on PATH.")

    def call(lean: bool):
        try:
            return _generation_process(_claude_cmd(claude, cfg, task, lean), input=prompt,
                                  capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace",
                                  cwd=_NEUTRAL_CWD, shell=(os.name == "nt"))
        except subprocess.TimeoutExpired as e:
            raise _cli_failure("claude", e.stdout, e.stderr, timed_out=True) from None

    proc = call(True)
    events = _cli_json(proc.stdout)
    if _cli_has_error(events) or proc.returncode != 0:
        raise _cli_failure("claude", proc.stdout, proc.stderr)
    env = events[-1] if events else {}
    text = str(env.get("result", "")).strip()
    if not text:
        raise _cli_failure("claude", proc.stdout, proc.stderr)
    return EngineResult(text=text, via="claude-cli", raw=env)


# -------------------------------------------------------------- Codex ----
def _run_codex(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    """Best-effort: OpenAI Codex CLI non-interactive. Untested in this build —
    if your Codex version expects a different invocation, adjust here."""
    codex = _which("codex")
    if not codex:
        raise EngineError("`codex` CLI not found on PATH.")
    cmd = [codex, "exec", "--skip-git-repo-check", "--json", "--ignore-user-config",
           "--ignore-rules", "--ephemeral", "--sandbox", "read-only",
           "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
           "-c", 'features.shell_tool=false', "-c", 'features.unified_exec=false',
           "-c", 'features.code_mode=false', "-c", 'features.hooks=false',
           "-c", 'features.multi_agent=false']
    model = _model_for(cfg, "codex_model", task)
    if model and not re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,200}", model):
        raise EngineError("Invalid CLI model name.")
    if model:
        cmd += ["-m", model]
    cmd += ["-"]  # read prompt from stdin
    try:
        proc = _generation_process(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace",
                              cwd=_NEUTRAL_CWD, shell=(os.name == "nt"))
    except subprocess.TimeoutExpired as e:
        raise _cli_failure("codex", e.stdout, e.stderr, timed_out=True) from None
    events = _cli_json(proc.stdout)
    if _cli_has_error(events) or proc.returncode != 0:
        raise _cli_failure("codex", proc.stdout, proc.stderr)
    messages = [e["item"].get("text", "") for e in events
                if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
    text = messages[-1] if messages else _ANSI.sub("", proc.stdout or "").strip()
    if not text:
        raise _cli_failure("codex", proc.stdout, proc.stderr)
    return EngineResult(text=text, via="codex-cli")


# ------------------------------------------------------------- APIs ----
def _post(url: str, budget: dict | None, **kw):
    """requests.post with at most ONE retry (shared with the JSON-retry budget) on
    timeout, HTTP 429 or 5xx, after a short pause."""
    import requests
    budget = budget if budget is not None else {"retry": 1}
    while True:
        try:
            run = policy.current()
            if run:
                kw["timeout"] = run.remaining()
            r = requests.post(url, **kw)
        except requests.Timeout as e:
            if budget["retry"] > 0:
                budget["retry"] -= 1
                if policy.current():
                    policy.current().attempt()
                time.sleep(2)
                continue
            raise EngineError("API request timed out.") from e
        if (r.status_code == 429 or r.status_code >= 500) and budget["retry"] > 0:
            budget["retry"] -= 1
            if policy.current():
                policy.current().attempt()
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
              json={"model": model, "max_tokens": policy.output_budget(task, model), "system": policy.SYSTEM,
                    "messages": [{"role": "user", "content": prompt}]},
              timeout=timeout)
    if r.status_code != 200:
        raise EngineError(f"Anthropic API request failed (HTTP {r.status_code}).")
    if r.json().get("stop_reason") == "max_tokens":
        raise EngineError("AI reply was cut off. Increase the task output budget or shorten the input.", "truncated")
    text = "".join(b.get("text", "") for b in r.json().get("content", [])
                   if b.get("type") == "text").strip()
    if not text:
        raise EngineError("Anthropic API returned empty text.")
    return EngineResult(text=text, via="anthropic-api", raw={"usage": r.json().get("usage", {})})


def _run_openai(prompt: str, timeout: int, cfg: dict, task: str = "main", budget=None) -> EngineResult:
    key = os.environ.get("OPENAI_API_KEY") or cfg.get("openai_api_key")
    if not key:
        raise EngineError("No OpenAI API key set.")
    model = _model_for(cfg, "openai_model", task) or "gpt-5"
    payload = {"model": model, "max_completion_tokens": policy.output_budget(task, model),
               "messages": [{"role": "system", "content": policy.SYSTEM}, {"role": "user", "content": prompt}]}
    schema = _RESPONSE_SCHEMA.get()
    if schema:
        payload["response_format"] = {"type": "json_schema", "json_schema":
                                      {"name": "job_application", "strict": True, "schema": schema}}
    r = _post("https://api.openai.com/v1/chat/completions", budget,
              headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
              json=payload, timeout=timeout)
    if r.status_code != 200:
        raise EngineError(f"OpenAI API request failed (HTTP {r.status_code}).")
    if r.json()["choices"][0].get("finish_reason") == "length":
        raise EngineError("AI reply was cut off. Increase the task output budget or shorten the input.", "truncated")
    text = (r.json()["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise EngineError("OpenAI API returned empty text.")
    return EngineResult(text=text, via="openai-api", raw={"usage": r.json().get("usage", {})})


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
    budget = budget if budget is not None else {"retry": 1}
    payload = {"model": model, "max_completion_tokens": policy.output_budget(task, model),
               "messages": [{"role": "system", "content": policy.SYSTEM}, {"role": "user", "content": prompt}]}
    r = _post(url, budget, headers=headers, json=payload, timeout=timeout)
    if r.status_code == 400 and "max_completion_tokens" in r.text and budget["retry"] > 0:
        budget["retry"] -= 1
        if policy.current():
            policy.current().attempt()
        payload = dict(payload)
        payload["max_tokens"] = payload.pop("max_completion_tokens")
        r = _post(url, budget, headers=headers, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise EngineError(f"Custom API request failed (HTTP {r.status_code}).")
    if r.json()["choices"][0].get("finish_reason") == "length":
        raise EngineError("AI reply was cut off. Increase the task output budget or shorten the input.", "truncated")
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
    cfg = config.load_config() if cfg is None else cfg
    if task not in {"main", "fast"}:
        raise EngineError("Unknown AI task tier.")
    if len(prompt) > policy.POLICY.max_input_chars:
        raise EngineError("AI input is too large. Shorten the CV or answers.")
    backend = resolve_backend(cfg)
    if backend not in _RUNNERS:
        raise EngineError("No supported AI backend available. Select a backend in Settings.")
    try:
        with policy.scope(timeout, background=(task in {"fast", "score"})) as run:
            run.attempt()
            result = _RUNNERS[backend](policy.SYSTEM + "\n\n" + prompt, run.remaining(), cfg, task, _budget)
            run.remaining()
            if len(result.text) > policy.POLICY.max_output_chars:
                raise EngineError("AI output exceeded the size limit.")
            if result.raw and isinstance(result.raw.get("usage"), dict):
                usage = {k: v for k, v in result.raw["usage"].items() if isinstance(v, (int, float))}
                try:
                    policy.trace(run, backend=backend, task=task, usage=usage, outcome="provider_returned")
                except OSError:
                    pass
            return result
    except EngineError:
        raise
    except policy.PolicyError as e:
        raise EngineError(str(e)) from e


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
    raise EngineError("Model did not return valid JSON.")


_JSON_REMINDER = ("\n\nYour previous reply was not valid JSON. Reply again with ONLY the JSON, "
                  "no commentary and no code fences.")


def generate_json(prompt: str, timeout: int = 300, cfg: dict | None = None, task: str = "main", schema=None):
    """One retry in total per call (API retry OR bad-JSON retry, whichever fires
    first), so the worst case is two calls and cost stays bounded."""
    cfg = config.load_config() if cfg is None else cfg
    try:
        with policy.scope(timeout, background=(task in {"fast", "score"})) as run:
            schema_token = _RESPONSE_SCHEMA.set(schema)
            outcome = "failed"
            try:
                budget = {"retry": 1}
                text = generate(prompt, timeout=timeout, cfg=cfg, task=task, _budget=budget).text
                try:
                    data = extract_json(text)
                except EngineError:
                    if budget["retry"] <= 0:
                        raise
                    budget["retry"] -= 1
                    data = extract_json(generate(prompt + _JSON_REMINDER, timeout=timeout, cfg=cfg,
                                                 task=task, _budget=budget).text)
                outcome = "json_parsed"
                return data
            finally:
                _RESPONSE_SCHEMA.reset(schema_token)
                try:
                    policy.trace(run, backend=resolve_backend(cfg), task=task,
                                 model_fingerprint=policy.fingerprint(cfg, task), outcome=outcome)
                except OSError:
                    pass  # Trace storage must not destroy a generated result.
    except EngineError:
        raise
    except policy.PolicyError as e:
        raise EngineError(str(e)) from e


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
