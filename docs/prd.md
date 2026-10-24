# PRD: Sleeper, an agent that finds underrated GitHub repos

**Status:** v0.3 · **Updated:** 2026-10-24 · **Timebox:** 5.5 hours (hackathon) · **LLM budget:** $50

This document says **what** we are building and **why**. It does not say how. Implementation choices belong to whoever picks up a ticket, within the few big decisions locked in [`decisions.md`](decisions.md). The work itself is broken down in [`tickets/`](tickets/README.md).

Earlier versions are in [`archive/`](archive/). v0.2 has detailed implementation notes (algorithms, thresholds, prompt drafts). They are **not binding**. Where they conflict with this PRD, `decisions.md` or a ticket, the archive loses.

---

## 1. Summary

Sleeper is a lightly prompted research agent. A user picks a topic and a star range. The agent decides for itself how to find open-source projects that are **good but not yet widely discovered**: which lists and screens to run, which repos to look at closely, and which to drop. It argues against its own picks, then returns a few **pick cards**, each backed by sourced numbers.

We can **prove whether it works**. We run the agent as if it were a past date T, then measure what actually happened to its picks between T and today.

**Design principle:** *free rein over strategy, no freedom over facts.* The agent chooses how to research. Every repo it names and every number it states must come from a tool, and the system checks this.

**Extension:** the same engine uses arXiv papers, first as evidence for repo picks, later as a second discovery mode.

## 2. Why this domain

- **Discovery is inefficient.** Good projects are mostly found by luck (Hacker News, social media), so a careful agent has a real edge.
- **Outcomes are fast and objective.** Star and contributor growth show up within 3–6 months.
- **The past can be reconstructed.** Star, commit, issue and release timestamps are public, so we can rebuild what was knowable on a past date.
- **Samples are large.** Each run ranks 20+ repos, enough for a meaningful comparison.
- **Judges get it.** Hackathon judges are developers and see the value immediately.

## 3. Users and use cases

| User | Wants to |
|---|---|
| Developer scouting tools | Find promising young projects in an area before everyone else does |
| Hackathon judge | See, within 4 minutes, that the agent reasons for itself, that its facts are real, and whether its picks would have paid off |
| Our team | Get honest evidence (positive or negative) about whether agentic research beats simple heuristics |

## 4. Goals and non-goals

### Goals
- **G1:** From minimal input, return 3–5 underrated repos, each with a clear, sourced case.
- **G2:** Show the agent's self-chosen strategy in a live trace.
- **G3:** Guarantee that every number on a card comes from a tool call, verified by code rather than trust.
- **G4:** Stay within a hard token budget per run and a hard total spend.
- **G5:** Show whether the agent's picks beat simple baselines in a backtest that only uses data available at T.
- **G6 (extension):** Use linked arXiv papers as evidence for repo picks.

### Non-goals
- Predicting viral hype or meme repos.
- Repos outside public GitHub.
- Free-text topics typed during a demo. Topics come from a predefined list; free text is a later priority (P2).
- A full paper-discovery product during the hackathon (stretch only).
- Authentication, multiple users or deployment. Sleeper runs on a laptop.

## 5. Concepts

| Term | Meaning |
|---|---|
| **T** | The "as of" moment of a run. In **backtest mode**, T is a fixed past date. In **live mode**, T is the moment the universe was built. All times are UTC. "Before T" means strictly earlier than T. |
| **Window "last N days"** | The half-open interval [T − N days, T). Every windowed metric uses this definition. |
| **Universe** | The fixed set of candidate repos for one topic and one T. The agent may only research and pick repos from it. |
| **Surfaced** | A repo the agent has seen in a list or screen result earlier in the same run. Deep research is allowed only on surfaced repos. |
| **Researched in depth** | A repo for which the agent pulled detailed stats or read the README. |
| **Pick card** | The agent's structured case for one repo (Section 6, F4). |
| **Underrated** | Quality and momentum signals that outpace current attention. A repo already growing fast and widely noticed is *not* underrated. |
| **Bear pass** | A separate skeptical review of each card that can weaken or kill the pick. |
| **Final picks** | Cards that survive the bear pass, either untouched or weakened. |
| **Grounding** | Checks that every fact on a card traces back to a tool output. |

