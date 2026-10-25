// Sleeper UI: one input that runs the whole pipeline (draft -> build -> research -> picks), with
// everything else (trace, meter, topic editing, replay, prebuilt live runs, past runs) behind
// collapsed <details>. Talks only to the T14 API.

import { renderPicks } from "./cards.js";
import { Trace } from "./trace.js";
import { fmtInt, fmtTime, fmtUsd, getPointer, h, renderJson } from "./util.js";

const EVENT_TYPES = [
  "run_started", "status", "narration", "reasoning", "tool_call", "tool_result",
  "usage", "skeptic_review", "fact_check", "run_finished",
];

const $ = (id) => document.getElementById(id);
const state = {
  config: null, universes: [], runs: [], runId: null, source: null, lastSeq: -1, outputs: {},
  spend: null, busy: false, replaying: false,
};
const trace = new Trace($("trace"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Errors carry `status`, and `reason` when the server refused (400 detail = {reason, message}).
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

const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---- The status line, progress bar and errors under the input ------------------------------

function setStage(text, fraction = null) {
  $("q-status").textContent = text || "";
  $("q-status").hidden = !text;
  $("progress").hidden = fraction == null;
  if (fraction != null) $("progress-bar").style.width = `${Math.round(Math.min(1, Math.max(0, fraction)) * 100)}%`;
}

const REFUSAL_TITLES = {
  spend_guard: "Not started: spend limit",
  offline: "Not started: offline mode",
  no_universe: "Not started: universe not found",
  no_api_key: "Not started: no API key",
  web_model: "Not started: model has web access",
  other: "Not started",
};

const PLAIN = {
  spend_guard: "The spend limit is reached, so nothing was started.",
  offline: "Offline mode: this needs the network. Replays still work under More options.",
  no_universe: "That topic's data is missing, so nothing was started.",
  no_api_key: "No API key is set on the server, so the model can't be called.",
  web_model: "That model has web access, which Sleeper doesn't allow.",
};

function plainSentence(err) {
  if (PLAIN[err.reason]) return PLAIN[err.reason];
  if (!err.reason && err.status === 409) return "Something else is still running. Try again when it finishes.";
  if (!err.reason && !err.status) return "Can't reach the server.";
  const first = String(err.message || "Something went wrong.").split(/(?<=\.)\s/)[0];
  return first.length > 160 ? "Something went wrong. Details are under “How it's working”." : first;
}

// One plain sentence under the input; the titled box with the full message in "How it's working".
function showRefusal(err) {
  let title = REFUSAL_TITLES[err.reason] ?? "Not started";
  if (!err.reason && err.status === 409) title = "Not started: something is in progress";
  else if (!err.reason && err.status === 422) title = "Not started: invalid settings";
  else if (!err.reason && !err.status) title = "Not started: can't reach the server";
  $("launch-error-title").textContent = title;
  $("launch-error-msg").textContent = err.message;
  $("launch-error").hidden = false;
  $("d-how").hidden = false;
  $("q-error").textContent = plainSentence(err);
  $("q-error").hidden = false;
}

function hideRefusal() {
  $("launch-error").hidden = true;
  $("q-error").hidden = true;
}

// ---- The input: fine print with the worst-case cost --------------------------------------

const DRAFT_USD = 0.01; // drafting the searches is one small dev-model call (under a cent)
const shortModel = (id) => id.replace(/^.*\//, "");

function modelWorst(model, budget) {
  const m = state.config.models.find((x) => x.id === model);
  if (!m || model === state.config.fake_model) return 0;
  const dearest = state.config.models.reduce((a, x) => ((x.input_per_m ?? 0) > (a.input_per_m ?? 0) ? x : a), {});
  const pin = m.input_per_m ?? dearest.input_per_m ?? 0;
  const pout = m.output_per_m ?? dearest.output_per_m ?? 0;
  return (budget * (pin + state.config.output_share * pout)) / 1e6;
}

// The pipeline's own worst case: the draft call plus a research run on the default model and budget.
const askWorst = () => DRAFT_USD + modelWorst(state.config.default_model, state.config.default_token_budget);

function fillAsk() {
  const { config } = state;
  const reason = config.custom_topics_reason || config.live_disabled_reason;
  $("q").disabled = Boolean(reason) || state.busy;
  $("q-go").disabled = Boolean(reason) || state.busy;
  if (reason) {
    $("q-fine").textContent = `${reason} Replays of recorded runs are under More options.`;
    return;
  }
  const worst = askWorst();
  let text = `Uses ${shortModel(config.default_model)}; at most ${fmtUsd(worst)}.`;
  const s = state.spend;
  if (s && s.spent_usd + worst > s.guard_usd) text += " That would pass the spend limit, so it will be refused.";
  $("q-fine").textContent = text;
}

function setBusy(busy) {
  state.busy = busy;
  document.body.classList.toggle("busy", busy);
  fillAsk();
}

// ---- The pipeline: draft -> build -> run -> picks, no extra clicks --------------------------
// Submitting is the consent to the cost shown in the fine print.

async function discover(e) {
  e.preventDefault();
  if (state.busy) return;
  const text = $("q").value.trim();
  if (!text) return $("q").focus();
  hideRefusal();
  ntError(null);
  clearRun();
  setBusy(true);
  $("d-how").hidden = false;
  $("nt").hidden = false;
  $("nt-plan").hidden = true;
  $("nt-progress").hidden = true;
  try {
    setStage("Finding repos…", 0.03);
    const plan = await post("/api/topics/draft", { text });
    ntShowPlan(plan);
    if (!plan.total) throw Object.assign(new Error(plan.warning || "No repos match."), { reason: "other", plainText: `No repos match “${text}”. Try other words.` });
    await buildAndResearch({ label: plan.label, queries: plan.queries.map((q) => q.query) });
  } catch (err) {
    fail(err);
  }
}

// Shared by the input and by "Rebuild and research" in "How it's working". Throws on failure.
async function buildAndResearch(draft) {
  setBusy(true);
  setStage("Building the topic…", 0.1);
  const job = await post("/api/topics/build", draft);
  $("nt-plan").hidden = true;
  $("nt-progress").hidden = false;
  const universe = await ntFollow(job.id);
  setStage("Researching…", 0.4);
  const { run_id } = await post("/api/runs", {
    topic: universe.topic,
    universe_file: universe.file,
    model: state.config.default_model,
    max_picks: 5,
    token_budget: state.config.default_token_budget,
  });
  await loadRuns();
  openRun(run_id);
  // setBusy(false) once the run finishes (finishRun).
}

function fail(err) {
  setBusy(false);
  setStage("");
  showRefusal(err);
  if (err.plainText) $("q-error").textContent = err.plainText;
}

// ---- Custom topic details: drafted searches with counts, recount, rebuild -------------------

const NT_TITLES = {
  offline: "Offline mode",
  no_api_key: "No API key",
  spend_guard: "Spend limit reached",
  other: "Couldn't do that",
};

function ntError(err) {
  $("nt-error-title").textContent = err ? NT_TITLES[err.reason] ?? (err.status === 409 ? "Busy" : "Couldn't do that") : "";
  $("nt-error-msg").textContent = err ? err.message : "";
  $("nt-error").hidden = !err;
}

function ntQueryRow(query = "", total = null) {
  const count = h("span", { class: "nt-count", title: "Repos this search matches" }, total == null ? "–" : fmtInt(total));
  const input = h("input", {
    type: "text", value: query, maxlength: 150, "aria-label": "GitHub search",
    oninput: () => {
      count.textContent = "–";
      $("nt-summary").textContent = "Edited: recount to see how many repos match.";
    },
  });
  const row = h("div", { class: "nt-q" }, input, count);
  row.append(h("button", {
    type: "button", class: "nt-remove", title: "Remove this search", "aria-label": "Remove this search",
    onclick: () => row.remove(),
  }, "×"));
  return row;
}

function ntDraftFromForm() {
  return {
    label: $("nt-label").value.trim(),
    queries: [...document.querySelectorAll("#nt-queries input")].map((i) => i.value.trim()).filter(Boolean),
  };
}

function ntShowPlan(plan) {
  $("nt-label").value = plan.label;
  $("nt-queries").replaceChildren(...plan.queries.map((q) => ntQueryRow(q.query, q.total)));
  const cost = plan.cost_usd ? ` Suggestion cost ${fmtUsd(plan.cost_usd)}.` : "";
  $("nt-summary").textContent =
    `${fmtInt(plan.total)} matching repos (50–2,000 stars, created since 2025). Builds a topic of up to ` +
    `${plan.max_size}: about ${fmtInt(plan.est_requests)} GitHub requests, usually under a minute.${cost}`;
  $("nt-warning").textContent = plan.warning ?? "";
  $("nt-warning").hidden = !plan.warning;
  $("nt-plan").hidden = false;
}

async function ntRecount() {
  ntError(null);
  $("nt-recount").disabled = true;
  try {
    ntShowPlan(await post("/api/topics/count", ntDraftFromForm()));
  } catch (err) {
    ntError(err);
  } finally {
    $("nt-recount").disabled = false;
  }
}

async function ntRebuild() {
  if (state.busy) return;
  ntError(null);
  hideRefusal();
  clearRun();
  try {
    await buildAndResearch(ntDraftFromForm());
  } catch (err) {
    $("nt-plan").hidden = false;
    fail(err);
  }
}

// Polls a build until it is done; returns its UniverseInfo, or throws with the build's error.
async function ntFollow(jobId) {
  for (;;) {
    const job = await api(`/api/topics/build/${jobId}`);
    $("nt-bar").max = Math.max(job.to_fetch, 1);
    $("nt-bar").value = job.state === "done" ? $("nt-bar").max : job.fetched;
    $("nt-log").textContent = job.lines.slice(-8).join("\n");
    $("nt-log").scrollTop = $("nt-log").scrollHeight;
    if (job.state === "running") {
      $("nt-status").textContent = job.to_fetch
        ? `Building “${job.label}”: metrics for ${job.fetched} of ${job.to_fetch} repos…`
        : `Building “${job.label}”: searching GitHub…`;
      setStage("Building the topic…", 0.1 + 0.3 * (job.to_fetch ? job.fetched / job.to_fetch : 0));
      await sleep(1000);
      continue;
    }
    if (job.state === "error") {
      $("nt-status").textContent = "";
      $("nt-plan").hidden = false;
      throw Object.assign(new Error(job.error), { reason: "other", plainText: "Couldn't build the topic. Details are under “How it's working”." });
    }
    $("nt-status").textContent = `Built “${job.universe.topic_label}” with ${job.universe.rows} repos.`;
    state.universes = await api("/api/universes").catch(() => state.universes);
    syncModel();
    return job.universe;
  }
}

// ---- More options: prebuilt live runs (with a confirm step), replay, past runs ---------------

function fillLauncher() {
  const { config } = state;
  const reason = config.live_disabled_reason;
  $("live-disabled").textContent = reason || "";
  $("live-disabled").hidden = !reason;
  $("f-model").replaceChildren(
    ...config.models.map((m) => {
      const price = m.input_per_m == null ? "" : ` · $${m.input_per_m}/$${m.output_per_m} per M`;
      return h("option", { value: m.id }, m.label + price);
    }),
  );
  // Offline or without a key only the scripted fake model can run.
  $("f-model").value = reason ? config.fake_model : config.default_model;
  for (const o of $("f-model").options) o.disabled = Boolean(reason) && o.value !== config.fake_model;
  $("f-budget").value = config.default_token_budget;
  syncModel();
}

// Finished runs, newest first. Default: the newest with cards, else the recorded fake run.
function fillReplay() {
  const list = $("r-run");
  const keep = list.value;
  const done = state.runs.filter((r) => r.status !== "running");
  list.replaceChildren(
    ...done.map((r) =>
      h("option", { value: r.run_id }, `${r.topic} · ${shortModel(r.model)} · ${r.cards} picks · ${fmtTime(r.started_at)}`),
    ),
  );
  const fallback = done.find((r) => r.cards > 0) ?? done.find((r) => r.topic === "fake") ?? done[0];
  if (done.some((r) => r.run_id === keep)) list.value = keep;
  else if (fallback) list.value = fallback.run_id;
  $("r-start").disabled = !done.length;
  if (!done.length) list.replaceChildren(h("option", { value: "" }, "No finished runs yet"));
}

const isFake = () => $("f-model").value === state.config.fake_model;
const worstCase = () => modelWorst($("f-model").value, Number($("f-budget").value) || 0);

// The scripted fake model only knows the fake universe; real models only the real ones.
function syncModel() {
  const fake = isFake();
  const select = $("f-universe");
  const keep = select.value;
  const choices = state.universes.filter((u) => (u.topic === "fake") === fake);
  select.replaceChildren(
    ...choices.map((u) =>
      h("option", { value: u.file, dataset: { topic: u.topic } },
        `${u.topic_label} · ${u.rows} repos${u.topic.startsWith("custom-") ? " · custom" : ""}`),
    ),
  );
  if (choices.some((u) => u.file === keep)) select.value = keep;
  select.disabled = fake || !choices.length;
  $("f-budget").disabled = fake;
  $("f-hint").textContent = fake
    ? "Offline test: a scripted run on fake data. Free, no network."
    : choices.length
      ? "Real model calls on a prebuilt universe. This spends from the OpenRouter budget."
      : "No real universe is built yet (sleeper build-universe).";
  cancelConfirm();
  syncCost();
}

// Updates the cost text only; a changed setting cancels a pending confirm in its own handler.
function syncCost() {
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
  if (state.config.live_disabled_reason) return;
  try {
    state.spend = await api("/api/spend");
  } catch {
    state.spend = null;
  }
  syncCost();
  fillAsk();
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

// Step 1: a live run from More options asks first; the free offline test starts at once.
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

// Step 2: POST. A refusal (spend guard, offline, ...) shows under the input; no run exists.
async function postRun() {
  // A paid run from here only ever starts from the confirm box (askToStart shows it first).
  if (!isFake() && $("f-confirm").hidden) return;
  if (state.busy) return;
  const buttons = [$("f-start"), $("f-go")];
  for (const b of buttons) b.disabled = true;
  try {
    const { run_id } = await post("/api/runs", liveSettings());
    cancelConfirm();
    await loadRuns();
    setBusy(true);
    openRun(run_id);
    toTop();
  } catch (err) {
    cancelConfirm();
    showRefusal(err);
    toTop();
  } finally {
    for (const b of buttons) b.disabled = false;
    syncCost();
  }
}

function startReplay(e) {
  e.preventDefault();
  const runId = $("r-run").value;
  if (!runId) return;
  hideRefusal();
  openRun(runId, "replay", $("r-speed").value);
  toTop();
}

function toTop() {
  $("d-more").open = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

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
          {
            type: "button",
            class: `run-item${r.run_id === state.runId ? " active" : ""}`,
            onclick: () => {
              hideRefusal();
              openRun(r.run_id);
              toTop();
            },
          },
          h("span", { class: "run-top" }, h("span", { class: "run-topic" }, r.topic), h("span", { class: `pill status-${r.status}` }, r.status)),
          h("span", { class: "run-sub" }, `${fmtTime(r.started_at)} · ${shortModel(r.model)}${r.status === "running" ? "" : ` · ${r.cards} picks`}`),
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

const PHASE_TEXT = {
  wrapping_up: "Wrapping up…",
  submit_only: "Wrapping up…",
  reviewing: "Double-checking the picks…",
  fact_checking: "Checking the facts…",
};

function setStatus(status) {
  const pill = $("run-status");
  pill.textContent = status === "ok" ? "" : status;
  pill.className = `pill status-${status}`;
  pill.hidden = !pill.textContent;
}

// Empties the results and their folds (not the custom-topic details).
function clearRun() {
  if (state.source) state.source.close();
  Object.assign(state, { runId: null, source: null });
  $("run").hidden = true;
  $("run-detail").hidden = true;
  $("d-summary").hidden = true;
  $("d-ranking").hidden = true;
  document.body.classList.remove("has-results");
}

async function openRun(runId, mode = "events", speed = null) {
  if (state.source) state.source.close();
  closeDrawer();
  Object.assign(state, { runId, source: null, lastSeq: -1, outputs: {}, replaying: mode === "replay" });
  if (speed != null) $("speed").value = speed;
  history.replaceState(null, "", `#run=${encodeURIComponent(runId)}`);
  document.body.classList.add("has-results");
  $("run").hidden = false;
  $("d-how").hidden = false;
  $("run-detail").hidden = false;
  $("d-summary").hidden = true;
  $("d-ranking").hidden = true;
  trace.reset();
  $("cards").replaceChildren();
  $("ranking").replaceChildren();
  $("summary").hidden = true;
  $("replay").disabled = true;
  $("run-title").textContent = "";
  setStatus("ok");
  setStage(mode === "replay" ? "Replaying…" : "Researching…", 0.4);
  loadRuns().catch(() => {});

  let info;
  try {
    info = await api(`/api/runs/${encodeURIComponent(runId)}`);
  } catch (err) {
    $("run-title").textContent = runId;
    $("run-meta").textContent = err.message;
    if (runId === state.runId) fail(err);
    return;
  }
  if (runId !== state.runId) return;
  $("run-title").textContent = info.topic;
  $("run-meta").textContent = `${info.run_id} · ${info.model}`;
  if (mode !== "replay" && info.status !== "running") setStage("Loading…", 0.9);
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
  const verb = state.replaying ? "Replaying…" : "Researching…";
  if (ev.type === "run_started") {
    resetMeter(ev.settings);
    $("run-title").textContent = ev.universe.topic_label;
  }
  if (ev.type === "tool_result") state.outputs[ev.call_id] = { tool: ev.output.tool, output: ev.output };
  if (ev.type === "usage") {
    updateMeter(ev);
    const share = Math.max(ev.turn / ev.max_turns, ev.usage.total_tokens / ev.token_budget);
    setStage($("q-status").textContent || verb, 0.4 + 0.5 * Math.min(1, share));
  }
  if (ev.type === "status") {
    const text = PHASE_TEXT[ev.phase] ?? (ev.phase === "researching" ? verb : null);
    if (text) setStage(text, Number.parseFloat($("progress-bar").style.width) / 100 || 0.4);
  }
  if (ev.type === "run_finished") setStatus(ev.status);
  trace.add(ev);
}

async function finishRun(runId) {
  if (runId !== state.runId) return;
  $("replay").disabled = false;
  loadRuns().catch(() => {});
  try {
    const result = await api(`/api/runs/${encodeURIComponent(runId)}/result`);
    if (runId !== state.runId) return;
    setStatus(result.status);
    renderPicks(result, { cardsEl: $("cards"), rankingEl: $("ranking"), summaryEl: $("summary"), open: openSource });
    $("d-summary").hidden = $("summary").hidden;
    $("d-ranking").hidden = !$("ranking").childElementCount;
    const n = result.cards.length;
    setStage(n ? `Done · ${n} pick${n === 1 ? "" : "s"}` : "Done · no picks this time", null);
  } catch (err) {
    $("cards").replaceChildren(h("p", { class: "error" }, err.message));
    setStage("");
  } finally {
    setBusy(false);
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
  $("ask").addEventListener("submit", discover);
  $("replay-form").addEventListener("submit", startReplay);
  $("launcher").addEventListener("submit", askToStart);
  $("f-go").addEventListener("click", postRun);
  $("f-cancel").addEventListener("click", cancelConfirm);
  $("f-model").addEventListener("change", syncModel);
  $("f-universe").addEventListener("change", cancelConfirm);
  $("f-budget").addEventListener("input", () => {
    cancelConfirm();
    syncCost();
  });
  $("f-max-picks").addEventListener("input", cancelConfirm);
  $("d-more").addEventListener("toggle", () => $("d-more").open && loadSpend());
  // Enter inside the topic editor must never submit anything around it.
  $("nt").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") e.preventDefault();
  });
  $("nt-recount").addEventListener("click", ntRecount);
  $("nt-build").addEventListener("click", ntRebuild);
  $("nt-add").addEventListener("click", () => $("nt-queries").append(ntQueryRow()));
  $("replay").addEventListener("click", () => {
    if (!state.runId) return;
    hideRefusal();
    openRun(state.runId, "replay");
  });
  $("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

  try {
    [state.config, state.universes] = await Promise.all([api("/api/config"), api("/api/universes")]);
    fillLauncher();
    fillAsk();
    await loadRuns();
  } catch (err) {
    $("q").disabled = $("q-go").disabled = true;
    $("q-error").textContent = `Can't reach the server: ${err.message}`;
    $("q-error").hidden = false;
    return;
  }
  loadSpend();
  if (!$("q").disabled) $("q").focus();
  // Deep link: #run=<id> opens that run.
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get("run")) openRun(params.get("run"));
}

init();
