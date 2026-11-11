// Vanilla JS front end for the Statement Scrubber API (same origin, no framework).
// Everything that comes from a statement or from the model is put on the page with
// textContent, never innerHTML, so text inside a statement cannot inject markup.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { runId: null, analysis: null, currency: null, timer: null };

// ---------------------------------------------------------------- helpers
function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) node.append(kid);
  return node;
}

function fmt(n) {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function setStatus(msg, isErr = false) {
  const s = $("status");
  s.textContent = msg || "";
  s.className = "status" + (isErr ? " err" : "");
}

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (e) {
    throw new Error("Cannot reach the API. Is `uvicorn app.main:app` running?");
  }
  if (res.status === 204) return null;
  const type = res.headers.get("content-type") || "";
  const body = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = body && body.detail !== undefined ? body.detail : body;
    if (msg && typeof msg === "object") msg = msg.message || JSON.stringify(msg);
    const err = new Error(String(msg) || res.statusText);
    err.status = res.status;
    throw err;
  }
  return body;
}

function busy(btn, on, label) {
  if (on) { btn.dataset.label = btn.textContent; btn.textContent = label; btn.disabled = true; }
  else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; }
}

// ---------------------------------------------------------------- health + runs list
async function checkHealth() {
  try {
    const h = await api("/health");
    setStatus(h.database === "up" ? "API and PostgreSQL are up." : "API is up but PostgreSQL is not reachable.", h.database !== "up");
  } catch (e) { setStatus(e.message, true); }
}

async function loadRuns(selectId) {
  try {
    const runs = await api("/runs");
    const sel = $("runs");
    sel.replaceChildren(el("option", { value: "", text: runs.length ? "choose a run..." : "none yet" }));
    for (const r of runs) {
      const when = new Date(r.created_at).toLocaleString();
      sel.append(el("option", { value: r.run_id, text: `#${r.run_id}  ${when}  (${r.file_count} files)` }));
    }
    if (selectId) sel.value = String(selectId);
  } catch (e) { setStatus(e.message, true); }
}

// ---------------------------------------------------------------- 1. scrub
function renderRun(run) {
  state.runId = run.run_id;
  state.analysis = null;
  $("run-info").hidden = false;
  $("run-title").textContent = `Run #${run.run_id}: ${run.file_count} file(s) scrubbed and stored`;
  const dl = $("dl");
  dl.href = `/runs/${run.run_id}/combined`;
  dl.setAttribute("download", `run_${run.run_id}_scrubbed.txt`);

  const chips = $("chips");
  chips.replaceChildren();
  const counts = Object.entries(run.placeholder_counts || {}).sort((a, b) => b[1] - a[1]);
  if (!counts.length) chips.append(el("span", { class: "chip", text: "nothing removed" }));
  for (const [tag, n] of counts) chips.append(el("span", { class: "chip", text: `${tag} x ${n}` }));

  const tb = $("files-table").tBodies[0];
  tb.replaceChildren();
  for (const f of run.files || []) {
    const removed = Object.values(f.placeholder_counts || {}).reduce((a, b) => a + b, 0);
    tb.append(el("tr", {},
      el("td", { text: f.filename }), el("td", { text: f.file_type }),
      el("td", { text: f.status }), el("td", { class: "num", text: String(f.chars) }),
      el("td", { text: String(removed) })));
  }
  $("combined").textContent = run.combined_text || "";

  $("btn-delete").disabled = false;
  $("btn-analyze").disabled = false;
  $("s2").classList.remove("locked");
  resetAnalysis();
  resetChat();
}

async function openRun(id) {
  setStatus(`Loading run #${id}...`);
  try {
    renderRun(await api(`/runs/${id}`));
    $("runs").value = String(id);
    setStatus("");
    try { renderAnalysis(await api(`/runs/${id}/analysis`)); } catch (e) { if (e.status !== 404) throw e; }
    if (state.analysis) await loadChat();
  } catch (e) { setStatus(e.message, true); }
}

async function scrub(request, button, label) {
  busy(button, true, label);
  setStatus("Scrubbing...");
  try {
    const run = await api(request.path, request.options);
    renderRun(run);
    await loadRuns(run.run_id);
    setStatus(`Run #${run.run_id} saved. Next: analyse it.`);
  } catch (e) { setStatus(e.message, true); }
  finally { busy(button, false); }
}

$("btn-upload").addEventListener("click", () => {
  const files = $("files").files;
  if (!files.length) { setStatus("Choose one or more statement files first.", true); return; }
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  scrub({ path: "/runs", options: { method: "POST", body: fd } }, $("btn-upload"), "Scrubbing...");
});

$("btn-folder").addEventListener("click", () => {
  scrub({ path: "/runs/from-folder", options: {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder: "data" }) } },
    $("btn-folder"), "Scrubbing...");
});

$("runs").addEventListener("change", (e) => { if (e.target.value) openRun(e.target.value); });

