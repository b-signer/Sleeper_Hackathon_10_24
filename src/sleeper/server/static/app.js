// Sleeper UI (T15 launcher, trace and budget meter; T16 cards). Talks only to the T14 API.

import { renderPicks } from "./cards.js";
import { Trace } from "./trace.js";
import { fmtInt, fmtTime, fmtUsd, getPointer, h, renderJson } from "./util.js";

const EVENT_TYPES = [
  "run_started", "status", "narration", "reasoning", "tool_call", "tool_result",
  "usage", "skeptic_review", "fact_check", "run_finished",
];

const $ = (id) => document.getElementById(id);
const state = { config: null, universes: [], runs: [], runId: null, source: null, lastSeq: -1, outputs: {}, tab: null };
const trace = new Trace($("trace"));

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
  return body;
}

// ---- Launcher ----------------------------------------------------------------

function fillLauncher() {
  const { config, universes } = state;
  $("offline-pill").hidden = !config.offline;
  $("f-universe").replaceChildren(
    ...universes.map((u) => h("option", { value: u.file, dataset: { topic: u.topic } }, `${u.topic_label} · ${u.rows} repos`)),
  );
  $("f-model").replaceChildren(
    ...config.models.map((m) => {
      const price = m.input_per_m == null ? "" : ` · $${m.input_per_m}/$${m.output_per_m} per M`;
      return h("option", { value: m.id, disabled: config.offline && !m.offline_ok }, m.label + price);
    }),
  );
  $("f-model").value = config.default_model;
  syncModel();
}

// The scripted fake model only knows the fake universe.
function syncModel() {
  const fake = $("f-model").value === state.config.fake_model;
  if (fake) {
    const u = state.universes.find((x) => x.topic === "fake");
    if (u) $("f-universe").value = u.file;
  }
  $("f-universe").disabled = fake;
  $("f-hint").textContent = fake ? "Scripted run on fake data: offline and free." : "Real model calls: this spends from the OpenRouter budget.";
}

async function startRun(e) {
  e.preventDefault();
  const select = $("f-universe");
  const settings = {
    topic: select.selectedOptions[0]?.dataset.topic,
    universe_file: select.value,
    model: $("f-model").value,
    stars_min: Number($("f-stars-min").value),
    stars_max: Number($("f-stars-max").value),
    max_picks: Number($("f-max-picks").value),
  };
  const button = $("f-start");
  button.disabled = true;
  $("launch-error").hidden = true;
  try {
    const { run_id } = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    await loadRuns();
    openRun(run_id);
  } catch (err) {
    $("launch-error").textContent = err.message;
    $("launch-error").hidden = false;
  } finally {
    button.disabled = false;
  }
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

async function openRun(runId, mode = "events") {
  if (state.source) state.source.close();
  closeDrawer();
  Object.assign(state, { runId, source: null, lastSeq: -1, outputs: {} });
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
  $("launcher").addEventListener("submit", startRun);
  $("f-model").addEventListener("change", syncModel);
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
    $("launch-error").textContent = `Can't reach the server: ${err.message}`;
    $("launch-error").hidden = false;
    return;
  }
  // Deep link: #run=<id>, optionally &tab=trace|picks to stay on that tab.
  const params = new URLSearchParams(location.hash.slice(1));
  if (["trace", "picks"].includes(params.get("tab"))) state.tab = params.get("tab");
  if (params.get("run")) openRun(params.get("run"));
}

init();
