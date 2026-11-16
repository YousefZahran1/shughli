/* شغلي (Job Hunt Cockpit) — built by jo builds */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const scoreClass = s => s == null ? "s-lo" : s >= 75 ? "s-hi" : s >= 50 ? "s-md" : "s-lo";
const scoreText = s => s == null ? "\u2014" : s;      // unscored jobs show a neutral dash, not a fake 0
const safeUrl = u => { try { const x = new URL(u); return (x.protocol === "http:" || x.protocol === "https:") ? x.href : "#"; } catch { return "#"; } };

let JOBS = [], PROFILE = null, CONFIG = null;
const activeRegion = new Set(), activeElig = new Set();

// ---- nested get/set ----
const getPath = (o, p) => p.split(".").reduce((a, k) => (a || {})[k], o);
function setPath(o, p, v) { const ks = p.split("."); let c = o; for (let i = 0; i < ks.length - 1; i++) c = c[ks[i]] ??= {}; c[ks[ks.length - 1]] = v; }

// ---- tabs ----
function switchTab(name) {
  $$(".tab").forEach(x => x.classList.toggle("on", x.dataset.tab === name));
  ["jobs", "profile", "settings"].forEach(n => $("#tab-" + n).classList.toggle("hidden", n !== name));
  if (name === "settings") { renderSettings(); loadBackends(); }
}
$$(".tab").forEach(t => t.onclick = () => switchTab(t.dataset.tab));

// ---- onboarding (register everything first) ----
function liveReady() {
  const v = sel => (($(sel) || {}).value || "").trim();
  const name = v('[data-p="personal.name"]'), country = v('[data-p="preferences.target_country"]');
  const nationality = v('[data-p="nationality"]'), kws = v('[data-plist="preferences.role_keywords"]');
  return { name: !!name, country: !!country, nationality: !!nationality, kws: !!kws,
           ready: !!(name && country && nationality && kws) };
}
function updateOnboarding() {
  const r = liveReady();
  const cvDone = !!(PROFILE && PROFILE.meta && PROFILE.meta.imported);
  const steps = { cv: cvDone, who: r.name && r.nationality, where: r.country && r.kws, save: r.ready };
  const order = ["cv", "who", "where", "save"];
  let activeSet = false;
  order.forEach(k => {
    const li = $(`.steps li[data-step="${k}"]`); if (!li) return;
    const done = k === "save" ? r.ready : steps[k];
    li.classList.toggle("done", !!done);
    li.classList.remove("active");
    if (!done && !activeSet) { li.classList.add("active"); activeSet = true; }
  });
  $("#onboard").classList.toggle("hidden", r.ready);      // hide once fully set up
  $("#goto-jobs").classList.toggle("hidden", !r.ready);
}

// ---- status ----
let LAST_STATUS = null;
async function refreshStatus() {
  let s;
  try { s = await (await fetch("/api/status")).json(); }
  catch (e) { $("#subtitle").textContent = "\u26a0 server not reachable"; return null; }
  LAST_STATUS = s;
  showModelRouting(s.model_routing);
  CONFIG = s.config;
  $("#app-title").textContent = s.app_title;
  document.title = s.app_title;
  const pill = $("#backend-pill");
  if (s.active_backend && s.active_backend !== "none") {
    pill.className = "pill pill-good"; pill.textContent = "● " + s.active_backend;
  } else { pill.className = "pill pill-warn"; pill.textContent = "⚠ no AI backend"; }
  const bits = [`${s.cached_jobs} jobs`];
  if (!s.profile_ready) bits.push("set up Profile →");
  $("#subtitle").textContent = bits.join(" · ");
  AVAIL = s.backends || {};
  return s;
}

// ================= PROFILE =================
function lines(v) { return (v || []).join("\n"); }
function parseLines(t) { return t.split("\n").map(x => x.trim()).filter(Boolean); }

