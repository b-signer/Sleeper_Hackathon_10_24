# Criteria scores

How the four 0–100 scores in every universe row (`CriteriaScores`, D8) are computed, by `src/sleeper/scoring.py` (T07). The same paragraph is written into each universe file's `header.scoring`.

Scores are percentiles within this universe (0 = lowest repo, 100 = highest; ties share the average rank), rounded to 0.1. Higher always means more of the criterion. Popularity: stars. Momentum: lifetime star velocity (stars_per_day) and forks created in the last 30 days (forks_30d), equally weighted (D13; star_events_30d is not used because the events feed caps it). Quality: a weighted mean of commits in the last 90 days capped at 150 (25%), distinct commit authors in the last 90 days capped at 4 (25%), number of releases capped at 40 (20%), recency of the last commit (20%), and has-a-real-README (10%: 100 if the README has at least 500 characters, 0 if shorter or missing, 50 if unknown). Each capped metric is clipped before its percentile, so every repo at or above the cap ties: volume beyond the cap earns nothing more, and README length beyond the threshold does not count. Newness: creation date, so the newest repo scores 100. A metric that is unknown (None) for a repo counts as the median (50) for that repo, neither rewarded nor penalised; the other repos' percentiles are computed among the known values only.

Notes:

- Quality caps (T18): quality used to be an equal mean of raw commits, authors, releases, recency and README length, so a repo with 981 commits or a 50,000-character README won by sheer volume. Now the volume metrics are capped before taking percentiles (`COMMITS_CAP`, `AUTHORS_CAP`, `RELEASES_CAP` in `scoring.py`, tuned so that roughly 25-40% of the rebuilt universes tie at each cap) and README length only counts as a small has-a-real-README check. A cap is used, not a log scale, because percentiles ignore any monotonic rescaling.
- Topic relevance (T18) is not a score: off-topic candidates are dropped before scoring (`counts.excluded["off_topic"]`), see `src/sleeper/relevance.py` and the `scope` strings in [`topics.toml`](../topics.toml).
- Percentiles are relative: the same repo can score differently in another topic's universe, or after a rebuild.
- "Underrated" is the agent's judgement, not a score. A sleeper typically shows low popularity with high momentum, quality or newness.
- Building a universe: `uv run sleeper build-universe <topic>` (topics in [`topics.toml`](../topics.toml)), written to `data/universes/<topic>.json`. See `src/sleeper/universe.py` for the search, exclusion and sampling steps.
