# WP2: Topic universes

WP2 turns a topic and a T into a frozen, unbiased set of candidate repos with metrics, so the agent and the backtest work on the same ground truth.

---

### T2.1 Topic definitions
**Goal:** Define the three topics so a builder can find their repos and check that each repo is on topic.
**Implements:** F1.1, D8 · **Depends on:** none · **P0 · S**

**Done when:**
- A topic file defines `rust-devtools`, `local-first` and `llm-evals`. Each has a display label, the searches that find candidates, and keywords that confirm a repo is on topic.
- Each topic's searches, run today with the D7 window and a modest lower star bound, return roughly the sizes in `decisions.md` §2 (150–450 repos).

**Hints:** v0.2 §10.1 has a working draft of searches and keywords for all three topics.

---

### T2.2a Universe candidates
**Goal:** Find the topic's candidate repos, apply membership as of T, and sample. This is the expensive, rate-limited part, so it should start early.
**Implements:** F1.2–F1.5, F1.7, C1 · **Depends on:** T1.1, T1.5, T2.1 to write and test the code (against a stubbed stars-at-T); T1.2 to run a real build · **P0 · M** · *Target: builds running by ~2:00*

**Done when:**
- One command produces the membership list for a topic and a T (or for live mode). It records the build time, searches, the number of repos left after each filter, and the sampling seed (F1.7).
- Membership follows F1.2 and F1.3. Searches never hit GitHub's 1,000-result cap without being split further.
- **No look-ahead:** a test on the generated searches confirms that backtest builds have no upper star bound, no sort by current popularity, and no filter on archived status.
- If more than about 400 repos qualify, the builder takes a reproducible random sample seeded from the topic and T, never the top 400.
- An interrupted build can be resumed without paying for the requests again.

---

### T2.2b Universe metrics
**Goal:** Fill in the screening metrics for every member, then write the final universe file.
**Implements:** F1.1, F1.6, F3.2 · **Depends on:** T2.2a, T1.3, T1.4 · **P0 · M**

**Done when:**
- Every row has the F3.2 metrics, including the star growth rate formula, and the file is valid against the universe contract.
- Off-topic repos are removed using the README at T (F1.1). This is a "should": it reduces leaks from matching against current text. Removals are counted in the header.
- Reruns reuse cached responses.
- In backtest mode, the finished universe file passes the future-data guard (T1.5).
- A backtest build of 400 repos (2.2a plus 2.2b) fits in about one GitHub token's hourly limit. Report the actual request count.

---

### T2.3 👤 Build and commit backtest universes
**Goal:** Produce the frozen backtest universes everyone will use.
**Implements:** F1.6, F7.1 · **Depends on:** T2.2a, then T2.2b · **P0 · M (mostly waiting)** · *Candidate builds start by ~2:00. The first final universe by ~3:15, the rest by ~3:45. Commit each one as soon as it is ready.*

**Done when:**
- Universes as of T for at least two topics, ideally three, are committed. Each topic is built with a different teammate's token.
- Each file's header counts have been sanity-checked by a person: no topic is empty, and none is suspiciously full of famous repos.
- Response caches are shared with the demo machine.

---

### T2.4 Build live universes for the demo
**Goal:** Prebuilt live-mode universes, so the live demo doesn't wait on GitHub.
**Implements:** F8.1, F9.1, G1 · **Depends on:** T1.6, T2.2b · **P0 · S**

**Done when:** live universes exist for at least 2 topics (G1 needs live runs on 2), each built within about one token's hourly limit. Each shows its build time as its T, and the UI lists it.
