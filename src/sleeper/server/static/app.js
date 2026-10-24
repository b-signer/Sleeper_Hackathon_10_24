// Sleeper UI (T15 launcher, trace and budget meter; T16 cards). Talks only to the T14 API.

import { renderPicks } from "./cards.js";
import { Trace } from "./trace.js";
import { fmtInt, fmtTime, fmtUsd, getPointer, h, renderJson } from "./util.js";

const EVENT_TYPES = [
  "run_started", "status", "narration", "reasoning", "tool_call", "tool_result",
  "usage", "skeptic_review", "fact_check", "run_finished",
];

const $ = (id) => document.getElementById(id);
const state = {
  config: null, universes: [], runs: [], runId: null, source: null, lastSeq: -1, outputs: {}, tab: null,
  mode: "replay", spend: null,
};
const trace = new Trace($("trace"));

// Errors carry `status`, and `reason` when the server refused a run (400 detail = {reason, message}).
async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body.detail;
    let message = `${res.status} ${res.statusText}`;
    if (typeof d === "string") message = d;
    else if (Array.isArray(d)) message = d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ");
    else if (d && typeof d.message === "string") message = d.message;
    const err = new Error(message);
    err.status = res.status;
    err.reason = d && typeof d === "object" && !Array.isArray(d) ? d.reason ?? null : null;
    throw err;
  }
  return body;
}

// ---- Launcher: Replay (default, free) or Live run (real model, costs money) ------------

const REFUSAL_TITLES = {
  spend_guard: "Not started: spend limit",
  offline: "Not started: offline mode",
  no_universe: "Not started: universe not found",
  no_api_key: "Not started: no API key",
  web_model: "Not started: model has web access",
  other: "Not started",
};

function showRefusal(err) {
  let title = REFUSAL_TITLES[err.reason] ?? "Not started";
  if (!err.reason && err.status === 409) title = "Not started: a run is in progress";
  else if (!err.reason && err.status === 422) title = "Not started: invalid settings";
  else if (!err.reason && !err.status) title = "Not started: can't reach the server";
  $("launch-error-title").textContent = title;
  $("launch-error-msg").textContent = err.message;
  $("launch-error").hidden = false;
}

function hideRefusal() {
  $("launch-error").hidden = true;
}

function setMode(mode) {
  if (mode === "live" && state.config.live_disabled_reason) mode = "replay";
  state.mode = mode;
  for (const [id, m] of [["mode-replay", "replay"], ["mode-live", "live"]]) {
    $(id).classList.toggle("active", mode === m);
    $(id).setAttribute("aria-selected", String(mode === m));
  }
  $("replay-form").hidden = mode !== "replay";
  $("launcher").hidden = mode !== "live";
  cancelConfirm();
  hideRefusal();
  if (mode === "live") loadSpend();
}

function fillLauncher() {
  const { config } = state;
  $("offline-pill").hidden = !config.offline;
  const reason = config.live_disabled_reason;
  $("mode-live").disabled = Boolean(reason);
  $("mode-live").title = reason || "";
  $("live-disabled").textContent = reason || "";
  $("live-disabled").hidden = !reason;
  $("f-model").replaceChildren(
    ...config.models.map((m) => {
      const price = m.input_per_m == null ? "" : ` · $${m.input_per_m}/$${m.output_per_m} per M`;
      return h("option", { value: m.id }, m.label + price);
    }),
  );
  $("f-model").value = config.default_model;
  $("f-budget").value = config.default_token_budget;
  syncModel();
  setMode("replay");
}

// Finished runs, newest first. Default: the newest with cards, else the recorded fake run.
function fillReplay() {
  const list = $("r-run");
  const keep = list.value;
  const done = state.runs.filter((r) => r.status !== "running");
  list.replaceChildren(
    ...done.map((r) =>
      h("option", { value: r.run_id }, `${r.topic} · ${r.model.replace(/^.*\//, "")} · ${r.cards} picks · ${fmtTime(r.started_at)}`),
    ),
  );
  const fallback = done.find((r) => r.cards > 0) ?? done.find((r) => r.topic === "fake") ?? done[0];
  if (done.some((r) => r.run_id === keep)) list.value = keep;
  else if (fallback) list.value = fallback.run_id;
  $("r-start").disabled = !done.length;
  if (!done.length) list.replaceChildren(h("option", { value: "" }, "No finished runs yet"));
}

