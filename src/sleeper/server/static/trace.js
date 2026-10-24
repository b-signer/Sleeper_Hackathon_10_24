// T15 live trace: narration as a story, each tool call as one line that expands to its raw output.

import { fmtInt, h, renderJson } from "./util.js";

const OPS = { ">": ">", ">=": "≥", "<": "<", "<=": "≤", "==": "=", between: "in" };

const PHASES = {
  starting: "Starting",
  researching: "Researching",
  wrapping_up: "Budget running low: wrapping up",
  submit_only: "Budget spent: submit only",
  reviewing: "Skeptic review",
  fact_checking: "Fact check",
  finished: "Finished",
};

function describeCall(tool, args = {}) {
  if (tool === "browse") {
    const parts = (args.filters || []).map((f) => {
      const v = Array.isArray(f.value) ? f.value.join("–") : f.value;
      return `${f.field} ${OPS[f.op] || f.op} ${v}`;
    });
    if (args.keyword) parts.push(`“${args.keyword}”`);
    if (args.language) parts.push(args.language);
    const sort = args.sort_by ? `by ${args.sort_by}${args.sort_dir === "asc" ? " ↑" : " ↓"}` : "";
    return ["browse", [parts.join(", ") || "all repos", sort].filter(Boolean).join(" · ")];
  }
  if (tool === "repo_details") return ["repo details", args.repo];
  if (tool === "readme") return ["readme", args.repo];
  if (tool === "submit") {
    return ["submit", `${(args.cards || []).length} picks, ${(args.ranking || []).length} ranked`];
  }
  return [tool, JSON.stringify(args)];
}

function describeResult(out) {
  if (out.error) return { text: `${out.error.code}: ${out.error.message}`, kind: "err" };
  if (out.tool === "browse") {
    const names = out.rows.slice(0, 3).map((r) => r.repo).join(", ");
    const more = out.rows.length > 3 ? ", …" : "";
    return { text: `${out.total} match${out.total === 1 ? "" : "es"}${names ? `: ${names}${more}` : ""}` };
  }
  if (out.tool === "repo_details") {
    const m = out.metrics;
    const bits = [
      `${fmtInt(m.stars)}★`,
      m.forks_30d != null && `${m.forks_30d} forks/30d`,
      m.commits_90d != null && `${m.commits_90d} commits/90d`,
      m.authors_90d != null && `${m.authors_90d} author${m.authors_90d === 1 ? "" : "s"}`,
    ];
    return { text: bits.filter(Boolean).join(" · ") };
  }
  if (out.tool === "readme") return { text: `${fmtInt(out.total_chars)} chars${out.truncated ? " (truncated)" : ""}` };
  if (out.tool === "submit") {
    return out.ok ? { text: "accepted", kind: "ok" } : { text: `rejected: ${out.errors.join("; ")}`, kind: "err" };
  }
  return { text: "" };
}

export class Trace {
  constructor(listEl) {
    this.list = listEl;
    this.calls = new Map(); // call_id -> {row, result}
    this.lastTurn = 0;
  }

  reset() {
    this.list.replaceChildren();
    this.calls.clear();
    this.lastTurn = 0;
  }

  add(ev) {
    const stick = this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 60;
    const row = this.render(ev);
    if (row) this.list.append(row);
    if (stick) this.list.scrollTop = this.list.scrollHeight;
  }

  render(ev) {
    switch (ev.type) {
      case "run_started": {
        const u = ev.universe;
        return h("li", { class: "t-start" }, `Run started · ${ev.settings.model} · ${u.topic_label} (${u.counts.rows} repos)`);
      }
      case "status":
        return ev.phase === "finished" ? null : h("li", { class: "t-phase" }, PHASES[ev.phase] || ev.phase, ev.message && ` · ${ev.message}`);
      case "narration": {
        const head = ev.turn !== this.lastTurn ? h("span", { class: "t-turn" }, `Turn ${ev.turn}`) : null;
        this.lastTurn = ev.turn;
        return h("li", { class: "t-narr" }, head, h("p", {}, ev.text));
      }
      case "reasoning":
        return h("li", { class: "t-reason" }, h("details", {}, h("summary", {}, "Reasoning"), h("p", {}, ev.text)));
      case "tool_call":
        return this.toolRow(ev);
      case "tool_result":
        this.fillResult(ev);
        return null;
      case "skeptic_review":
        return h(
          "li",
          { class: `t-note verdict-${ev.review.verdict}` },
          h("strong", {}, `Skeptic: ${ev.repo} ${ev.review.verdict}`),
          " · ",
          ev.review.argument,
        );
      case "fact_check":
        return h(
          "li",
          { class: `t-note ${ev.flags.length ? "verdict-weakened" : ""}` },
          `Fact check ${ev.repo}: `,
          ev.flags.length === 0
            ? "all numbers match"
            : ev.flags.length === 1
              ? "1 number doesn't match its source"
              : `${ev.flags.length} numbers don't match their sources`,
        );
      case "run_finished":
        return h("li", { class: `t-end status-${ev.status}` }, `Finished: ${ev.status}`, ev.error && ` · ${ev.error}`);
      default:
        return null; // usage feeds the budget meter, not the trace
    }
  }

  toolRow(ev) {
    const [name, detail] = describeCall(ev.tool, ev.args);
    const result = h("span", { class: "t-result" }, "…");
    const raw = h("div", { class: "t-raw", hidden: true });
    const line = h(
      "button",
      { class: "t-line", type: "button", "aria-expanded": "false" },
      h("span", { class: "t-id" }, ev.call_id),
      h("span", { class: "t-tool" }, name),
      h("span", { class: "t-detail" }, detail),
      h("span", { class: "t-arrow" }, "→"),
      result,
    );
    const row = h("li", { class: "t-call pending" }, line, raw);
    const entry = { row, result, raw, args: ev.args, output: null };
    line.addEventListener("click", () => {
      const open = raw.hidden;
      if (open) this.renderRaw(entry);
      raw.hidden = !open;
      line.setAttribute("aria-expanded", String(open));
    });
    this.calls.set(ev.call_id, entry);
    return row;
  }

  renderRaw(entry) {
    entry.raw.replaceChildren(h("div", { class: "t-raw-label" }, "Arguments"), renderJson(entry.args).el);
    if (entry.output) entry.raw.append(h("div", { class: "t-raw-label" }, "Output"), renderJson(entry.output).el);
  }

  fillResult(ev) {
    const entry = this.calls.get(ev.call_id);
    if (!entry) return;
    entry.output = ev.output;
    const { text, kind } = describeResult(ev.output);
    entry.result.textContent = text;
    entry.row.classList.remove("pending");
    if (kind) entry.row.classList.add(`is-${kind}`);
    if (!entry.raw.hidden) this.renderRaw(entry);
  }
}