## 6. Key features

Priorities: **P0** means the demo fails without it. **P1** is expected. **P2** happens if time allows. Requirement IDs are stable, so tickets reference them.

### F1. Topic universe (P0)
Given a topic, a star range and T, the system assembles the universe of candidate repos.

- **F1.1** Topics come from a predefined list. Each topic has a label and a definition of what counts as on-topic.
- **F1.2** The universe includes repos that, **as of T**: matched the topic, had stars inside the range, were created inside the allowed window, and had at least one commit.
- **F1.3** It excludes forks, mirrors, templates and "awesome" lists. In live mode, it also excludes archived repos.
- **F1.4** **No look-ahead.** In backtest mode, nothing about the universe (membership, ordering or row contents) may depend on what happened after T. In particular, a repo that later grew past the star range must still be included if it was in range at T.
- **F1.5** The universe has a bounded size. If too many repos qualify, the system takes a reproducible, unbiased sample, never the "top" repos.
- **F1.6** Each repo row carries enough as-of-T metrics to screen on (see F3.2). A universe, once built, is saved and reused, so every run and every scoring step sees the same one.
- **F1.7** A saved universe records how it was built: when, with which queries, how many repos each filter removed, and the sampling seed.

### F2. Research agent (P0)
- **F2.1** The agent receives only the run settings: topic, star range, created window, maximum number of picks, mode and T, and budget. It chooses its own strategy.
- **F2.2** It may only use the provided tools. It has **no web access**.
- **F2.3** Before each batch of tool calls, it writes one or two plain sentences saying what it is doing and why. This narration is the live trace.
- **F2.4** It can examine several repos at once (parallel tool calls).
- **F2.5** It ends by submitting 1 to *max picks* cards (default 5, never more than 5) and a ranking of **every** repo it researched in depth. The target is at least 20 ranked repos. If it ranks fewer, the run still finishes, but the result is marked as below target.
- **F2.6** If a submission is invalid, the agent is told exactly what is wrong and may fix it a limited number of times. After that, the run ends as invalid, and the last attempt is kept.
- **F2.7** Runs end with a clear status: `ok`, `budget_exhausted`, `finish_invalid`, `stalled`, `timeout` or `error`.

### F3. Agent tools (P0 unless noted)
The agent needs these capabilities. How each tool is shaped is up to the ticket owner, within the shared contracts (WP0).

- **F3.1 Browse the universe:** list repos with simple filters (dates, stars, language, keyword) and sorting, including a random order, with paging.
- **F3.2 Screen the universe:** filter and sort by metrics. At minimum: stars at T, stars gained over the last 30 and 90 days, star growth rate over the last 90 days, commits over the last 30 and 90 days, distinct authors over the last 90 days, days since the last commit, age, and number of releases before T.
  - Star growth rate over the last 90 days = `stars_gained_90d / max(1, stars_T − stars_gained_90d)`.
- **F3.3 Repo deep dive:** as-of-T detail for one repo. This includes license, star history (monthly, plus the biggest single-day burst), commit cadence (weekly), author concentration, issue and PR activity, open issues, releases, and the last release.
- **F3.4 Read the README** as it was at T, with the length capped.
- **F3.5 Derived numbers:** a calculator that computes ratios, growth rates and similar values only from values in earlier tool outputs, so the agent never does arithmetic itself.
- **F3.6 Submit:** accepts cards and the ranking, validates them (F2.5, F4, F6.1), and either ends the run or returns specific errors.
- **F3.7 Contributors (P1):** the top authors before T, with their activity and account age. Live mode may add current profile facts. Backtest mode may not.
- **F3.8 Linked papers (extension):** see F10.
- **F3.9** Every tool output gets a unique ID within the run, so it can be cited. An error output cannot be cited.
- **F3.10** Deep tools refuse repos that are outside the universe or not yet surfaced, with distinct errors for each case.
- **F3.11** Tool outputs are kept compact enough that a run can research 20–30 repos within the budget (D10). Approximate size limits:
  - list or screen output: about 2k tokens, at most 50 rows;
  - one repo deep dive: about 600 tokens;
  - README: 1.5k tokens by default, 3k at most;
  - the context sent to the model: about 60k tokens.

  The run budget was sized from these numbers. If you change one, re-check D10.