const modelInfo = () => state.config.models.find((m) => m.id === $("f-model").value);
const isFake = () => $("f-model").value === state.config.fake_model;

// Same formula as sleeper.agent.spend.estimate_max_cost (unknown prices: the dearest known
// model's). The server checks the guard again when the run starts.
function worstCase() {
  const m = modelInfo();
  if (!m || isFake()) return 0;
  const budget = Number($("f-budget").value) || 0;
  const dearest = state.config.models.reduce((a, x) => ((x.input_per_m ?? 0) > (a.input_per_m ?? 0) ? x : a), {});
  const pin = m.input_per_m ?? dearest.input_per_m ?? 0;
  const pout = m.output_per_m ?? dearest.output_per_m ?? 0;
  return (budget * (pin + state.config.output_share * pout)) / 1e6;
}

// The scripted fake model only knows the fake universe; real models only the real ones.
function syncModel() {
  const fake = isFake();
  const select = $("f-universe");
  const keep = select.value;
  const choices = state.universes.filter((u) => (u.topic === "fake") === fake);
  select.replaceChildren(
    ...choices.map((u) => h("option", { value: u.file, dataset: { topic: u.topic } }, `${u.topic_label} · ${u.rows} repos`)),
  );
  if (choices.some((u) => u.file === keep)) select.value = keep;
  select.disabled = fake || !choices.length;
  $("f-budget").disabled = fake;
  $("f-hint").textContent = fake
    ? "Offline test: a scripted run on fake data. Free, no network."
    : choices.length
      ? "Real model calls on a prebuilt universe. This spends from the OpenRouter budget."
      : "No real universe is built yet (sleeper build-universe).";
  syncCost();
}

function syncCost() {
  cancelConfirm();
  const cost = worstCase();
  $("f-start").textContent = isFake() ? "Start offline test (free)" : `Start live run · up to ${fmtUsd(cost)}`;
  $("f-start").disabled = !isFake() && !state.universes.some((u) => u.topic !== "fake");
  const s = state.spend;
  if (isFake() || !s) {
    $("f-spend").textContent = "";
    return;
  }
  const over = s.spent_usd + cost > s.guard_usd;
  $("f-spend").textContent =
    `Spent so far: ${fmtUsd(s.spent_usd)} of the ${fmtUsd(s.guard_usd)} guard` +
    (s.source === "local" ? " (local records; OpenRouter unreachable)" : "") +
    (over ? ". This run would go past the guard and will be refused." : ".");
  $("f-spend").classList.toggle("error", over);
}

async function loadSpend() {
  try {
    state.spend = await api("/api/spend");
  } catch {
    state.spend = null;
  }
  syncCost();
}

function liveSettings() {
  const select = $("f-universe");
  return {
    topic: select.selectedOptions[0]?.dataset.topic,
    universe_file: select.value,
    model: $("f-model").value,
    max_picks: Number($("f-max-picks").value),
    ...(isFake() ? {} : { token_budget: Number($("f-budget").value) }),
  };
}

// Step 1: a live run asks first; the free offline test starts at once.
function askToStart(e) {
  e.preventDefault();
  hideRefusal();
  if (isFake()) return postRun();
  const s = liveSettings();
  $("f-confirm-text").textContent =
    `Start a live run of ${s.model} on ${s.topic}? It makes real model calls and can cost up to ` +
    `${fmtUsd(worstCase())} (token budget ${fmtInt(s.token_budget)}).`;
  $("f-start").hidden = true;
  $("f-confirm").hidden = false;
  $("f-go").focus();
}

function cancelConfirm() {
  $("f-confirm").hidden = true;
  $("f-start").hidden = false;
}

// Step 2: POST. A refusal (spend guard, offline, ...) shows as a titled box; no run exists.
async function postRun() {
  const buttons = [$("f-start"), $("f-go")];
  for (const b of buttons) b.disabled = true;
  try {
    const { run_id } = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(liveSettings()),
    });
    cancelConfirm();
    await loadRuns();
    openRun(run_id);
  } catch (err) {
    cancelConfirm();
    showRefusal(err);
  } finally {
    for (const b of buttons) b.disabled = false;
    syncCost();
  }
}