$("btn-delete").addEventListener("click", async () => {
  if (!state.runId || !confirm(`Delete run #${state.runId} and its analysis and chat? This cannot be undone.`)) return;
  try {
    await api(`/runs/${state.runId}`, { method: "DELETE" });
    state.runId = null;
    $("run-info").hidden = true;
    $("btn-delete").disabled = true; $("btn-analyze").disabled = true;
    $("s2").classList.add("locked"); $("s3").classList.add("locked");
    resetAnalysis(); resetChat();
    await loadRuns();
    setStatus("Run deleted.");
  } catch (e) { setStatus(e.message, true); }
});

// ---------------------------------------------------------------- 2. analysis
function resetAnalysis() {
  state.analysis = null;
  $("an").hidden = true;
  $("an-msg").hidden = true;
  $("timer").textContent = "";
}

function showNotice(text, isErr) {
  const n = $("an-msg");
  n.textContent = text;
  n.className = "notice" + (isErr ? " err" : "");
  n.hidden = false;
}

$("btn-analyze").addEventListener("click", async () => {
  if (!state.runId) return;
  const btn = $("btn-analyze");
  busy(btn, true, "Analysing...");
  $("an-msg").hidden = true;
  const started = Date.now();
  $("timer").textContent = "0 s";
  state.timer = setInterval(() => { $("timer").textContent = `${Math.round((Date.now() - started) / 1000)} s`; }, 500);
  try {
    const mode = $("mode").value;
    const a = await api(`/runs/${state.runId}/analysis?mode=${encodeURIComponent(mode)}`, { method: "POST" });
    renderAnalysis(a);
    await loadChat();
    setStatus("Analysis stored. You can now chat about the data.");
  } catch (e) {
    showNotice(e.status === 429 ? e.message : `Analysis failed: ${e.message}`, true);
  } finally {
    clearInterval(state.timer);
    busy(btn, false);
  }
});

function chip(text, kind = "") { return el("span", { class: "chip " + kind, text }); }

function renderAnalysis(a) {
  state.analysis = a;
  const v = a.validation || {};
  const r = a.result || {};
  $("an").hidden = false;

  const chips = $("an-chips");
  chips.replaceChildren(
    chip(`mode: ${v.mode || "llm"}`), chip(`model: ${v.model || a.model}`),
    chip(`${v.kept ?? (r.transactions || []).length} transactions verified`, "ok"));
  if (v.rows_from_local_parser != null) chips.append(chip(`${v.rows_from_local_parser} read locally`));
  if (v.rows_from_model) chips.append(chip(`${v.rows_from_model} read by the model`));
  chips.append(chip(`${v.model_calls ?? 0} model call(s)`), chip(`${v.seconds_total ?? "?"} s`));
  const dropped = Object.entries(v).filter(([k, n]) => k.startsWith("dropped_") && n > 0);
  for (const [k, n] of dropped) chips.append(chip(`${k.replace("dropped_", "dropped: ").replaceAll("_", " ")} (${n})`, "warn"));
  if (v.insights_from_template) chips.append(chip("insights from template", "warn"));
  if ((v.injection_phrases_found || []).length) chips.append(chip("possible injection text in statement (ignored)", "warn"));

  if (v.llm_problem) {
    const why = { daily_limit: "Groq's daily limit was reached", no_api_key: "no GROQ_API_KEY is set", model_error: "the model returned unusable output" }[v.llm_problem] || "the model was unavailable";
    showNotice(`${why}, so this analysis was completed locally: rule-based categories and template insights.`, false);
  }

  const byCur = r.by_currency || {};
  const currencies = Object.keys(byCur).sort((x, y) => byCur[y].transactions - byCur[x].transactions);
  if (!currencies.includes(state.currency)) state.currency = currencies[0] || null;
  const tabs = $("tabs");
  tabs.replaceChildren();
  for (const c of currencies) {
    const b = el("button", { role: "tab", "aria-selected": String(c === state.currency), text: `${c} (${byCur[c].transactions})` });
    b.addEventListener("click", () => { state.currency = c; renderAnalysis(state.analysis); });
    tabs.append(b);
  }
  renderCurrency(r, state.currency);

  const ul = $("insights");
  ul.replaceChildren();
  for (const t of r.insights || []) ul.append(el("li", { text: t }));
  if (!(r.insights || []).length) ul.append(el("li", { class: "empty", text: "No insights passed the number check." }));

  const flags = $("flags");
  flags.replaceChildren();
  const dups = r.possible_duplicates || [];
  const revs = r.reversals_and_refunds || [];
  if (dups.length) {
    flags.append(el("h3", { text: "Possible duplicates (kept, flagged)" }));
    for (const d of dups) flags.append(el("div", { class: "small", text: `${d.date}: ${d.currency} ${fmt(d.amount)} listed ${d.times_listed}x` }));
  }
  if (revs.length) {
    flags.append(el("h3", { text: "Refunds and reversals (not counted as spending)" }));
    for (const x of revs) flags.append(el("div", { class: "small", text: `${x.date}: ${x.currency} ${fmt(x.amount)} (${x.direction})` }));
  }
}