- **F3.12** A value the system could not determine is reported as unknown, never guessed.

### F4. Pick cards and ranking (P0)
Each card must contain:
- The repo and a one-line pitch.
- **Why it surfaced:** which list or screen result first brought it up (cited).
- **The case:** 1–5 claims, each citing the tool outputs that support it.
- **Key figures:** 2–8 numbers. Each has a label, a unit, and a citation precise enough to find the exact value in the cited output.
- **Signals:** chosen from a fixed list: commit velocity, early star momentum, multiple contributors, maintainer track record, release cadence, issue responsiveness, novel technique, linked paper, niche gap, quality README.
- **Risks:** 0–4 cited claims.
- **Kill criteria:** 1–3 observable future events that would show the pick was wrong.
- **Confidence:** a value from 0 to 1.
- The bear verdict and argument, added afterwards by the system, not by the agent.

The ranking lists every repo researched in depth, ranked 1..n with no gaps, each with an optional note, and includes every card's repo. The run also produces a summary of 80 words or fewer.

### F5. Bear pass (P0)
- **F5.1** After a valid submission, each card gets an independent skeptical review. The reviewer sees only the card and the tool outputs it cites.
- **F5.2** The reviewer returns a verdict (`survives`, `weakened` or `killed`) and an argument of 120 words or fewer, following the same citation rules as cards.
- **F5.3** Killed picks stay visible, marked as killed. Final picks are the cards that are not killed. The backtest scores both the set before the bear pass and the final set.
- **F5.4** It checks at least these failure patterns: a single maintainer, stalling commits, star bursts that look bought or one-off, a README that promises more than the evidence shows, a dominant alternative, and cherry-picked evidence.

### F6. Grounding checks (P0 unless noted)
Each run gets a **grounding scorecard**.
- **F6.1 Numbers (P0):** every key figure matches the value it cites. Every number in a card's free text, in ranking notes, in the run summary and in bear arguments matches a key figure or a cited output, allowing for sensible display rounding (`1.2k`, `85%`, `2×`). Dates, versions, standard window phrases and kill-criteria thresholds are exempt.
- **F6.2 No model arithmetic (P0):** each derived value comes from the calculator, and recomputing it gives the same result.
- **F6.3 Universe (P0):** every card, ranked repo and deep-researched repo is in the universe and was surfaced before use.
- **F6.4 Why surfaced (P0):** the cited list or screen output actually contains the card's repo.
- **F6.5 Leak check (P0):** names of other repos, URLs, @handles, dates and arXiv IDs in card text, ranking notes, the run summary and bear arguments must appear somewhere in this run's tool outputs. Anything else is flagged. Kill criteria are exempt, because they describe the future on purpose.
- **F6.6 Claim judge (P0):** an independent model labels each claim and risk `supported`, `unsupported` or `contradicted` from the cited outputs only. The judge itself is scored on a hand-labeled set of about 15 claims. Below 80% accuracy, its results are shown as advisory.
- **F6.7 Planted repo (P1):** a fake repo with known data can be added to the universe. If the agent writes about it, every fact must match the planted data.
- **F6.8 Perturbation (P2):** change one input fact, rerun, and check that the card's claims follow the change.
- **F6.9 Tool accuracy (P1):** a manual spot-check of key metrics for 10 repos against GitHub, recorded in the repo.