function startReplay(e) {
  e.preventDefault();
  const runId = $("r-run").value;
  if (runId) openRun(runId, "replay", $("r-speed").value);
}

// ---- Run list ------------------------------------------------------------------

async function loadRuns() {
  state.runs = await api("/api/runs");
  const list = $("run-list");
  list.replaceChildren(
    ...state.runs.map((r) =>
      h(
        "li",
        {},
        h(
          "button",
          { type: "button", class: `run-item${r.run_id === state.runId ? " active" : ""}`, onclick: () => openRun(r.run_id) },
          h("span", { class: "run-top" }, h("span", { class: "run-topic" }, r.topic), h("span", { class: `pill status-${r.status}` }, r.status)),
          h("span", { class: "run-sub" }, `${fmtTime(r.started_at)} · ${r.model}${r.status === "running" ? "" : ` · ${r.cards} picks`}`),
        ),
      ),
    ),
  );
  if (!state.runs.length) list.append(h("li", { class: "muted" }, "No runs yet."));
  fillReplay();
}

// ---- Budget meter ----------------------------------------------------------------

function resetMeter(settings) {
  $("m-tokens").textContent = `0 / ${fmtInt(settings?.token_budget)}`;
  $("m-turns").textContent = `0 / ${settings?.max_turns ?? "—"}`;
  $("m-cost").textContent = fmtUsd(0);
  $("m-bar").style.width = "0%";
  $("meter").classList.remove("warn");
}

function updateMeter(ev) {
  const used = ev.usage.total_tokens;
  const share = Math.min(1, used / ev.token_budget);
  $("m-tokens").textContent = `${fmtInt(used)} / ${fmtInt(ev.token_budget)}`;
  $("m-turns").textContent = `${ev.turn} / ${ev.max_turns}`;
  $("m-cost").textContent = fmtUsd(ev.usage.cost_usd);
  $("m-bar").style.width = `${(share * 100).toFixed(1)}%`;
  $("meter").classList.toggle("warn", share >= 0.85);
}

// ---- Opening, streaming and replaying a run ----------------------------------------

function showTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.tab === name);
  $("tab-trace").hidden = name !== "trace";
  $("tab-picks").hidden = name !== "picks";
}

function setStatus(status) {
  const pill = $("run-status");
  pill.textContent = status;
  pill.className = `pill status-${status}`;
}

async function openRun(runId, mode = "events", speed = null) {
  if (state.source) state.source.close();
  closeDrawer();
  Object.assign(state, { runId, source: null, lastSeq: -1, outputs: {} });
  if (speed != null) $("speed").value = speed;
  history.replaceState(null, "", `#run=${encodeURIComponent(runId)}${state.tab ? `&tab=${state.tab}` : ""}`);
  $("empty").hidden = true;
  $("run").hidden = false;
  trace.reset();
  $("cards").replaceChildren();
  $("ranking").replaceChildren();
  $("summary").hidden = true;
  $("picks-count").textContent = "";
  $("replay").disabled = true;
  showTab("trace");
  for (const el of document.querySelectorAll(".run-item")) el.classList.remove("active");
  loadRuns();

  let info;
  try {
    info = await api(`/api/runs/${encodeURIComponent(runId)}`);
  } catch (err) {
    $("run-title").textContent = runId;
    $("run-meta").textContent = err.message;
    return;
  }
  $("run-title").textContent = info.topic;
  $("run-meta").textContent = `${info.run_id} · ${info.model}`;
  setStatus(mode === "replay" ? "replay" : info.status);
  resetMeter(null);

  const url =
    mode === "replay"
      ? `/api/runs/${encodeURIComponent(runId)}/replay?speed=${$("speed").value}`
      : `/api/runs/${encodeURIComponent(runId)}/events`;
  const source = new EventSource(url);
  state.source = source;
  const onEvent = (e) => handleEvent(JSON.parse(e.data));
  for (const t of EVENT_TYPES) source.addEventListener(t, onEvent);
  source.addEventListener("end", () => {
    source.close();
    finishRun(runId);
  });
  source.onerror = () => {
    // /events resumes after Last-Event-ID on its own; a replay would restart, so stop it instead.
    if (mode === "replay") source.close();
  };
}