function renderCurrency(r, cur) {
  const cards = $("cur-cards"), cats = $("cats"), tb = $("merch").tBodies[0];
  cards.replaceChildren(); cats.replaceChildren(); tb.replaceChildren();
  if (!cur) return;
  const b = r.by_currency[cur];
  const items = [["Income", b.income], ["Spending", b.spending], ["Money in", b.total_in], ["Money out", b.total_out], ["Net", b.net]];
  for (const [k, val] of items) cards.append(el("div", { class: "card" }, el("div", { class: "k", text: k }), el("div", { class: "v", text: `${cur} ${fmt(val)}` })));

  const list = (r.spending_by_category || {})[cur] || [];
  const max = Math.max(...list.map((c) => c.spent), 1);
  for (const c of list) {
    const bar = el("div", { class: "bar" }, el("i"));
    bar.firstChild.style.width = `${(100 * c.spent) / max}%`;
    cats.append(el("div", { class: "bar-row" },
      el("span", { text: c.category.replaceAll("_", " ") }), bar, el("span", { class: "num", text: `${fmt(c.spent)} (${c.share_pct}%)` })));
  }
  if (!list.length) cats.append(el("div", { class: "empty", text: "No spending in this currency." }));

  for (const m of ((r.top_merchants || {})[cur] || [])) {
    tb.append(el("tr", {}, el("td", { text: m.merchant }), el("td", { class: "num", text: `${m.times}x` }), el("td", { class: "num", text: fmt(m.spent) })));
  }
}

// ---------------------------------------------------------------- 3. chat
function resetChat() {
  $("chat-log").replaceChildren();
  const on = !!state.analysis;
  $("s3").classList.toggle("locked", !on);
  for (const id of ["q", "btn-send", "btn-clear"]) $(id).disabled = !on;
}

function addMsg(kind, text) {
  const m = el("div", { class: `msg ${kind}` }, el("div", { text }));
  const log = $("chat-log");
  log.append(m);
  log.scrollTop = log.scrollHeight;
  return m;
}

async function loadChat() {
  resetChat();
  if (!state.analysis) return;
  try {
    for (const m of await api(`/runs/${state.runId}/chat`)) addMsg(m.role === "user" ? "user" : "bot", m.content);
  } catch (e) { setStatus(e.message, true); }
}

function renderBotMeta(m, d) {
  const meta = el("div", { class: "meta" });
  meta.append(d.verified ? chip("numbers verified", "ok") : chip("could not verify the figures", "warn"), " ");
  if (!d.in_scope) meta.append(chip("not answerable from the data"), " ");
  meta.append(chip(`${d.history_turns_used} earlier turn(s) in context`));
  if (d.unverified_numbers_caught) meta.append(" ", chip(`${d.unverified_numbers_caught} wrong figure(s) caught`, "warn"));
  m.append(meta);
  const comp = Object.entries(d.computed || {});
  if (comp.length) {
    m.append(el("div", { class: "meta", text: "Computed by the app from the rows used: " +
      comp.map(([c, b]) => `${c}: ${b.count} transaction(s), out ${fmt(b.total_out)}, in ${fmt(b.total_in)}`).join("; ") }));
  }
  if ((d.sources || []).length) {
    const tbl = el("table", { class: "tbl" }, el("thead", {}, el("tr", {}, ...["Date", "Merchant", "Category", "Amount"].map((h) => el("th", { text: h })))));
    const body = el("tbody");
    for (const s of d.sources) {
      body.append(el("tr", {}, el("td", { text: s.date }), el("td", { text: s.merchant || "" }), el("td", { text: s.category }),
        el("td", { class: "num", text: `${s.direction === "credit" ? "+" : "-"}${s.currency} ${fmt(s.amount)}` })));
    }
    tbl.append(body);
    m.append(el("details", {}, el("summary", { text: `Rows used (${d.sources.length})` }), tbl));
  }
}

$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (!q || !state.runId) return;
  $("q").value = "";
  addMsg("user", q);
  const pending = addMsg("bot", "Thinking...");
  $("btn-send").disabled = true; $("q").disabled = true;
  try {
    const d = await api(`/runs/${state.runId}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
    pending.replaceChildren(el("div", { text: d.answer }));
    if (d.question_as_sent !== q) pending.append(el("div", { class: "meta", text: `Sent as: ${d.question_as_sent} (identifiers removed)` }));
    renderBotMeta(pending, d);
  } catch (err) {
    pending.replaceChildren(el("div", { text: err.status === 429 ? err.message : `Could not answer: ${err.message}` }));
  } finally {
    $("btn-send").disabled = false; $("q").disabled = false; $("q").focus();
    $("chat-log").scrollTop = $("chat-log").scrollHeight;
  }
});

$("btn-clear").addEventListener("click", async () => {
  if (!state.runId) return;
  try { await api(`/runs/${state.runId}/chat`, { method: "DELETE" }); $("chat-log").replaceChildren(); }
  catch (e) { setStatus(e.message, true); }
});

// ---------------------------------------------------------------- start
resetChat();
checkHealth();
loadRuns();
