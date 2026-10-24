// T16 pick cards: every F4 field, the four criteria on one scale, citations that open their source.

import { fmtNum, h } from "./util.js";

const CRITERIA = [
  ["popularity", "Popularity"],
  ["momentum", "Momentum"],
  ["quality", "Quality"],
  ["newness", "Newness"],
];

const VERDICT_TEXT = { survives: "Survives skeptic", weakened: "Weakened by skeptic", killed: "Killed by skeptic" };

// `open(callId, pointer, repo?)` shows a cited tool output in the source drawer.
export function renderPicks(result, { cardsEl, rankingEl, summaryEl, open }) {
  summaryEl.textContent = result.summary || "";
  summaryEl.hidden = !result.summary;
  cardsEl.replaceChildren(...result.cards.map((c) => renderCard(c, open)));
  if (!result.cards.length) {
    cardsEl.append(h("p", { class: "muted" }, result.error ? `No picks: ${result.error}` : "No picks in this run."));
  }
  renderRanking(result, rankingEl);
}

function renderCard(card, open) {
  const verdict = card.skeptic?.verdict;
  const flags = card.fact_flags || [];
  const shown = new Set();
  const flagsAt = (path) => {
    const found = flags.filter((f) => f.card_path === path);
    found.forEach((f) => shown.add(f));
    return found.map((f) => {
      const text = f.found != null ? `⚠ ${f.shown} ≠ ${f.found}` : `⚠ ${f.shown} not in source`;
      return f.source_call_id
        ? h("button", { class: "flag", type: "button", title: `${f.message}. Click to see ${f.source_call_id}.`, onclick: () => open(f.source_call_id, f.source_path) }, text)
        : h("span", { class: "flag", title: f.message, tabindex: "0" }, text);
    });
  };

  const cite = (ids) =>
    ids.map((id) => h("button", { class: "cite", type: "button", title: `Show ${id}`, onclick: () => open(id, null) }, id));

  const el = h(
    "article",
    { class: `card${verdict === "killed" ? " killed" : ""}` },
    h(
      "header",
      { class: "card-head" },
      h("a", { class: "card-repo", href: `https://github.com/${card.repo}`, target: "_blank", rel: "noopener" }, card.repo),
      verdict && h("span", { class: `badge verdict-${verdict}` }, VERDICT_TEXT[verdict]),
    ),
    h("p", { class: "card-pitch" }, card.pitch),
    renderCriteria(card, open, flagsAt),
    h(
      "section",
      {},
      h("h4", {}, "The case"),
      h("ol", { class: "claims" }, card.case.map((c, i) => h("li", {}, c.text, " ", flagsAt(`case[${i}].text`), cite(c.source_call_ids)))),
    ),
    card.risks.length > 0 &&
      h(
        "section",
        {},
        h("h4", {}, "Risks"),
        h("ul", { class: "claims risks" }, card.risks.map((r, i) => h("li", {}, r.text, " ", flagsAt(`risks[${i}].text`), cite(r.source_call_ids)))),
      ),
    h("section", {}, h("h4", {}, "Proven wrong if"), h("ul", { class: "claims watch" }, card.proven_wrong_if.map((s) => h("li", {}, s)))),
    h(
      "footer",
      { class: "card-foot" },
      h("span", { class: "muted" }, "Confidence"),
      h("span", { class: "conf" }, h("span", { class: "conf-bar", style: `width:${Math.round(card.confidence * 100)}%` })),
      h("span", {}, `${Math.round(card.confidence * 100)}%`),
    ),
  );

  if (card.skeptic) {
    el.append(h("blockquote", { class: `skeptic verdict-${verdict}` }, h("strong", {}, "Skeptic: "), card.skeptic.argument, " ", cite(card.skeptic.cited_call_ids)));
  }
  const rest = flags.filter((f) => !shown.has(f));
  if (rest.length) {
    el.append(h("ul", { class: "flags-rest" }, rest.map((f) => h("li", {}, `⚠ ${f.card_path}: ${f.message}`))));
  }
  return el;
}

function renderCriteria(card, open, flagsAt) {
  const pop = card.criteria.popularity.score;
  const rows = CRITERIA.map(([key, label]) => {
    const r = card.criteria[key];
    const kf = r.key_figure;
    return h(
      "div",
      { class: `crit-row crit-${key}` },
      h("span", { class: "crit-label" }, label),
      h(
        "span",
        { class: "crit-track", title: `${label} score ${fmtNum(r.score)} of 100` },
        h("span", { class: "crit-bar", style: `width:${r.score}%` }),
        key !== "popularity" && h("span", { class: "crit-line", style: `left:${pop}%` }),
      ),
      h(
        "button",
        { class: "crit-score", type: "button", title: `Show the score in ${r.score_call_id}`, onclick: () => open(r.score_call_id, `/scores/${key}`, card.repo) },
        Math.round(r.score),
      ),
      h(
        "span",
        { class: "crit-fig" },
        h(
          "button",
          { class: "fig", type: "button", title: `${kf.label}: show it in ${kf.source_call_id}`, onclick: () => open(kf.source_call_id, kf.source_path) },
          fmtNum(kf.value),
          kf.unit && ` ${kf.unit}`,
        ),
        flagsAt(`criteria.${key}.key_figure.value`),
      ),
    );
  });
  return h(
    "div",
    { class: "crit" },
    rows,
    h("p", { class: "crit-key" }, h("span", { class: "crit-key-line" }), "popularity level: bars past it have more substance than attention"),
  );
}

function renderRanking(result, tableEl) {
  const verdicts = Object.fromEntries(result.cards.map((c) => [c.repo.toLowerCase(), c.skeptic?.verdict || "pick"]));
  tableEl.replaceChildren(
    h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Repo"), h("th", {}, ""), h("th", {}, "Note"))),
    h(
      "tbody",
      {},
      result.ranking.map((e) => {
        const v = verdicts[e.repo.toLowerCase()];
        return h(
          "tr",
          { class: v === "killed" ? "killed" : "" },
          h("td", { class: "num" }, e.rank),
          h("td", { class: "repo" }, e.repo),
          h("td", {}, v && h("span", { class: `badge verdict-${v === "pick" ? "pick" : v}` }, v === "pick" ? "Pick" : VERDICT_TEXT[v])),
          h("td", { class: "muted" }, e.note || ""),
        );
      }),
    ),
  );
}