### F7. Backtest evaluation (P0)
- **F7.1** Run the agent in backtest mode on frozen universes. Use at least 2 runs per topic with different seeds, plus one run with the planted repo, which is not scored.
- **F7.2 Outcome:** the main measure is log star growth from T to the scoring date, `ln((stars_now+1)/(stars_T+1))`. Secondary measures are commits after T and whether any release happened after T. Current star counts are captured once per universe and reused (a frozen snapshot).
  - The agent is asked to find *underrated* repos (quality ahead of attention) but is scored on attention gained later. That gap is intended: we are testing whether quality signals at T predict later attention. Say so in the demo.
- **F7.3 Baselines** from the same universe, each picking the same number of repos as the agent: universe average (P0), random portfolios as a distribution (P0), momentum, i.e. top stars gained in 30 days (P0), activity, i.e. top commits in 90 days (P1), and a naive LLM given the universe list with no tools (P1).
- **F7.4 Statistics:**
  - The share of random portfolios the agent's picks beat, for both the before-bear and final sets and for each baseline.
  - Ranking spread: mean growth of the agent's top third minus its bottom third.
  - Spearman correlation between rank and growth, with a permutation p-value.
  - Killed picks compared with surviving picks.
  - Each run reported separately, plus a pooled result. Results are "directional", not "significant", unless the pooled ranking test gives p < 0.05.
- **F7.5 Contamination controls:**
  - Before the backtest, confirm that the agent and bear models know nothing after T.
  - Probe each pick and a sample of ranked repos for model knowledge of them after T.
  - Report flags. Never drop repos silently.
  - Knowledge from **before** T is fair game. Only knowledge of what happened after T counts as contamination.
- **F7.6** Report the known caveats: survivorship, removed stars, stars as a proxy, names and languages reflecting current values, current-text topic matching, and the small sample.
- **F7.7** Show the results even if they are bad.

### F8. Web UI (P0 unless noted)
A single local web page.
- **F8.1 Launch:** choose a topic (from the prebuilt universes), star range, max picks, live or backtest mode (showing T) and the model. Then start a run.
- **F8.2 Live trace:** the narration appears as a story. Each tool call shows as a one-line summary that expands to the raw output.
- **F8.3 Budget meter:** tokens used, the percentage of the budget and the dollar cost (marked if estimated).
- **F8.4 Cards:** all card fields, a bear badge (killed picks struck through) and grounding flags. Clicking a key figure opens its source output with the cited value highlighted.
- **F8.5 Grounding scorecard** panel.
- **F8.6 Backtest panel:**
  - Mean growth for the agent (before and after the bear pass), each baseline and the universe.
  - The random-portfolio distribution with the agent marked.
  - Agent rank against outcome, with Spearman ρ.
- **F8.7 Runs list and replay:** any recorded run can be replayed at higher speed, **with no network access**. Replay is the demo backup.

### F9. Picks on the record (P1)
- **F9.1** Final picks from every live run are saved with a timestamp, the run reference, the model and the universe, then committed. They will be re-scored on 2027-04-24.

### F10. Extension: arXiv papers
- **F10.1 Papers as signals (P1, only if the core is done by about 3:45):** for a repo, find the arXiv papers it references and report each paper's title, publication date and citation counts as of T. Only citations dated before T count in backtest mode. Cards may cite these like any other evidence.
- **F10.2 Paper discovery mode (stretch):** the same engine over a universe of papers, where "underrated" means early quality signals outpacing citations. The outcome is citations 6–12 months later. Contamination is harder here, so state the guarantee honestly.
- **F10.3 Cross-domain view (after the hackathon):** one query returns papers and repos and the gaps between them.

## 7. Cross-cutting constraints

These hold for every ticket.

