# WP9: Extension, arXiv papers

Start this only if the core P0 work is passing by about 3:45 (after Checkpoint B). It is the first thing to cut.

---

### T9.1 Linked papers as signals
**Goal:** Let the agent use the papers a repo implements as evidence for picking it.
**Implements:** F10.1, F3.8, C1 · **Depends on:** T3.2 · **Ext · M**

**Done when:**
- A tool, with the same surfacing, universe and future-data rules as T3.2, finds the arXiv papers that a repo's README at T references.
- For each paper it reports the title, the publication date, citations before T, and citations gained in the 90 days before T.
- In backtest mode, only citations dated before T are counted. Current-only facts, such as author h-index, are left out.
- It respects the external services' rate limits, and every response is cached.
- Cards can cite its output like any other evidence, and the grounding checks accept it.
- A fixture test covers README link extraction and the counting of citations before T.

**Hints:** the arXiv API is limited to about 1 request every 3 seconds. Semantic Scholar has citations with dates, and its public rate limits are low.

---

### T9.2 Paper discovery mode (stretch)
**Goal:** The same engine, with a universe of papers instead of repos.
**Implements:** F10.2 · **Depends on:** T9.1 · **Stretch · L**

**Done when:**
- A universe of papers for one category and date range can be researched with paper-specific tools.
- Its picks are scored on citations 6–12 months later, against simple baselines.
- The contamination guarantee is stated explicitly. Citations move slowly, so T may need to fall before the model's cutoff.
