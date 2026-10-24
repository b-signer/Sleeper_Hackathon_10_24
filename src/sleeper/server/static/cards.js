// T16 pick cards. Collapsed: repo, pitch, confidence (plus one marker if the skeptic or the fact
// check objected). Expanded (native <details>): the four criteria, case, risks, "proven wrong if",
// skeptic verdict and fact-check flags, with every figure and citation opening its source.

import { fmtNum, h } from "./util.js";

const CRITERIA = [
  ["popularity", "Popularity"],
  ["momentum", "Momentum"],
  ["quality", "Quality"],
  ["newness", "Newness"],
];

const VERDICT_TEXT = { survives: "Survives the skeptic", weakened: "Weakened by the skeptic", killed: "Killed by the skeptic" };

const pct = (x) => `${Math.round((x ?? 0) * 100)}%`;

// `open(callId, pointer, repo?)` shows a cited tool output in the source drawer.
export function renderPicks(result, { cardsEl, rankingEl, summaryEl, open }) {
  summaryEl.classList.add("picks-summary");
  summaryEl.textContent = result.summary || "";
  summaryEl.hidden = !result.summary;

  cardsEl.classList.add("picks");
  cardsEl.replaceChildren(...result.cards.map((c) => renderCard(c, open)));
  if (!result.cards.length) {
    cardsEl.append(h("p", { class: "picks-empty" }, result.error ? `No picks: ${result.error}` : "No picks in this run."));
  }
  renderRanking(result, rankingEl);
}

// ---- One card ----

function renderCard(card, open) {
  const verdict = card.skeptic?.verdict;
  const flags = card.fact_flags || [];
  const shown = new Set();
  const flagsAt = (path) => {
    const found = flags.filter((f) => f.card_path === path);
    found.forEach((f) => shown.add(f));
    return found.map((f) => renderFlag(f, open));
  };

  // Build the body first so flagsAt() has marked every flag placed inline.
  const body = h(
    "div",
    { class: "pick-body" },
    renderCriteria(card, open, flagsAt),
    renderClaims("The case", card.case, "case", open, flagsAt),
    card.risks.length > 0 && renderClaims("Risks", card.risks, "risks", open, flagsAt),
    card.proven_wrong_if.length > 0 &&
      h("section", { class: "pick-sec" }, h("h4", {}, "Proven wrong if"), h("ul", { class: "pick-list" }, card.proven_wrong_if.map((s) => h("li", {}, s)))),
    card.skeptic && renderSkeptic(card.skeptic, open),
  );
  const rest = flags.filter((f) => !shown.has(f));
  if (rest.length) {
    body.append(
      h(
        "section",
        { class: "pick-sec" },
        h("h4", {}, "Fact check"),
        h("ul", { class: "pick-list pick-flags" }, rest.map((f) => h("li", {}, renderFlag(f, open), " ", h("span", { class: "pick-small" }, `${f.card_path}: ${f.message}`)))),
      ),
    );
  }

  return h(
    "details",
    { class: `pick${verdict ? ` pick-${verdict}` : ""}` },
    h(
      "summary",
      { class: "pick-sum" },
      h(
        "span",
        { class: "pick-line" },
        h("a", { class: "pick-repo", href: `https://github.com/${card.repo}`, target: "_blank", rel: "noopener" }, card.repo),
        renderMarker(verdict, flags),
        h("span", { class: "pick-conf", title: `Confidence ${pct(card.confidence)}` }, pct(card.confidence)),
      ),
      h("span", { class: "pick-pitch" }, card.pitch),
    ),
    body,
  );
}

// A single small symbol, only when the skeptic disagreed or the fact check found a mismatch.
function renderMarker(verdict, flags) {
  const why = [];
  if (verdict === "weakened" || verdict === "killed") why.push(VERDICT_TEXT[verdict]);
  if (flags.length) why.push(`${flags.length} fact-check flag${flags.length > 1 ? "s" : ""}`);
  if (!why.length) return null;
  const tone = verdict === "killed" ? "bad" : "warn";
  const label = why.join(" · ");
  return h("span", { class: `pick-mark pick-mark-${tone}`, title: label, "aria-label": label, role: "img" }, "!");
}

