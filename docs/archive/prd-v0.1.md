# PRD: Sleeper: An Agent That Finds Underrated GitHub Repos (and the Papers Behind Them)

**Status:** Draft for hackathon · **Timebox:** 5.5 hours · **LLM budget:** $50 (OpenRouter)

---

## 1. Summary

Sleeper is a lightly prompted research agent. The user gives it a topic and a star range, and it works out on its own how to find open-source projects that are **good but not yet widely discovered**. It chooses its own searches and screens, decides which repos to dig into, argues against its own picks, and returns a small set of **pick cards** backed by sourced numbers.

This domain lets us **prove whether it works**: we run the agent as of a past date and measure what actually happened to its picks over the following months.

**Extension:** the same engine uses arXiv papers, first as signals for repos (a repo implementing a promising new paper), then as a second discovery mode (underrated papers).

**Design principle:** *free rein over strategy, no freedom over facts.* The agent decides how to research. Every repo and every number must come from a tool.

## 2. Why this domain

- **Inefficient discovery:** good projects are mostly found by luck (Hacker News, social media), so a careful agent has a real edge.
- **Fast, objective outcomes:** star and contributor growth are visible within 3–6 months.
- **Point-in-time data:** stargazer and commit timestamps are public, so we can reconstruct what was knowable on a past date.
- **Large samples:** each run can rank 30–50 repos, enough for a meaningful comparison.
- **Judge familiarity:** hackathon judges are developers and understand the value immediately.

## 3. Goals and non-goals

### Goals
- G1: From minimal input, return 3–5 underrated repos with a clear, sourced case for each.
- G2: Make the agent's self-chosen strategy visible in a live trace.
- G3: Guarantee every number on a card comes from a tool call (verified in code).
- G4: Stay within a hard token budget per run.
- G5: **Show whether the agent's picks beat simple baselines in a point-in-time backtest.**
- G6 (extension): Use linked arXiv papers as evidence for repo picks.

### Non-goals
- Predicting viral hype or meme repos.
- Repos outside public GitHub.
- A full paper-discovery product during the hackathon (Tier 2 is a stretch; see Section 10).

## 4. User input

```yaml
topic: "Rust developer tools"    # free text
stars: [50, 2000]                 # range at the as-of date
created_after: "2025-01-01"       # optional
max_picks: 5
as_of: null                       # null = live; a date = backtest mode
run_budget_tokens: 400000
```

## 5. Functional requirements