function rcard(fields, data) {
  const wrap = document.createElement("div"); wrap.className = "rcard";
  wrap.innerHTML = `<button class="rm" title="remove">✕</button><div class="rgrid"></div>`;
  const grid = wrap.querySelector(".rgrid");
  fields.forEach(f => {
    const val = data ? (data[f.key] || "") : "";
    const cell = document.createElement("label");
    cell.style.gridColumn = f.wide ? "1 / -1" : "";
    if (f.area) cell.innerHTML = `${f.label}<textarea data-f="${f.key}" rows="3">${esc(Array.isArray(val) ? val.join("\n") : val)}</textarea>`;
    else cell.innerHTML = `${f.label}<input data-f="${f.key}" type="text" value="${esc(val)}">`;
    grid.appendChild(cell);
  });
  wrap.querySelector(".rm").onclick = () => wrap.remove();
  return wrap;
}
const SCHEMAS = {
  work: { list: "#work-list", fields: [{ key: "region", label: "Region" }, { key: "status", label: "Status", wide: true }] },
  exp: { list: "#exp-list", fields: [{ key: "title", label: "Title" }, { key: "org", label: "Organization" }, { key: "location", label: "Location" }, { key: "dates", label: "Dates" }, { key: "bullets", label: "Bullets (one per line)", area: true, wide: true }] },
  proj: { list: "#proj-list", fields: [{ key: "name", label: "Name" }, { key: "stack", label: "Stack" }, { key: "dates", label: "Dates" }, { key: "bullets", label: "Bullets (one per line)", area: true, wide: true }] },
  edu: { list: "#edu-list", fields: [{ key: "degree", label: "Degree" }, { key: "institution", label: "Institution" }, { key: "dates", label: "Dates" }, { key: "detail", label: "Detail", wide: true }] },
};
function addCard(type, data) { const sc = SCHEMAS[type]; $(sc.list).appendChild(rcard(sc.fields, data)); }
function readCards(type) {
  const sc = SCHEMAS[type];
  return $$(`${sc.list} .rcard`).map(card => {
    const o = {};
    sc.fields.forEach(f => {
      const el = card.querySelector(`[data-f="${f.key}"]`);
      o[f.key] = f.area ? parseLines(el.value) : el.value.trim();
    });
    return o;
  });
}

function renderProfile(p) {
  PROFILE = p;
  $$("[data-p]").forEach(el => {
    const v = getPath(p, el.dataset.p);
    if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? "";
  });
  $$("[data-plist]").forEach(el => el.value = lines(getPath(p, el.dataset.plist)));
  $("#skills-text").value = (p.skills || []).map(s => `${s.category}: ${s.items}`).join("\n");
  const sen = new Set((p.preferences || {}).seniority || []);
  $$("#seniority-checks input").forEach(c => c.checked = sen.has(c.value));
  SCHEMAS.work && ($("#work-list").innerHTML = "", (p.work_authorization || []).forEach(d => addCard("work", d)));
  $("#exp-list").innerHTML = ""; (p.experience || []).forEach(d => addCard("exp", d));
  $("#proj-list").innerHTML = ""; (p.projects || []).forEach(d => addCard("proj", d));
  $("#edu-list").innerHTML = ""; (p.education || []).forEach(d => addCard("edu", d));
  updateOnboarding();
}
function collectProfile() {
  const p = JSON.parse(JSON.stringify(PROFILE || {}));
  $$("[data-p]").forEach(el => setPath(p, el.dataset.p, el.type === "checkbox" ? el.checked : el.value.trim()));
  $$("[data-plist]").forEach(el => setPath(p, el.dataset.plist, parseLines(el.value)));
  p.skills = parseLines($("#skills-text").value).map(l => {
    const i = l.indexOf(":"); return i < 0 ? { category: "", items: l.trim() } : { category: l.slice(0, i).trim(), items: l.slice(i + 1).trim() };
  });
  p.preferences = p.preferences || {};
  p.preferences.seniority = $$("#seniority-checks input:checked").map(c => c.value);
  p.work_authorization = readCards("work");
  p.experience = readCards("exp"); p.projects = readCards("proj"); p.education = readCards("edu");
  return p;
}

// ---- mandatory validation ----
const REQ = { name: '[data-p="personal.name"]', nationality: '[data-p="nationality"]',
  target_country: '[data-p="preferences.target_country"]', role_keywords: '[data-plist="preferences.role_keywords"]' };
