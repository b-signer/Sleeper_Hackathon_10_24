# PRD: Sleeper (MVP)

**Status:** v0.4 MVP · **Updated:** 2026-10-24 · **Timebox:** 5.5 hours · **LLM budget:** $50

This document says **what** we are building and **why**. How to build each piece is up to whoever picks up the ticket, within the few decisions in [`decisions.md`](decisions.md). The work is broken into tickets in [`tickets/`](tickets/README.md).

**Scope rule: product and demo first.** We build the core loop end to end, then polish. The larger plan, with a point-in-time backtest, grounding suite and papers, is in [`archive/v0.3-full-plan/`](archive/v0.3-full-plan/) as stretch material.

---

## 1. Summary

Sleeper is a research agent that finds **GitHub repos that are good but not yet widely discovered**.

The user picks a topic and a star range. The agent decides on its own which repos to screen, which to look at closely and which to drop. It returns a few **pick cards**. Each card rates the repo on four criteria and makes a short case, backed by numbers from GitHub.

**Design principle:** *free rein over strategy, no freedom over facts.* The agent chooses how to research, but every repo and every number it shows must come from a tool call.

## 2. Users and the demo moment

| User | Wants to |
|---|---|
| Developer scouting tools | Find promising young projects in an area early |
| Hackathon judge | See in 4 minutes that the agent reasons for itself and that its picks are backed by real data |

## 3. Goals and non-goals

### Goals
- **G1:** From minimal input, return 3–5 underrated repos, each with criteria scores and a sourced case.
- **G2:** Show the agent's self-chosen strategy as a live trace.
- **G3:** Every number on a card comes from a tool output.
- **G4:** Stay inside a per-run token budget and $50 total.

### Non-goals (MVP)
- Backtests, i.e. judging picks made as of a past date. These are stretch work.
- arXiv papers and other sources. GitHub repos only.
- Free-text topics. Topics come from a short predefined list.
- Exhaustive coverage. A bounded sample of each topic is good enough.
- Authentication, multiple users or deployment. Sleeper runs on a laptop.

## 4. The four criteria

Every repo in a universe is rated on these criteria, and every card shows them.

| Criterion | Meaning | Example evidence |
|---|---|---|
| **Popularity** | How much attention the repo has now | Stars |
| **Momentum** | How fast attention is growing | Star velocity (stars per day since creation) and forks in the last 30 days. Recent star events are shown when available |
| **Quality** | Signs of a healthy, well-run project | Recent commits, number of active contributors, releases, a substantial README |
| **Newness** | How young the repo is | Days since creation |

**Underrated** means quality and momentum that are high relative to popularity. A popular repo that is already growing fast is not underrated.

The scores must be **reproducible from the data shown**: the same data always gives the same score, computed by code, not invented by the model. The agent interprets them, picks, and argues the case.

## 5. Key features

**P0** means the demo fails without it. **P1** is expected if time allows. Requirement IDs are stable, so tickets reference them.

### F1. Topic universe (P0)
- **F1.1** A short predefined list of topics. Each has a label and a way to find its repos on GitHub. Start with `rust-devtools` and `local-first`; `llm-evals` is optional.
- **F1.2** For a topic, star range and creation window, the system builds a **universe**: a bounded set of matching public repos, about 200 at most. It excludes forks, archived repos and "awesome" lists.
- **F1.3** Each repo in the universe carries the metrics behind the four criteria, plus its scores.
- **F1.4** Universes are prebuilt and saved before the demo, so a run never waits on a full GitHub scan.

### F2. Research agent (P0)
- **F2.1** The agent receives the run settings and the tools. It chooses its own strategy and has no web access.
- **F2.2** Before each batch of tool calls, it writes one or two plain sentences saying what it is doing and why. This is the live trace.
- **F2.3** It finishes by submitting 1–5 pick cards (F4) and a ranking of every repo it looked at closely, with at least 10 repos as the target.
- **F2.4** An invalid submission is rejected with specific errors, and the agent gets a couple of retries.
- **F2.5** A run always ends with a clear status: `ok`, `budget_exhausted`, `invalid`, `timeout` or `error`.

