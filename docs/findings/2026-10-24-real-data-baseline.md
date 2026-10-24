# First real-data run: findings and baseline

**Date:** 2026-10-24 · **Run by:** Roee (with Claude) · **Cost:** 154 GitHub requests, $0.01

Until this run, every check used fake data or tiny runs. Tests show that the parts fit together, not that the picks are any good. This run is the first look at **real output**, and it's the baseline that T11 and the follow-up fixes should beat.

## What was run

1. `sleeper build-universe rust-devtools --max-size 25`: a 25-repo sample of the real Rust topic. The file is [`universe-rust-devtools-25.json`](2026-10-24-real-data-baseline/universe-rust-devtools-25.json).
2. `sleeper run` on it, using luna with a 120k token budget, at most 12 turns and a ranking target of 8, with the real T09 tools. The run record is [`result.json`](2026-10-24-real-data-baseline/result.json), [`events.jsonl`](2026-10-24-real-data-baseline/events.jsonl) and [`settings.json`](2026-10-24-real-data-baseline/settings.json).
3. The full 200-repo universes from T08 (`data/universes/`) were then sampled by hand for topic relevance.

**Result:** status `ok`. 3 turns, 36,776 tokens, $0.0108. 5 cards, 10 repos ranked. The first submit was accepted.

## What works (verified)

- **The data is accurate.** `pixtuoid/pixtuoid` and `fzn0x/idx` were checked against the GitHub API, and their stars, forks and creation dates match exactly. Of 25 rows, only 2 values were missing. The 981 commits for `holon-run/holon` are real: frequent human pull requests, not a bot.
- **The pipeline works end to end on real data:**
  - topic list → agent → real READMEs (served from the cache the universe build warmed) → strict submit check → result
  - 3 parallel browses, then 18 parallel repo details and README calls in a single turn
- **Cards are grounded.** Every number traces to a cited tool output. README-based claims are specific and accurate, for example "mcpls … exposes 31 tools for type information, references, diagnostics…".
- **Builds are cheap:** 25 repos took 104 s and 154 requests, so about 14 minutes and 1,200 requests for 200 repos.

## Problems: picks look plausible but wouldn't convince a developer

| # | Problem | Evidence | Fix in |
|---|---|---|---|
| 1 | **Off-topic repos in the universe, and the agent picks them** | The #2 pick, `M-Igashi/baken`, is a DJ music tool, not a developer tool. The full Rust universe includes a Discord client (`chojs23/concord`) and a blogging tool (`bahdotsh/blogr`). The local-first universe includes a B2B trade-sales AI and a hotel check-in system. Searches like `language:Rust topic:cli` match every Rust CLI | **T18** (topic relevance) and **T11** (the agent skips off-topic repos) |
| 2 | **The quality score rewards raw volume** | Quality is commits plus releases plus README length, so it can be gamed and looks naive. The agent cited "53,762 README characters" as a strength | **T18** (scoring) and **T11** (don't present README length as quality) |
| 3 | **Shallow research** | It looked at only 10 of 25 repos and submitted after 3 turns, with 69% of the budget unused. On 200 repos it would miss most candidates | **T11** |
| 4 | **"Wrong if" criteria aren't observable, and one is backwards** | "Its quality-adjusted adoption fails to grow" is vague. For `baken`, "usage expands beyond a niche DJ audience" describes success, not failure | **T11** |
| 5 | **Confidence is meaningless** | Every pick is between 0.82 and 0.90 | **T11** |
| 6 | **Backtest wording leaks in** | Claims say "a commit on the as-of date", but the MVP is live only | **T11** |
| 7 | **Nobody has seen a real run in the UI** | The UI's run button replays the recorded fake run | **T19** |

## How we check progress from now on

1. **A real-data run is the acceptance test.** After any change to the prompt, scoring or topics, do a cheap luna run on a real universe. A person reads the cards and asks: *"Would a developer agree these are good, underrated tools **in this topic**?"*
2. **Compare against this baseline.** For each problem above, say whether it is fixed, the same, or worse.
3. **Checkpoint B is a team review:** a real run on a T08 universe, watched in the UI by everyone.
4. **Code review:** no human has read the generated code line by line. If anyone has 15 minutes, the highest-value files are `src/sleeper/agent/submit_check.py` and `src/sleeper/scoring.py`.