function validateProfile() {
  const missing = [];
  $$(".fl.req").forEach(l => l.classList.remove("invalid"));
  for (const [key, sel] of Object.entries(REQ)) {
    const el = $(sel); const empty = !(el && el.value.trim());
    if (empty) { missing.push(key); el?.closest(".fl")?.classList.add("invalid"); }
  }
  return missing;
}
async function loadProfile() {
  try { renderProfile(await (await fetch("/api/profile")).json()); }
  catch (e) { $("#profile-saved").textContent = "\u26a0 could not load profile: " + e.message; }
}
async function saveProfile() {
  const missing = validateProfile();
  if (missing.length) {
    $("#profile-saved").innerHTML = `<span style="color:#ff8a8d">Fill required: ${missing.join(", ").replace(/_/g, " ")}</span>`;
    const first = $(".fl.invalid"); first?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  $("#profile-save").disabled = true; $("#profile-saved").textContent = "saving…";
  try {
    const r = await (await fetch("/api/profile", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(collectProfile()) })).json();
    if (!r.profile) throw new Error(r.msg || "save failed");
    PROFILE = r.profile; $("#profile-saved").textContent = "saved ✓";
    updateOnboarding(); refreshStatus(); setTimeout(() => $("#profile-saved").textContent = "", 2500);
  } catch (e) { $("#profile-saved").textContent = "\u26a0 " + e.message; }
  finally { $("#profile-save").disabled = false; }
}
$$("[data-add]").forEach(b => b.onclick = () => addCard(b.dataset.add));
$("#profile-save").onclick = saveProfile;
$("#goto-jobs").onclick = () => switchTab("jobs");
$("#tab-profile").addEventListener("input", updateOnboarding);

// CV upload
$("#cv-upload-btn").onclick = () => $("#cv-file").click();
$("#cv-file").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  $("#cv-status").innerHTML = '<span class="spin"></span>reading & structuring on your AI backend…';
  const fd = new FormData(); fd.append("cv", f);
  try {
    const r = await (await fetch("/api/profile/import", { method: "POST", body: fd })).json();
    if (!r.ok) throw new Error(r.msg);
    renderProfile(r.profile); $("#cv-status").textContent = "imported ✓ — review & save below"; refreshStatus();
  } catch (err) { $("#cv-status").textContent = "⚠ " + err.message; }
  e.target.value = "";
};

