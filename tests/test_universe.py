"""Tests for the universe builder and criteria scores (T07). Offline: fake search + fake metrics."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from sleeper.cli import main
from sleeper.contracts.universe import RepoMetrics, Universe
from sleeper.github import GitHubClient, NotFound
from sleeper.metrics import RepoMetricsResult
from sleeper.scoring import SCORING, UNKNOWN, percentiles, score_universe
from sleeper.universe import (
    TOPICS_FILE,
    Topic,
    build_universe,
    exclusion_reason,
    is_awesome_list,
    load_topics,
    sample_order,
    search_qualifiers,
    write_universe,
)

AS_OF = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)


# ---- fakes ---------------------------------------------------------------------


def item(i: int, name: str | None = None, **over) -> dict:
    full = name or f"own{i}/repo{i}"
    it = {"id": i, "full_name": full, "name": full.split("/")[1], "fork": False, "archived": False, "topics": []}
    it.update(over)
    return it


class FakeSearch:
    """Serves /search/repositories. `results` maps the query text before the qualifiers to items.

    Applies the stars:lo..hi qualifier to items that carry "stars", so star-range splitting works.
    """

    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.queries: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/repositories", request.url
        params = parse_qs(urlparse(str(request.url)).query)
        q = params["q"][0]
        self.queries.append(q)
        base = q.split(" stars:")[0]
        stars = q.split(" stars:")[1].split()[0]
        lo, hi = (int(x) for x in stars.split(".."))
        items = [it for it in self.results[base] if lo <= it.get("stars", lo) <= hi]
        page, per = int(params.get("page", ["1"])[0]), int(params["per_page"][0])
        chunk = items[(page - 1) * per : page * per] if page * per <= 1000 else []
        return httpx.Response(200, json={"total_count": len(items), "incomplete_results": False, "items": chunk})


def gh_for(fake, tmp_path) -> GitHubClient:
    return GitHubClient(token="t", offline=False, cache_dir=tmp_path / "gh", transport=httpx.MockTransport(fake))


def metrics(**over) -> RepoMetrics:
    base = dict(
        stars=100,
        stars_per_day=1.0,
        forks=10,
        forks_30d=2,
        star_events_30d=3,
        star_events_capped=False,
        commits_90d=20,
        authors_90d=2,
        releases=3,
        last_commit_at=AS_OF - timedelta(days=5),
        readme_chars=2000,
        created_at=AS_OF - timedelta(days=100),
        age_days=100,
    )
    base.update(over)
    return RepoMetrics(**base)


class FakeFetch:
    """Stands in for fetch_repo_metrics: metrics derived from the repo number, plus overrides."""

    def __init__(self, missing=(), forks=(), archived=(), renames=None) -> None:
        self.missing, self.forks, self.archived = set(missing), set(forks), set(archived)
        self.renames = renames or {}
        self.calls: list[str] = []
        self.as_of: set[datetime] = set()

    def __call__(self, gh, repo: str, *, as_of=None) -> RepoMetricsResult:
        self.calls.append(repo)
        self.as_of.add(as_of)
        if repo in self.missing:
            raise NotFound(404, "POST", "/graphql", "gone")
        i = int(repo.split("/")[0].removeprefix("own"))
        canonical, rid = self.renames.get(repo, (repo, i))
        m = metrics(stars=50 + i, stars_per_day=i / 10, created_at=AS_OF - timedelta(days=3000 - i), age_days=3000 - i)
        return RepoMetricsResult(
            repo=canonical,
            repo_id=rid,
            url=f"https://github.com/{canonical}",
            metrics=m,
            is_fork=repo in self.forks,
            is_archived=repo in self.archived,
        )


TOPIC = Topic(id="t", label="Test topic", queries=("q1", "q2"))


def build(tmp_path, results, fetch=None, **kw) -> tuple[Universe, FakeSearch, FakeFetch]:
    fake, fetch = FakeSearch(results), fetch or FakeFetch()
    u = build_universe(gh_for(fake, tmp_path), kw.pop("topic", TOPIC), built_at=AS_OF, fetch=fetch, progress=lambda m: None, **kw)
    return u, fake, fetch


# ---- topics --------------------------------------------------------------------


def test_topics_file_defines_the_demo_topics():
    topics = load_topics(TOPICS_FILE)
    assert {"rust-devtools", "local-first"} <= set(topics)
    for t in topics.values():
        assert t.label and t.queries
        assert (t.stars_min, t.stars_max, t.created_after) == (50, 2000, date(2025, 1, 1))


def test_search_qualifiers():
    assert search_qualifiers(TOPIC, 50, 2000) == "stars:50..2000 created:>=2025-01-01 fork:true"
    t = Topic(id="t", label="t", queries=("q",), created_before=date(2026, 3, 31))
    assert search_qualifiers(t, 1, 9) == "stars:1..9 created:2025-01-01..2026-03-31 fork:true"


# ---- search, exclusion, dedupe ------------------------------------------------------


def test_exclusion_reasons():
    assert exclusion_reason(item(1)) is None
    assert exclusion_reason(item(1, fork=True, archived=True)) == "fork"
    assert exclusion_reason(item(1, archived=True)) == "archived"
    assert exclusion_reason(item(1, "x/awesome-local-first")) == "awesome_list"
    assert exclusion_reason(item(1, topics=["awesome-list"])) == "awesome_list"
    assert is_awesome_list("x/Awesome-Rust") and not is_awesome_list("awesome/tool")  # owner name doesn't count


def test_search_exclusion_and_dedupe(tmp_path):
    q1 = [item(1), item(2), item(3, fork=True), item(4, archived=True)]
    q2 = [item(2), item(5, "own5/awesome-crdt"), item(6), item(1)]  # 1 and 2 also matched by q1
    u, fake, fetch = build(tmp_path, {"q1": q1, "q2": q2})
    c = u.header.counts
    assert c.search_total == 6
    assert c.excluded == {"archived": 1, "awesome_list": 1, "fork": 1}
    assert c.eligible == 3 and c.rows == 3 and not c.sampled
    assert sorted(r.repo for r in u.rows) == ["own1/repo1", "own2/repo2", "own6/repo6"]
    assert sorted(fetch.calls) == ["own1/repo1", "own2/repo2", "own6/repo6"]  # no metrics for excluded repos
    assert fetch.as_of == {AS_OF}  # one as_of for every repo
    assert all("fork:true" in q for q in fake.queries)
    assert u.header.queries == [f"q1 {search_qualifiers(TOPIC, 50, 2000)}", f"q2 {search_qualifiers(TOPIC, 50, 2000)}"]


def test_search_pages_and_splits_over_the_1000_cap(tmp_path):
    many = [item(i, stars=50 + i % 1900) for i in range(1, 1501)]
    topic = Topic(id="t", label="t", queries=("big",))
    u, fake, _ = build(tmp_path, {"big": many}, topic=topic, max_size=5)
    assert u.header.counts.search_total == 1500  # all found despite the 1000-per-search cap
    assert len(u.header.queries) >= 2 and all("stars:50..2000" not in q for q in u.header.queries)


def test_dropped_while_fetching_is_counted_and_backfilled(tmp_path):
    results = {"q1": [item(i) for i in range(1, 31)], "q2": []}
    fetch = FakeFetch(missing={"own3/repo3"}, forks={"own4/repo4"}, archived={"own5/repo5"})
    u, _, fetch = build(tmp_path, results, fetch=fetch, max_size=29)
    c = u.header.counts
    # 30 eligible, 3 dropped at fetch time: all 30 fetched, 27 rows
    assert c.rows == 27 and c.excluded == {"archived": 1, "fork": 1, "not_found": 1}
    assert c.eligible == c.search_total - sum(c.excluded.values()) == 27
    assert c.sampled  # 30 eligible before fetching > 29


def test_rename_duplicates_are_dropped(tmp_path):
    results = {"q1": [item(1), item(2)], "q2": []}
    fetch = FakeFetch(renames={"own2/repo2": ("own1/repo1", 1)})  # own2/repo2 redirects to repo 1
    u, _, _ = build(tmp_path, results, fetch=fetch)
    assert [r.repo for r in u.rows] == ["own1/repo1"] and u.header.counts.excluded == {"duplicate": 1}


# ---- seeded sampling --------------------------------------------------------------


def test_sample_is_seeded_random_not_top(tmp_path):
    results = {"q1": [item(i) for i in range(1, 101)], "q2": []}
    a, _, _ = build(tmp_path / "a", results, seed=7, max_size=10)
    b, _, _ = build(tmp_path / "b", {"q1": list(reversed(results["q1"])), "q2": []}, seed=7, max_size=10)
    c, _, _ = build(tmp_path / "c", results, seed=8, max_size=10)
    ids = lambda u: [r.repo_id for r in u.rows]  # noqa: E731
    assert ids(a) == ids(b)  # same seed, same set of repos -> same sample, whatever the search order
    assert ids(a) != ids(c)
    assert a.header.counts.sampled and a.header.counts.rows == 10 and a.header.seed == 7
    assert ids(a) != list(range(91, 101)) and ids(a) != list(range(1, 11))  # not the top or bottom 10


def test_sample_order_is_a_permutation():
    items = [item(i) for i in range(20)]
    order = sample_order(items, 7)
    assert sorted(it["id"] for it in order) == list(range(20))
    assert [it["id"] for it in order] == [it["id"] for it in sample_order(list(reversed(items)), 7)]


# ---- scoring -------------------------------------------------------------------


def test_percentiles():
    assert percentiles([10, 20, 30]) == [0.0, 50.0, 100.0]
    assert percentiles([5, 5, 9]) == [25.0, 25.0, 100.0]  # ties share the average rank
    assert percentiles([None, 1, 2]) == [UNKNOWN, 0.0, 100.0]  # None doesn't take a rank
    assert percentiles([3]) == [50.0] and percentiles([None, None]) == [UNKNOWN, UNKNOWN]


def test_scores_are_in_range_and_monotonic():
    ms = [
        metrics(
            stars=50 + 10 * i,
            stars_per_day=0.1 * i,
            forks_30d=i,
            commits_90d=i,
            authors_90d=i,
            releases=i,
            last_commit_at=AS_OF - timedelta(days=100 - i),
            readme_chars=100 * i,
            created_at=AS_OF - timedelta(days=400 - i),
            age_days=400 - i,
        )
        for i in range(10)
    ]
    scores = score_universe(ms)
    for crit in ("popularity", "momentum", "quality", "newness"):
        vals = [getattr(s, crit) for s in scores]
        assert vals == sorted(vals) and vals[0] == 0.0 and vals[-1] == 100.0, crit


def test_each_input_moves_its_criterion():
    base = [metrics(stars=s, created_at=AS_OF - timedelta(days=d), age_days=d) for s, d in ((100, 200), (300, 150), (500, 50))]

    def bumped(**over):
        return score_universe([base[0].model_copy(update=over), *base[1:]])[0]

    s0 = score_universe(base)[0]
    assert bumped(stars=10_000).popularity > s0.popularity
    assert bumped(stars_per_day=99.0).momentum > s0.momentum
    assert bumped(forks_30d=99).momentum > s0.momentum
    assert bumped(star_events_30d=999).momentum == s0.momentum  # not a momentum input (D13)
    for f, v in (("commits_90d", 999), ("authors_90d", 99), ("releases", 99), ("readme_chars", 99_999),
                 ("last_commit_at", AS_OF)):
        assert bumped(**{f: v}).quality > s0.quality, f
    assert bumped(created_at=AS_OF - timedelta(days=1), age_days=1).newness > s0.newness


def test_none_metrics_count_as_median():
    ms = [
        metrics(stars_per_day=None, forks_30d=None, commits_90d=None, authors_90d=None, releases=None,
                last_commit_at=None, readme_chars=None),
        metrics(stars=200, stars_per_day=2.0, forks_30d=5, commits_90d=50, authors_90d=5, releases=9, readme_chars=9000),
        metrics(stars=300, stars_per_day=0.1, forks_30d=0, commits_90d=1, authors_90d=1, releases=0, readme_chars=10),
    ]
    s = score_universe(ms)[0]
    assert (s.momentum, s.quality) == (UNKNOWN, UNKNOWN)
    assert all(0 <= v <= 100 for sc in score_universe(ms) for v in sc.model_dump().values())
    assert score_universe([]) == []


def test_scoring_paragraph_is_documented():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[1] / "docs" / "scoring.md").read_text(encoding="utf-8")
    assert SCORING in " ".join(doc.split())


# ---- contract and CLI --------------------------------------------------------------


def test_universe_file_is_valid_against_the_contract(tmp_path):
    u, _, _ = build(tmp_path, {"q1": [item(i) for i in range(1, 8)], "q2": [item(9)]})
    path = write_universe(u, tmp_path / "out" / "t.json")
    back = Universe.model_validate(json.loads(path.read_text()))
    h = back.header
    assert back == u and h.built_at == AS_OF and h.builder.startswith("sleeper ") and " @ " in h.builder
    assert h.scoring == SCORING and h.github_requests == 2 and h.build_seconds is not None
    assert (h.topic, h.topic_label, h.stars_min, h.stars_max, h.created_after) == ("t", "Test topic", 50, 2000, date(2025, 1, 1))
    assert [r.repo for r in back.rows] == sorted(r.repo for r in back.rows)


def test_cli_build_universe(tmp_path, monkeypatch):
    topics = tmp_path / "topics.toml"
    topics.write_text('[mini]\nlabel = "Mini"\nqueries = ["q1"]\n')
    fake = FakeSearch({"q1": [item(1), item(2)]})
    monkeypatch.setattr("sleeper.universe.fetch_repo_metrics", FakeFetch())
    monkeypatch.setattr("sleeper.github.GitHubClient", lambda: gh_for(fake, tmp_path))
    out = tmp_path / "u.json"
    assert main(["build-universe", "mini", "--topics", str(topics), "--out", str(out), "--seed", "3"]) == 0
    u = Universe.model_validate_json(out.read_text())
    assert u.header.topic == "mini" and u.header.seed == 3 and u.header.counts.rows == 2
    assert main(["build-universe", "nope", "--topics", str(topics)]) == 2


def test_universes_dir_is_not_gitignored():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["git", "check-ignore", "-q", "data/universes/local-first.json"], cwd=root)
    assert r.returncode == 1  # 1 = not ignored
