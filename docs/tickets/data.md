# Data

### T05 GitHub access layer
**Goal:** One way to call GitHub that survives rate limits, caches everything and can run fully offline.
**Implements:** C4, D6 · **Depends on:** T01 · **P0 · M**

**Done when:**
- Requests use the configured token. A missing token fails with a clear message.
- Low or exhausted rate limits cause waiting and retrying within a bounded time, never a crash. A simulated test shows this.
- Every response is cached on disk. A repeated request is served from the cache, and the cache file can be copied to another machine.
- In offline mode, nothing touches the network, and a cache miss raises a clear, distinct error.

---

### T06 Repo metrics
**Goal:** For one repo, the numbers behind the four criteria.
**Implements:** F1.3, PRD §4 · **Depends on:** T05 · **P0 · M**

**Must provide at least:**
- **Popularity:** stars
- **Momentum (D13):** star velocity (`stars / age_days`), forks in the last 30 days, and star events in the last 30 days from the repo events feed, flagged when the feed hit its 300-event limit. GitHub no longer lists stargazers, so don't try to page them
- **Quality:** commits in the last 90 days, distinct authors in the last 90 days, number of releases and date of the latest, date of the last commit, README length
- **Newness:** created date and age in days
- **Display:** description, language and license

**Done when:**
- Each metric is tested on fixture data.
- Values that can't be determined are reported as unknown, never guessed.
- A live check on 3 repos matches the GitHub UI.
- Requests per repo are bounded (state the number). Recent forks: stop paging once forks are older than 30 days.

---

### T07 Universe builder and criteria scores
**Goal:** Build and save a topic's universe, with metrics and scores for every repo.
**Implements:** F1.1–F1.4, D7, D8 · **Depends on:** T02, T06 · **P0 · M**

**Done when:**
- A topics file defines `rust-devtools` and `local-first` (and optionally `llm-evals`), each with a label and how to find its repos.
- One command builds a universe for a topic: matching public repos in the star range and created window, excluding forks, archived repos and "awesome" lists.
- If more than about 200 repos match, a seeded random sample is taken rather than the top 200, so the universe isn't biased toward popular repos.
- Every row has the T06 metrics and a score for each of the four criteria. The momentum score uses `stars_per_day` and `forks_30d` (D13), not the capped star-event count. The scores are computed by code from the metrics (D8) and documented in one short paragraph: how each score is computed, and why a higher score is better.
- The file is valid against the contract and records when and how it was built.
- A 200-repo build finishes within about one token's hourly rate limit. Report the time and request count.

**Hints:** topic search queries from `archive/prd-v0.2-signer.md` §10.1 return 150–450 repos each.

---

### T08 👤 Build the demo universes
**Goal:** Prebuilt universes that the demo and tuning runs use.
**Implements:** F1.4, C4 · **Depends on:** T07 · **P0 · S (mostly waiting)** · *Target ~2:45*

**Done when:**
- Universes for 2 topics are built and committed.
- A person has sanity-checked the header counts and a few rows.
- The response cache is copied to the demo machine.

---

### T18 Topic relevance and quality score (real-data findings)
**Goal:** Universes contain repos that actually belong to the topic, and the quality score can't be won by sheer volume.
**Implements:** F1.1–F1.3, PRD §4, D8 · **Depends on:** T07 · **P0 · M** · *From the [findings](../findings/2026-10-24-real-data-baseline.md) (problems 1 and 2)*

**Done when:**
- **Topic relevance:** a person samples 20 random rows from each universe, and at least 17 clearly belong to the topic. The baseline Rust universe had a Discord client, a blogging tool and a DJ tool; local-first had a trade-sales AI and a hotel check-in system. How to do it is the owner's call: tighter searches, a keyword check on the description and README, or a cheap model classifying each repo. If a model is used, record the cost.
- **Quality score:** volume metrics are damped, for example capped or log-scaled commits and releases, so 981 commits doesn't beat 200 by a mile. README length is no longer a quality input; at most it's a small "has a real README" check. The header's `scoring` paragraph is updated to match.
- Universes are rebuilt and committed. All tests pass.