### F3. Agent tools (P0)
- **F3.1 Browse and screen:** list universe repos, filtered and sorted by any criterion or metric.
- **F3.2 Repo details:** the full metrics and scores for one repo.
- **F3.3 README:** read a repo's README, with the length capped.
- **F3.4 Submit:** accepts the cards and the ranking, checks them, and ends the run or returns errors.
- **F3.5** Every tool output has an ID that cards cite. Deep tools only accept repos in the universe.

### F4. Pick cards (P0)
Each card contains:
- The repo and a one-line pitch
- The four criteria scores, each with its key number
- A short case: 1–4 claims, each citing the tool outputs behind it
- Risks: 0–3 items
- What would prove the pick wrong: 1–2 observable signals to watch
- Confidence, from 0 to 1

### F5. Skeptic review (P1)
- **F5.1** After submission, a separate model call argues against each card, using only the card and its cited outputs. It returns `survives`, `weakened` or `killed` and a short argument. Killed cards stay visible, struck through.

### F6. Fact check (P1)
- **F6.1** An automatic check that every number on a card matches a value in the tool outputs it cites. Mismatches are flagged on the card.

### F7. Web UI (P0)
- **F7.1 Launch:** choose a topic (from the prebuilt universes), star range, max picks and model, then start a run.
- **F7.2 Live trace:** the agent's narration, plus each tool call as one line that expands to the raw output.
- **F7.3 Cards:** every card field, with the four criteria shown visually and a skeptic badge if F5 shipped. Clicking a number shows its source output (P1).
- **F7.4 Budget meter:** tokens used and dollar cost.
- **F7.5 Replay:** any recorded run can be replayed at higher speed with no network access. Replay is the demo backup.

### F8. Picks on the record (P1)
- **F8.1** Final picks from demo runs are saved with a timestamp and committed, so they can be checked months later.

### Stretch (only after the demo is safe)
- **S1 Rough backtest:** judge picks made as of a past date against what happened since. **This is now much harder:** GitHub no longer lists stargazers, so past star counts can't be rebuilt from the API (see `decisions.md`). It would need an outside source such as GH Archive. Treat it as post-hackathon. The full design is in `archive/v0.3-full-plan/`.
- **S2 Linked papers:** arXiv papers referenced in a repo's README, used as evidence.

## 6. Constraints

- **C1. Facts only from tools.** The model's memory is not a source. Every repo named and every number shown traces to a tool output in the same run.
- **C2. Hard budgets.** There is a per-run token cap, a hard $50 limit on the OpenRouter key, and a check that refuses a run that would exceed it.
- **C3. Runs are recorded.** Each run saves its settings, a complete event log and its result. The UI reads only these records, which is what makes replay work.
- **C4. Demo-safe.** The demo works with no network, using prebuilt universes, cached GitHub data and a recorded run.
- **C5. Shared contracts.** The data shapes passed between components are defined once (ticket T02). To change one, update the contract and tell the other owners.
- **C6. No secrets in the repo.**

## 7. Success criteria

- End-to-end runs for 2 topics, each ending `ok` with 3–5 cards that show all four criteria.
- Every number on the cards traces to a tool output (spot-checked by hand, or by F6 if shipped).
- Every run stays under its token cap, and total spend stays at or below $50.
- A recorded run replays in the UI with no network.

## 8. Demo (4 minutes)

1. Pick "Rust developer tools, 50–2,000 stars" and start a run. (15 s)
2. Live trace: the agent chooses its own screens, reads READMEs and drops weak candidates. Switch to replay at higher speed if the run is slow. (75 s)
3. Open a card: the four criteria, the case, the risks, and what would prove it wrong. Click a number to see its source. (60 s)
4. Show a card the skeptic killed, if F5 shipped. (20 s)
5. The ranking: why these repos are underrated compared with the popular ones in the same topic. (30 s)
6. Close with the timestamped picks committed today, which anyone can check in six months. (20 s)

## 9. Risks

| Risk | Mitigation |
|---|---|
| GitHub rate limits | Universes are small and prebuilt, and every response is cached |
| The live demo fails | Offline replay of a recorded run |
| Budget overrun | Per-run cap, a key-level spend limit, and the cheap model during development |
| The model invents facts | Tools are the only source (C1), and cards cite tool output IDs |
| Running out of time | Build against fake data first, follow the cut order, and keep stretch work out until the demo is safe |