function renderFlag(f, open) {
  const text = f.found != null ? `${f.shown} ≠ ${f.found}` : `${f.shown} not in source`;
  return f.source_call_id
    ? h("button", { class: "pick-flag", type: "button", title: `${f.message}. Show ${f.source_call_id}.`, onclick: () => open(f.source_call_id, f.source_path) }, text)
    : h("span", { class: "pick-flag", title: f.message, tabindex: "0" }, text);
}

const cite = (ids, open) =>
  (ids || []).map((id) => h("button", { class: "pick-cite", type: "button", title: `Show ${id}`, onclick: () => open(id, null) }, id));

function renderClaims(title, claims, path, open, flagsAt) {
  return h(
    "section",
    { class: "pick-sec" },
    h("h4", {}, title),
    h("ul", { class: "pick-list" }, claims.map((c, i) => h("li", {}, c.text, " ", flagsAt(`${path}[${i}].text`), cite(c.source_call_ids, open)))),
  );
}

function renderSkeptic(sk, open) {
  return h(
    "section",
    { class: `pick-sec pick-skeptic pick-skeptic-${sk.verdict}` },
    h("h4", {}, VERDICT_TEXT[sk.verdict] || "Skeptic"),
    h("p", {}, sk.argument, " ", cite(sk.cited_call_ids, open)),
  );
}

// Four criteria on one 0-100 scale; the faint tick marks the popularity level on the other bars.
function renderCriteria(card, open, flagsAt) {
  const pop = card.criteria.popularity.score;
  const rows = CRITERIA.map(([key, label]) => {
    const r = card.criteria[key];
    const kf = r.key_figure;
    return h(
      "div",
      { class: `pick-crit-row pick-crit-${key}` },
      h("span", { class: "pick-crit-label" }, label),
      h(
        "span",
        { class: "pick-track", title: `${label} score ${fmtNum(r.score)} of 100` },
        h("span", { class: "pick-bar", style: `width:${r.score}%` }),
        key !== "popularity" && h("span", { class: "pick-tick", style: `left:${pop}%` }),
      ),
      h(
        "button",
        { class: "pick-score", type: "button", title: `Show the score in ${r.score_call_id}`, onclick: () => open(r.score_call_id, `/scores/${key}`, card.repo) },
        Math.round(r.score),
      ),
      h(
        "span",
        { class: "pick-fig" },
        h(
          "button",
          { class: "pick-fig-btn", type: "button", title: `${kf.label}: show it in ${kf.source_call_id}`, onclick: () => open(kf.source_call_id, kf.source_path) },
          fmtNum(kf.value),
          kf.unit && ` ${kf.unit}`,
        ),
        flagsAt(`criteria.${key}.key_figure.value`),
      ),
    );
  });
  return h(
    "section",
    { class: "pick-sec pick-crit" },
    rows,
    h("p", { class: "pick-small pick-crit-key" }, h("span", { class: "pick-tick-key" }), "popularity level: bars past it have more substance than attention"),
  );
}

// ---- Ranking: rank, repo, note ----

function renderRanking(result, listEl) {
  const killed = new Set(result.cards.filter((c) => c.skeptic?.verdict === "killed").map((c) => c.repo.toLowerCase()));
  const items = result.ranking.map((e) =>
    h(
      "li",
      { class: `picks-rank-item${killed.has(e.repo.toLowerCase()) ? " is-killed" : ""}` },
      h("span", { class: "picks-rank-num" }, e.rank),
      h("span", { class: "picks-rank-repo" }, e.repo),
      e.note && h("span", { class: "picks-rank-note" }, e.note),
    ),
  );
  if (/^(OL|UL)$/.test(listEl.tagName)) {
    listEl.classList.add("picks-rank");
    listEl.replaceChildren(...items);
  } else {
    listEl.replaceChildren(h("ol", { class: "picks-rank" }, items));
  }
}