// ================= SETTINGS =================
const BACKENDS = [
  { id: "claude-cli", name: "Claude Code CLI", sub: "subscription", type: "cli", provider: "claude" },
  { id: "codex-cli", name: "Codex CLI", sub: "ChatGPT / OpenAI subscription", type: "cli", provider: "codex" },
  { id: "anthropic-api", name: "Anthropic API", sub: "paste a key", type: "api", field: "anthropic_api_key", flag: "anthropic_key_set" },
  { id: "openai-api", name: "OpenAI API", sub: "paste a key", type: "api", field: "openai_api_key", flag: "openai_key_set" },
  { id: "custom-api", name: "Custom / other model", sub: "any OpenAI-compatible API (OpenRouter, Groq, local…)", type: "custom" },
  { id: "auto", name: "Auto", sub: "first available", type: "auto" },
];
let AUTH = {}, AVAIL = {};
let loginActive = false;
async function loadBackends() {
  try { AUTH = await (await fetch("/api/auth/status" + (loginActive ? "?fresh=1" : ""))).json(); } catch { AUTH = {}; }
  renderBackends(AVAIL, (CONFIG || {}).backend || "auto");
}
function renderBackends(avail, current) {
  $("#backend-options").innerHTML = BACKENDS.map(b => {
    let control = "", tag = "";
    if (b.type === "cli") {
      const installed = avail[b.id];
      const authed = AUTH[b.provider] && AUTH[b.provider].authenticated;
      if (!installed) { control = `<button class="btn small bk-login" data-provider="${b.provider}">Install &amp; sign in</button>`; tag = `<span class="tag">not installed</span>`; }
      else if (authed) tag = `<span class="tag ok">● connected</span>`;
      else { control = `<button class="btn small bk-login" data-provider="${b.provider}">Log in</button>`; tag = `<span class="tag">not logged in</span>`; }
    } else if (b.type === "api") {
      const set = AUTH[b.flag];
      control = `<input class="bk-key" type="password" placeholder="${set ? "saved ✓ — paste to replace" : "paste API key"}" data-field="${b.field}">
                 <button class="btn small bk-save" data-field="${b.field}">Save</button>`;
      tag = set ? `<span class="tag ok">key set</span>` : `<span class="tag">no key</span>`;
    } else if (b.type === "custom") {
      const set = AUTH.custom_key_set;
      control = `<div class="bk-custom">
        <input class="bk-cbase" type="text" placeholder="base URL — e.g. https://openrouter.ai/api/v1" value="${esc(AUTH.custom_base_url || "")}">
        <input class="bk-cmodel" type="text" placeholder="model — e.g. anthropic/claude-sonnet-4.5" value="${esc(AUTH.custom_model || "")}">
        <div class="bk-crow"><input class="bk-ckey" type="password" placeholder="${set ? "key saved — paste to replace" : "API key (leave blank for local/no-auth)"}">
          <button class="btn small bk-csave">Save</button></div></div>`;
      tag = set ? `<span class="tag ok">configured</span>` : `<span class="tag">not set</span>`;
    } else {
      tag = Object.values(avail).some(Boolean) ? `<span class="tag ok">ready</span>` : `<span class="tag">none ready</span>`;
    }
    return `<div class="backend-opt" data-id="${b.id}">
      <input type="radio" name="backend" value="${b.id}" ${current === b.id ? "checked" : ""}>
      <span class="bk-text"><span class="bk-name">${b.name}</span> <span class="bk-sub">${b.sub}</span></span>
      <span class="bk-ctl">${control}</span>${tag}</div>`;
  }).join("");
  wireBackendControls();
}
function wireBackendControls() {
  $$("#backend-options .backend-opt").forEach(row => row.onclick = e => {
    if (e.target.closest(".bk-ctl")) return;      // don't hijack input/button clicks
    row.querySelector('input[type="radio"]').checked = true;
  });
  $$(".bk-login").forEach(b => b.onclick = () => cliLogin(b.dataset.provider, b));
  $$(".bk-save").forEach(b => b.onclick = () => saveKey(b.dataset.field, b));
  const cs = $(".bk-csave"); if (cs) cs.onclick = () => saveCustom(cs);
}
async function saveCustom(btn) {
  const base = $(".bk-cbase").value.trim(), model = $(".bk-cmodel").value.trim(), key = $(".bk-ckey").value.trim();
  if (!base || !model) { alert("Custom provider needs a base URL and a model name."); return; }
  const body = { custom_base_url: base, custom_model: model }; if (key) body.custom_api_key = key;
  btn.innerHTML = '<span class="spin"></span>'; btn.disabled = true;
  await fetch("/api/keys", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  await refreshStatus(); loadBackends();
}
let loginPoll = null;
async function cliLogin(provider, btn) {
  btn.innerHTML = '<span class="spin"></span>'; btn.disabled = true;
  let d;
  try { d = await (await fetch(`/api/auth/${provider}/login`, { method: "POST" })).json(); }
  catch (e) { btn.textContent = "failed"; btn.disabled = false; alert("Request failed: " + e.message); return; }
  if (!d.ok) { btn.textContent = "failed"; alert(d.msg); btn.disabled = false; return; }
  loginActive = true;
  if (d.installing) btn.innerHTML = '<span class="spin"></span> installing…';
  let tries = 0; clearInterval(loginPoll);
  loginPoll = setInterval(async () => {
    tries++;
    if (d.installing) {
      const is = await (await fetch(`/api/auth/${provider}/install`)).json();
      if (is.state === "error") { clearInterval(loginPoll); loginActive = false; btn.disabled = false; btn.textContent = "retry"; alert("Install failed: " + is.error); return; }
      if (is.state === "logging_in" || is.state === "done") { btn.innerHTML = '<span class="spin"></span> sign in via browser…'; await refreshStatus(); }
    }
    const st = await (await fetch("/api/auth/status?fresh=1")).json();
    if (st[provider] && st[provider].authenticated) { clearInterval(loginPoll); loginActive = false; await refreshStatus(); loadBackends(); }
    else if (tries > 100) { clearInterval(loginPoll); loginActive = false; btn.disabled = false; btn.textContent = "retry"; }
  }, 3000);
}
async function saveKey(field, btn) {
  const input = $(`.bk-key[data-field="${field}"]`); const val = input.value.trim();
  if (!val) { input.focus(); return; }
  btn.innerHTML = '<span class="spin"></span>'; btn.disabled = true;
  await fetch("/api/keys", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ [field]: val }) });
  input.value = ""; await refreshStatus(); loadBackends();
}
function showModelRouting(routing) {
  if (!routing) return;
  $("#effective-models").textContent = `Active backend: ${routing.backend}. Fast tier: ${routing.tiers.fast}. Main tier: ${routing.tiers.main}.`;
}
function renderSettings() {
  $$("[data-cfg]").forEach(el => {
    const v = getPath(CONFIG, el.dataset.cfg);
    if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? "";
  });
}
async function saveSettings() {
  $("#settings-save").disabled = true; $("#settings-saved").textContent = "saving…";
  const cfg = JSON.parse(JSON.stringify(CONFIG));
  $$("[data-cfg]").forEach(el => setPath(cfg, el.dataset.cfg, el.type === "checkbox" ? el.checked : (el.type === "number" ? +el.value : el.value)));
  const chosen = document.querySelector('input[name="backend"]:checked');
  if (chosen) cfg.backend = chosen.value;
  try {
    const r = await (await fetch("/api/config", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(cfg) })).json();
    if (!r.ok) throw new Error(r.msg || "save failed");
    showModelRouting(r.model_routing);
    CONFIG = r.config; $("#settings-saved").textContent = "saved ✓";
    refreshStatus(); setTimeout(() => $("#settings-saved").textContent = "", 2500);
  } catch (e) { $("#settings-saved").textContent = "\u26a0 " + e.message; }
  finally { $("#settings-save").disabled = false; }
}
$("#settings-save").onclick = saveSettings;