- **C1. No look-ahead in backtests.** No information from T or later may reach the agent, the bear reviewer, the universe or the baselines. Free-text READMEs are exempt, since they can legitimately talk about plans. Structured data must be checked automatically, and a violation fails the run loudly. Models that influence picks must have a knowledge cutoff before T. ([`decisions.md`](decisions.md) lists the known traps.)
- **C2. Facts only from tools.** The model's memory is not a source. Every repo named and every number stated must trace to a tool output in the same run.
- **C3. Budgets are hard limits.** There is a per-run token budget (the agent is warned as it runs low, then forced to submit), separate small caps for the bear, judge, probe and baseline calls, and a total spend guard that refuses to start a run that would exceed the limit. **Every** LLM call goes through the spend guard.
- **C4. Runs are recorded and reproducible.** Each run saves its settings, code version, prompt version, model IDs, universe, a complete append-only event log, the result, the grounding scorecard and backtest results. The UI and the grounding checks read only these records.
- **C5. Offline-capable.** With an offline switch on, nothing touches the network. Recorded runs replay, and data the system lacks is reported as a clear error.
- **C6. Shared contracts are binding.** The data shapes passed between components are defined once (ticket T0.2). To change them, update the contract and tell the other ticket owners.
- **C7. Secrets never enter the repo.**

## 8. Success criteria

- **G1/G2:** live runs end to end for at least 2 topics, each ending `ok` with 3–5 cards.
- **G3:** 100% of key figures pass F6.1 or are visibly flagged. 0 out-of-universe repos. 0 model-arithmetic flags.
- **G4:** every run stays under its token cap, and total spend stays at or below $50.
- **G5:** backtests complete for at least 2 topics (2 runs each), with the universe, random and momentum baselines, ranking spread and Spearman ρ, all shown in the UI.
- (Should) The planted-repo test passes, or is inconclusive after one rerun.
- Contamination checks pass for the agent and bear models.
- A recorded run replays in the UI with no network.

## 9. Demo (4 minutes)

1. Choose "Rust developer tools, 50–2,000 stars", live mode, using a prebuilt live universe. If live isn't ready, use backtest mode. (10 s)
2. Live trace: the agent picks its own screens, reads READMEs and drops weak candidates. Use replay at higher speed if the live run is slow. (60 s)
3. Open a card: the case, signals, a linked paper if there is one, and kill criteria. Click a figure to see its source. (40 s)
4. Show a pick the bear pass killed. (20 s)
5. Grounding scorecard, including the planted-repo result if T5.3 shipped. (20 s)
6. Backtest: "As of T, here is what it picked. By today, its picks grew X against Y for momentum and Z for the universe. They beat P% of random portfolios. Ranking spread: S." Use the real numbers, even if they are bad. (45 s)
7. Close with the caveats and the timestamped live picks. (15 s)

## 10. Risks

| Risk | Mitigation (what must be true) |
|---|---|
| Look-ahead bias | C1, plus F1.4, plus an automated future-data check |
| GitHub rate limits during universe builds | Builds start early and use separate tokens; responses are cached and shareable between machines |
| The model already knows the repos | Cutoff before T, contamination checks (F7.5), no web access |
| Context growth blows the budget | The runtime keeps context bounded without breaking citations |
| Star counts are gamed | The bear pass sees star bursts; listed as a caveat |
| Runaway spend | Provider-side key limit, a per-run cap and a total spend guard |
| Bad backtest result | Show it anyway; honest negatives are credible |
| Live demo fails | Offline replay of the best recorded run |
| Teams or agents block each other | Contracts and fake data land first (WP0), so everything else can build against fakes |

## 11. Open questions

- Who owns which work package? Fill this in in [`tickets/README.md`](tickets/README.md).
- Keep the third topic (`llm-evals`), or drop it to save build time? If a topic is cut, `llm-evals` goes first. Topics as a whole are cut late (see the cut order in the tickets).
- Should the demo machine get everyone's response cache before rehearsal? (Recommended: yes.)