function handleEvent(ev) {
  if (ev.seq <= state.lastSeq) return; // already shown (e.g. after a reconnect)
  state.lastSeq = ev.seq;
  if (ev.type === "run_started") {
    resetMeter(ev.settings);
    $("run-title").textContent = ev.universe.topic_label;
  }
  if (ev.type === "tool_result") state.outputs[ev.call_id] = { tool: ev.output.tool, output: ev.output };
  if (ev.type === "usage") updateMeter(ev);
  if (ev.type === "run_finished") setStatus(ev.status);
  trace.add(ev);
}

async function finishRun(runId) {
  if (runId !== state.runId) return;
  $("replay").disabled = false;
  loadRuns();
  try {
    const result = await api(`/api/runs/${encodeURIComponent(runId)}/result`);
    if (runId !== state.runId) return;
    setStatus(result.status);
    renderPicks(result, { cardsEl: $("cards"), rankingEl: $("ranking"), summaryEl: $("summary"), open: openSource });
    $("picks-count").textContent = result.cards.length ? String(result.cards.length) : "";
    if (state.tab) showTab(state.tab);
    else if (result.cards.length) showTab("picks");
  } catch (err) {
    $("cards").replaceChildren(h("p", { class: "error" }, err.message));
  }
}

// ---- Source drawer (click a number or citation) --------------------------------------

function openSource(callId, pointer, repo = null) {
  const entry = state.outputs[callId];
  // A score may cite a browse output, where it sits in the repo's row rather than at the top level.
  if (entry?.tool === "browse" && repo && pointer?.startsWith("/scores/")) {
    const i = entry.output.rows.findIndex((r) => r.repo.toLowerCase() === repo.toLowerCase());
    if (i >= 0) pointer = `/rows/${i}${pointer}`;
  }
  $("drawer-title").textContent = entry ? `${callId} · ${entry.tool}${entry.output.repo ? ` · ${entry.output.repo}` : ""}` : callId;
  const body = $("drawer-body");
  if (!entry) {
    body.replaceChildren(h("p", { class: "error" }, `${callId} is not in this run's tool outputs.`));
  } else {
    const { el, target } = renderJson(entry.output, pointer);
    const missing = pointer && getPointer(entry.output, pointer) === undefined;
    body.replaceChildren(
      pointer ? h("p", { class: missing ? "error" : "muted" }, missing ? `${pointer} is not in this output.` : `Highlighted: ${pointer}`) : "",
      el,
    );
    if (target) requestAnimationFrame(() => target.scrollIntoView({ block: "center" }));
  }
  $("drawer").hidden = false;
}

function closeDrawer() {
  $("drawer").hidden = true;
}

// ---- Start-up --------------------------------------------------------------------------

async function init() {
  $("mode-replay").addEventListener("click", () => setMode("replay"));
  $("mode-live").addEventListener("click", () => setMode("live"));
  $("replay-form").addEventListener("submit", startReplay);
  $("launcher").addEventListener("submit", askToStart);
  $("f-go").addEventListener("click", postRun);
  $("f-cancel").addEventListener("click", cancelConfirm);
  $("f-model").addEventListener("change", syncModel);
  $("f-universe").addEventListener("change", cancelConfirm);
  $("f-budget").addEventListener("input", syncCost);
  $("f-max-picks").addEventListener("input", cancelConfirm);
  $("replay").addEventListener("click", () => state.runId && openRun(state.runId, "replay"));
  $("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());
  for (const b of document.querySelectorAll(".tabs button")) {
    b.addEventListener("click", () => {
      state.tab = null; // a click is a one-off choice; the next run picks its own tab again
      showTab(b.dataset.tab);
    });
  }

  try {
    [state.config, state.universes] = await Promise.all([api("/api/config"), api("/api/universes")]);
    fillLauncher();
    await loadRuns();
  } catch (err) {
    $("launch-error-title").textContent = "Can't reach the server";
    $("launch-error-msg").textContent = err.message;
    $("launch-error").hidden = false;
    return;
  }
  // Deep link: #run=<id>, optionally &tab=trace|picks to stay on that tab.
  const params = new URLSearchParams(location.hash.slice(1));
  if (["trace", "picks"].includes(params.get("tab"))) state.tab = params.get("tab");
  if (params.get("run")) openRun(params.get("run"));
}

init();