// ================= JOBS =================
let pollTimer = null;
let SEARCH_ID = null;
async function startFetch() {
  $("#fetch-btn").disabled = true; $("#fetch-panel").classList.remove("hidden");
  $("#fetch-title").innerHTML = '<span class="spin"></span>Fetching + scoring…';
  try {
    const r = await fetch("/api/fetch", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ score: true }) });
    if (r.status !== 200) { const d = await r.json(); $("#fetch-title").textContent = "\u26a0 " + (d.msg || "could not start"); $("#fetch-btn").disabled = false; return; }
  } catch (e) { $("#fetch-title").textContent = "\u26a0 " + e.message; $("#fetch-btn").disabled = false; return; }
  clearInterval(pollTimer); pollTimer = setInterval(pollLog, 1200);
}
function resumeFetch() {   // page was reloaded while a fetch was running
  $("#fetch-btn").disabled = true; $("#fetch-panel").classList.remove("hidden");
  $("#fetch-title").innerHTML = '<span class="spin"></span>Fetching + scoring…';
  clearInterval(pollTimer); pollTimer = setInterval(pollLog, 1200); pollLog();
}
async function pollLog() {
  let d;
  try { d = await (await fetch("/api/fetch/log")).json(); } catch { return; }
  SEARCH_ID = d.workflow_id;
  $("#fetch-cancel").disabled = !d.running || d.cancelling;
  $("#fetch-cancel").textContent = d.cancelling ? "Cancelling…" : "Cancel search";
  $("#fetch-log").textContent = d.log.join("\n"); $("#fetch-log").scrollTop = 1e9;
  if (!d.running) {
    clearInterval(pollTimer); $("#fetch-btn").disabled = false;
    $("#fetch-title").textContent = d.error ? "Error: " + d.error : `Done — ${d.count} jobs.`;
    await loadJobs(); refreshStatus();
    // If results are weak, the CV is likely the problem — review + flag it.
    const scored = JOBS.filter(j => j.score != null);
    const top = Math.max(0, ...scored.map(j => j.score));
    if (scored.length && top < 60) {
      try { const rev = await runCvReview(); if ((rev.score || 0) < 65) { switchTab("profile"); showCvBanner(rev); $("#cv-quality").scrollIntoView({ behavior: "smooth" }); } } catch { }
    }
  }
}
async function loadJobs() {
  JOBS = await (await fetch("/api/jobs")).json();
  const regions = new Set(JOBS.map(j => j.region).filter(Boolean)), eligs = new Set(JOBS.map(j => j.level).filter(Boolean));
  $("#f-region").innerHTML = [...regions].map(r => `<span class="chip ${activeRegion.has(r) ? "on" : ""}" data-v="${esc(r)}">${esc(r)}</span>`).join("");
  $("#f-elig").innerHTML = [...eligs].map(e => `<span class="chip ${activeElig.has(e) ? "on" : ""}" data-v="${esc(e)}">${esc(e)}</span>`).join("");
  $$("#f-region .chip").forEach(c => c.onclick = () => { toggle(activeRegion, c.dataset.v); c.classList.toggle("on"); render(); });
  $$("#f-elig .chip").forEach(c => c.onclick = () => { toggle(activeElig, c.dataset.v); c.classList.toggle("on"); render(); });
  render();
}
const toggle = (s, v) => (s.has(v) ? s.delete(v) : s.add(v));
function render() {
  const q = $("#f-search").value.toLowerCase(), minS = +$("#f-score").value;
  const rows = JOBS.filter(j => (j.score ?? 0) >= minS &&
    (!q || (j.title + " " + j.company).toLowerCase().includes(q)) &&
    (!activeRegion.size || activeRegion.has(j.region)) && (!activeElig.size || activeElig.has(j.level)));
  $("#count-line").textContent = `${rows.length} of ${JOBS.length} shown`;
  const body = $("#jobs-body");
  if (!rows.length) { body.innerHTML = `<tr><td colspan="6" class="empty">${JOBS.length ? "No jobs match." : "No jobs yet. Set up your <b>Profile</b>, then hit <b>Fetch</b>."}</td></tr>`; return; }
  body.innerHTML = rows.map(j => `<tr data-id="${j.id}">
    <td><span class="score ${scoreClass(j.score)}">${scoreText(j.score)}</span></td>
    <td><span class="jt">${esc(j.title)}</span>${j.level && j.level !== "general" ? `<span class="badge">${esc(j.level)}</span>` : ""}</td>
    <td>${esc(j.company)}</td><td>${esc(j.location)}</td>
    <td class="muted small">${esc(j.score_reason || "")}</td><td class="muted small">${esc(j.source)}</td></tr>`).join("");
  $$("#jobs-body tr").forEach(tr => tr.onclick = () => openDrawer(tr.dataset.id));
}
let CURRENT = null;
function openDrawer(id) {
  const j = JOBS.find(x => x.id === id); if (!j) return; CURRENT = j;
  $("#d-title").textContent = j.title; $("#d-meta").textContent = `${j.company} · ${j.location} · ${j.source}`;
  $("#d-badges").innerHTML = `<span class="score ${scoreClass(j.score)}">score ${scoreText(j.score)}</span> ` +
    (j.level && j.level !== "general" ? `<span class="badge">${esc(j.level)}</span>` : "") + (j.salary ? ` <span class="badge">${esc(j.salary)}</span>` : "");
  $("#d-apply").href = safeUrl(j.url); $("#d-desc").textContent = j.description || "(no description)";
  $("#d-gen").classList.add("hidden"); $("#d-genstatus").classList.add("hidden");
  HAS_PKG = false; $("#d-tailor").textContent = TAILOR_LABEL;     // opening a job never generates anything
  $("#drawer").classList.remove("hidden");
}
const TAILOR_LABEL = $("#d-tailor").textContent;
let HAS_PKG = false;
async function tailor() {
  if (!CURRENT) return; const st = $("#d-genstatus");
  st.className = "genstatus"; st.classList.remove("hidden");
  st.innerHTML = '<span class="spin"></span>Generating on your AI backend… (20–60s)'; $("#d-tailor").disabled = true;
  try {
    const d = await (await fetch("/api/tailor", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: CURRENT.id, force: HAS_PKG }) })).json();
    if (!d.ok) throw new Error(d.msg);
    const p = d.package;
    $("#g-summary").textContent = p.summary || "";
    $("#g-skills").innerHTML = (p.skills || []).map(s => `<li><b>${esc(s[0] || "")}</b>${esc(s[1] || "")}</li>`).join("");
    $("#g-projects").innerHTML = (p.projects || []).map(pr => `<li><b>${esc(pr.header || "")}</b><br>${(pr.bullets || []).map(esc).join("<br>")}</li>`).join("");
    $("#g-cover").textContent = p.cover_letter || "";
    const checks = p.source_checks || {};
    $("#g-review-notes").textContent = "Review before submitting: " + (checks.note || "Check all claims against your CV.") +
      (checks.warnings?.length ? "\n" + checks.warnings.join("\n") : "");
    const fit = p.fit || {};
    $("#g-fit").innerHTML = (fit.matches || []).map(m => `<div>✓ <b>${esc(m)}</b></div>`).join("") + (fit.gaps || []).map(g => `<div class="gap">△ <b>${esc(g)}</b></div>`).join("");
    $("#dl-cv").href = "/api/download?path=" + encodeURIComponent(p.cv_docx);
    $("#dl-cl").href = "/api/download?path=" + encodeURIComponent(p.cover_docx);
    $("#d-gen").classList.remove("hidden"); st.classList.add("hidden");
    HAS_PKG = true; $("#d-tailor").textContent = "Regenerate";
  } catch (e) { st.className = "genstatus err"; st.textContent = "⚠ " + e.message; }
  finally { $("#d-tailor").disabled = false; }
}