### 5.1 Universe
- FR1: Build the universe with GitHub search on topic keywords and creation date, **slicing by creation-date ranges** so each query returns under 1,000 results (GitHub's search cap).
- FR2: **Never filter or sort by current star counts in backtest mode.** Current counts leak the future; a repo that grew past the range would be quietly excluded. Compute stars as of T (FR8) and filter on that.
- FR3: Exclude forks, archived repos, and awesome-lists.
- FR4: The agent may only pick or research repos in the universe. Tools reject others.

### 5.2 Agent toolbox

| # | Tool | Returns | Priority |
|---|---|---|---|
| 1 | `list_universe(filters)` | Repos with name, description, stars as of T, created date | P0 |
| 2 | `screen(metric, op, value)` | Repos matching a metric (e.g., commits in last 90 days > 50) | P0 |
| 3 | `get_repo_stats(repo)` | Stars over time, commits, contributors, issue open/close rates, releases, as of T | P0 |
| 4 | `read_readme(repo)` | README as of T, truncated to about 3k tokens | P0 |
| 5 | `get_contributors(repo)` | Top contributors: followers, other notable repos, org affiliation | P1 |
| 6 | `get_dependents(repo)` | Notable repos that depend on it, if available | P2 |
| 7 | `get_linked_papers(repo)` | arXiv IDs found in README, with paper stats (extension) | Ext |
| — | `compute(expression, inputs)` | Derived metrics; the model never does its own arithmetic | P0 |
| — | `finish(cards, ranking)` | Ends the run | P0 |

- FR5: Tool outputs are compact JSON. Each call is logged with an ID.
- FR6: Use an authenticated GitHub token (5,000 requests/hour). Cache everything to disk.
- FR7: In backtest mode, every tool respects T:
- FR8: Stars as of T from stargazer timestamps (`Accept: application/vnd.github.star+json` returns `starred_at`).
- FR9: Commits via `until=T`; issues and releases filtered by `created_at ≤ T`; README fetched at the last commit before T.

### 5.3 Agent loop
- FR10: One system prompt: goal, input, rules (Section 6), remaining budget, tools. No prescribed workflow.
- FR11: The agent sees `tokens_remaining` each turn; a wrap-up warning at 15%; a hard stop at 0%.
- FR12: Older tool results are trimmed to keep context small.
- FR13: `finish` requires both the top picks (cards) **and** a ranking of every repo it researched in depth (for the ranking evaluation).

### 5.4 Bear pass (mandatory)
- FR14: A separate, cheaper model call per pick, with a fresh context, gets only the card and its cited tool outputs. It looks for reasons the pick is wrong: single maintainer, stalled commits, star bursts that look bought, a README that overpromises, a dominant competitor.
- FR15: Returns `{verdict: survives | weakened | killed, argument}`. Killed picks are shown as killed.

### 5.5 Pick card schema

```json
{
  "repo": "owner/name",
  "one_liner": "Fast incremental linter for X; 3 contributors from major Rust projects, 340 stars.",
  "why_surfaced": "Screened commits_90d > 60 and stars < 500",
  "case": [
    {"claim": "Commit velocity doubled over the last quarter", "source_call_id": "c_021"}
  ],
  "key_figures": [
    {"label": "Stars (as of T)", "value": 340, "source_call_id": "c_019"}
  ],
  "signals": ["maintainer track record", "linked paper gaining citations"],
  "kill_criteria": ["No release within 60 days"],
  "risks": [{"claim": "Single maintainer writes 85% of commits", "source_call_id": "c_024"}],
  "bear_verdict": "survives",
  "bear_argument": "…",
  "confidence": 0.6
}
```

### 5.6 UI
- FR16: Input form (Section 4), with a live/backtest toggle.
- FR17: Live trace of reasoning and tool calls, written readably.
- FR18: Budget meter with tokens and estimated dollar cost.
- FR19: Cards with bear verdict, flagged figures, and expandable sources.
- FR20: Grounding scorecard (Section 7.1) and backtest results (Section 7.2).

## 6. System prompt rules (draft)

1. Only research repos returned by `list_universe` or `screen`.
2. Every number and factual claim must cite a tool call ID.
3. Don't rely on memory about any repo, person, or paper. If a tool doesn't say it, it's unknown.
4. Use `compute` for any arithmetic.
5. Spend your budget on the most promising repos; drop weak ones early.
6. "Underrated" means quality and momentum signals outpacing current attention, not just "growing fast already."
7. For each pick, state what would prove you wrong.
8. Finish with up to `max_picks` cards and a full ranking of repos you researched.

## 7. Evaluation

### 7.1 Grounding: is it making things up?

| Check | Method | Priority |
|---|---|---|
| Numbers check | Each figure compared in code to its cited tool output | Must |
| No model arithmetic | Derived values must come from `compute` or are recomputed and compared | Must |
| Universe check | Every repo on a card came from `list_universe`/`screen` | Must |
| "Why surfaced" check | The cited screen exists in the log with those parameters | Must |
| Leak check | Names, numbers, and dates on cards not found in any tool output are flagged | Must |
| Claim judge | Cheap model sees one claim plus its cited output only: supported / unsupported / contradicted. Validated against ~15 hand-labeled claims | Must |
| Planted repo | Inject a fictional repo with fabricated stats into the universe; if picked, its card must match the planted data exactly | Should |
| Perturbation | Change one input (e.g., set commits to zero) and re-run on that repo; the card must change | Should |
| Tool accuracy | Spot-check ~10 repos' as-of-T stats by hand | Should |

**Output:** a grounding scorecard per run, e.g. "52 claims, 50 supported, 2 flagged; 0 out-of-universe repos; planted repo test passed."

### 7.2 Outcome: are the picks actually good?

**Point-in-time backtest**
- Choose T at least 4–6 months ago **and after the agent model's documented training cutoff** (check the model card). Example: T = April 2026, outcome measured to October 2026.
- Run the agent in backtest mode with all tools restricted to T (FR7–FR9).
- **Contamination probe:** before each run, ask the model with no tools whether it knows each top pick and what happened to it. Flag and report any repo it seems to know.

**Outcome metric:** star growth from T to now, as `log((stars_now + 1) / (stars_T + 1))`. Secondary: contributor growth, whether a release shipped.

**Comparisons**

| Baseline | Definition |
|---|---|
| Universe average | Mean growth across the whole as-of-T universe |
| Momentum | Top N by stars gained in the 30 days before T |
| Activity | Top N by commits in the 90 days before T |
| Random | N random repos from the universe (averaged over many draws) |

**Ranking spread:** compare the top third of the agent's ranking against the bottom third. This uses 30–50 repos per run instead of 5, so the result means something.

**Caveats to report:** deleted and private-turned repos are missing (survivorship); stars are an imperfect proxy for value and can be gamed; a few topics and one as-of date is a small sample.

**Picks on the record:** commit live-mode picks to the repo with a timestamp so they can be checked later.

## 8. Budget plan ($50)

| Use | Allocation | Notes |
|---|---|---|
| Development and debugging | $12 | Cheap, fast model |
| Backtest runs (2–3 topics) | $15 | Agent model with cutoff before T |
| Live demo runs | $10 | Strongest affordable model |
| Claim judge, bear pass, probes | $5 | Cheap model |
| Reserve | $8 | Re-runs |

Hard per-run token cap in code; spend limit set on the OpenRouter key; actual cost logged per run. Check current OpenRouter prices and model cutoffs before choosing.

## 9. Timeline (5.5 hours)

| Time | Track A (data) | Track B (agent) | Track C (eval + UI) |
|---|---|---|---|
| 0:00–0:30 | Agree on tools, card schema, topics, T, models | Same | Same |
| 0:30–2:00 | GitHub client, cache, universe builder, `get_repo_stats` with as-of-T | Agent loop, budget, logging, `compute` | Numbers, universe and leak checks |
| 2:00–3:00 | `read_readme` at T, `screen`, contributors | First end-to-end run (cheap model) | Claim judge + labeled examples |
| 3:00–4:00 | Outcome scorer + baselines | Bear pass, `finish` with ranking | UI: trace, cards, budget meter |
| 4:00–4:45 | Extension Tier 1 (Section 10) **or** help elsewhere | Prompt tuning; planted-repo test | Scorecard and backtest views |
| 4:45–5:15 | Backtest runs + live demo runs | Same | Same |
| 5:15–5:30 | Rehearse | Same | Same |

**Cut first if behind:** extension → contributors tool → perturbation test → UI polish.
**Never cut:** as-of-T correctness, universe restriction, numbers check, the backtest with at least one baseline.

## 10. Extension: arXiv papers

Papers and code are linked: many promising repos implement a recent paper, and a paper's code gaining traction can signal the paper is underrated (and the reverse).

### Tier 1: Papers as signals for repos (fits the hackathon if core is done by ~4:00)
- `get_linked_papers(repo)`: extract arXiv IDs and links from the README (regex), then fetch metadata from the arXiv API and citation counts from the Semantic Scholar API.
- In backtest mode, count only citations from papers published on or before T.
- New signals the agent can use: "implements a paper less than 6 months old," "paper citations accelerating," "authors have prior well-cited work."
- Cards may cite paper facts with call IDs like any other evidence.
- **Effort:** ~45 minutes. **Cost:** negligible (free APIs).

### Tier 2: Paper discovery mode (stretch)
- Same engine, different universe: arXiv papers in a category (e.g., `cs.LG`) and date range.
- Tools: `list_papers`, `get_paper_stats` (citations as of T, authors' h-index, venue), `read_abstract`, `get_linked_repos` (GitHub links in the abstract or comments).
- "Underrated" = method quality and early signals (code released, strong authors, early citations from good papers) outpacing current citations.
- **Outcome:** citations 6–12 months after T, compared against baselines (author h-index, early citation count).
- **Watch out for:** citation counts move slowly, so T may need to be 12+ months ago, which conflicts with model cutoffs. Use anonymized titles and abstracts or accept a weaker contamination guarantee and state it.

### Tier 3: Cross-domain view (post-hackathon)
- One query ("efficient inference") returns both papers and repos, with links between them.
- Detects mismatches: highly starred repo built on an overlooked paper, or a well-cited paper with no good implementation (a gap someone could fill).

## 11. Demo script (4 min)

1. Type: "Rust developer tools, 50–2,000 stars." (10 s)
2. Live trace: the agent picks its own screens, reads READMEs, drops names. (60 s, or a recorded run sped up)
3. Open a card: the case, signals, linked paper, kill criteria; click a figure to its source. (40 s)
4. Show a pick the bear pass killed (e.g., star burst that looks bought). (20 s)
5. Grounding scorecard, including the planted-repo test. (20 s)
6. **Backtest:** "As of April 2026, after the model's cutoff, here's what it picked. Six months later: X× universe growth, versus Y× for momentum. Top-third versus bottom-third ranking spread: Z." (45 s)
7. Close with the caveats and the timestamped live picks. (15 s)

## 12. Success criteria

- End-to-end live run for at least 2 topics.
- Backtest completed for at least 1 topic with at least 2 baselines and the ranking spread.
- 100% of card figures pass the numbers check or are flagged; 0 out-of-universe picks.
- Planted-repo test passed.
- Each run under its budget cap.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Look-ahead bias in the universe | Never use current star counts in backtest mode (FR2) |
| GitHub rate limits (search: 30/min) | Token auth, caching, precompute universe before demo |
| Stargazer paging is slow for larger repos | Keep the star range low; cache aggressively |
| Model knows the repos | T after model cutoff + contamination probe |
| Star counts gamed | Bear pass checks for suspicious bursts; report as caveat |
| Backtest result is bad | Show it anyway, with the grounding results; honest negatives are credible |
| Live demo fails | Pre-recorded best run as backup |

## 14. Open questions

- Which 2–3 topics? (Pick ones with many small active repos: Rust dev tools, LLM eval tooling, local-first apps.)
- Exact T and which agent model, based on documented cutoffs.
- Stack: Python + FastAPI + simple frontend, or Streamlit?
- Stars range: 50–2,000, or narrower for faster stargazer fetching?