// ================= CV COACH =================
let LAST_REVIEW = null;
async function runCvReview() { const d = await (await fetch("/api/cv/review", { method: "POST" })).json(); if (!d.ok) throw new Error(d.msg); LAST_REVIEW = d.review; return d.review; }
function showCvBanner(rev) {
  const weak = (rev.score || 0) < 65;
  $("#cv-quality").classList.toggle("hidden", !weak);
  if (weak) { $("#cvq-title").textContent = `⚠ Your CV scores ${rev.score}/100 — it's holding back your matches`; $("#cvq-verdict").textContent = rev.verdict || ""; }
}
function openCvModal(rev) {
  $("#cvmodal-apply").classList.remove("hidden");
  const tagColor = rev.score >= 65 ? "var(--good)" : rev.score >= 45 ? "var(--warn)" : "#e5484d";
  $("#cvmodal-review").innerHTML = `<div class="cvq-review">
    <span class="tag" style="background:${tagColor};color:#06240f">CV score ${rev.score}/100</span>
    <div style="margin-bottom:8px">${esc(rev.verdict || "")}</div>
    ${rev.issues?.length ? `<b>Weaknesses</b><ul class="cvq-list">${rev.issues.map(i => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
    ${rev.missing?.length ? `<b>Missing</b><ul class="cvq-list">${rev.missing.map(i => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}</div>`;
  $("#cvmodal-questions").innerHTML = (rev.questions || []).map((q, i) =>
    `<div class="qitem"><label>${esc(q)}</label><textarea data-q="${i}" rows="2" data-qtext="${esc(q)}"></textarea></div>`).join("");
  $("#cvmodal-status").textContent = ""; $("#cvmodal").classList.remove("hidden");
}
async function checkCv(openModal) {
  const btn = $("#cv-check-btn"); const prev = btn.innerHTML; btn.innerHTML = '<span class="spin"></span>reviewing…'; btn.disabled = true;
  try { const rev = await runCvReview(); showCvBanner(rev); if (openModal) openCvModal(rev); }
  catch (e) { alert("CV review failed: " + e.message); }
  finally { btn.innerHTML = prev; btn.disabled = false; }
}
async function applyImprovements() {
  const qa = $$("#cvmodal-questions textarea").map(t => ({ question: t.dataset.qtext, answer: t.value.trim() })).filter(x => x.answer);
  if (!qa.length) { $("#cvmodal-status").textContent = "Answer at least one question first."; return; }
  $("#cvmodal-apply").disabled = true; $("#cvmodal-status").innerHTML = '<span class="spin"></span>rewriting your CV…';
  try {
    const d = await (await fetch("/api/cv/improve", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ qa }) })).json();
    if (!d.ok) throw new Error(d.msg);
    const fields = ["summary", "skills", "experience", "projects"];
    $("#cvmodal-review").innerHTML = '<p>Review the proposed changes. Your saved CV has not changed.</p>' +
      fields.filter(k => JSON.stringify(d.before[k]) !== JSON.stringify(d.profile[k])).map(k =>
        `<h3>${esc(k)}</h3><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px"><div><b>Before</b><pre style="white-space:pre-wrap">${esc(JSON.stringify(d.before[k], null, 2))}</pre></div><div><b>Proposed</b><pre style="white-space:pre-wrap">${esc(JSON.stringify(d.profile[k], null, 2))}</pre></div></div>`).join("") +
      `<p>${esc(d.source_checks.note)}</p>` + (d.source_checks.warnings || []).map(w => `<p>${esc(w)}</p>`).join("");
    $("#cvmodal-questions").innerHTML = '<button id="cv-accept" class="btn btn-primary">Accept and save</button> <button id="cv-reject" class="btn">Reject changes</button>';
    $("#cvmodal-status").textContent = "Awaiting your review.";
    $("#cvmodal-apply").classList.add("hidden");
    $("#cv-reject").onclick = () => $("#cvmodal").classList.add("hidden");
    $("#cv-accept").onclick = async () => {
      const btn = $("#cv-accept"); btn.disabled = true;
      try {
        const r = await (await fetch("/api/profile", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ ...d.profile, _base_hash: d.base_hash }) })).json();
        if (!r.ok) throw new Error(r.msg);
        renderProfile(r.profile); $("#cv-quality").classList.add("hidden"); $("#cvmodal").classList.add("hidden");
        $("#profile-saved").textContent = "Reviewed CV changes saved.";
      } catch (e) { $("#cvmodal-status").textContent = e.message; }
      finally { btn.disabled = false; }
    };
  } catch (e) { $("#cvmodal-status").textContent = "⚠ " + e.message; }
  finally { $("#cvmodal-apply").disabled = false; }
}
$("#cv-check-btn").onclick = () => checkCv(true);
$("#cvq-improve").onclick = () => LAST_REVIEW ? openCvModal(LAST_REVIEW) : checkCv(true);
$("#cvq-dismiss").onclick = () => $("#cv-quality").classList.add("hidden");
$("#cvmodal-close").onclick = () => $("#cvmodal").classList.add("hidden");
$("#cvmodal-apply").onclick = applyImprovements;
$("#cvmodal").onclick = e => { if (e.target.id === "cvmodal") $("#cvmodal").classList.add("hidden"); };

$("#fetch-btn").onclick = startFetch;
$("#fetch-cancel").onclick = async () => {
  await pollLog();
  const result = await (await fetch("/api/fetch/cancel", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ workflow_id: SEARCH_ID }) })).json();
  if (!result.ok) $("#fetch-title").textContent = result.msg;
  await pollLog();
};
$("#fetch-close").onclick = () => $("#fetch-panel").classList.add("hidden");
$("#drawer-close").onclick = () => $("#drawer").classList.add("hidden");
$("#drawer").onclick = e => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); };
$("#d-tailor").onclick = tailor;
$("#f-search").oninput = render;
$("#f-score").oninput = e => { $("#f-score-val").textContent = e.target.value; render(); };

(async () => {
  const st = await refreshStatus(); renderSettings(); await loadProfile(); await loadJobs();
  if (st && st.fetch && st.fetch.running) resumeFetch();
  // First run: if the profile isn't set up yet, open on the guided setup.
  if (!liveReady().ready) switchTab("profile");
})();
