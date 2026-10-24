"""Tests for S1 past universes. Offline: fake universe, FixtureHistory, mock GitHub transport."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

import sleeper.scoring
from sleeper import fakes
from sleeper.agent.data_tools import ReadmeUnavailable
from sleeper.backtest import past_universe as pu
from sleeper.backtest.history import FixtureHistory
from sleeper.backtest.past_universe import (
    as_of_end,
    asof_readme_source,
    build_past_universe,
    make_refetcher,
    write_past_universe,
)
from sleeper.backtest.types import PastUniverseReport
from sleeper.contracts.universe import CriteriaScores, RepoMetrics, Universe
from sleeper.github import RAW_ACCEPT, GitHubClient
from sleeper.metrics import RepoMetricsResult

AS_OF = date(2026, 8, 1)  # 80 days before the fake universe's built_at (2026-10-20 12:00)
END = datetime(2026, 8, 1, 23, 59, 59, tzinfo=UTC)
RAW_CT = {"content-type": "application/vnd.github.raw; charset=utf-8"}


@pytest.fixture(scope="module")
def u() -> Universe:
    return fakes.fake_universe()


def history_for(u: Universe, skip: tuple[str, ...] = ()) -> FixtureHistory:
    """Half of today's stars for every repo, except ``skip``."""
    return FixtureHistory({r.repo: r.metrics.stars // 2 for r in u.rows if r.repo not in skip})


def build(u, **kw):
    return build_past_universe(u, AS_OF, kw.pop("history", None) or history_for(u), source_path="data/universes/fake.json", **kw)


def test_as_of_end():
    assert as_of_end(AS_OF) == END


def test_drops_and_report_counts(u):
    past, report = build(u, history=history_for(u, skip=("weekend/todo-term",)))
    repos = {r.repo for r in past.rows}
    # cargo-sprout (45 days old) and phantom-build (60) were created after the date.
    assert "fernwork/cargo-sprout" not in repos and "ghostly/phantom-build" not in repos
    assert "weekend/todo-term" not in repos
    assert report.dropped == {"created_after_as_of": 2, "no_history": 1}
    assert report.rows_in == 10 and report.rows_out == 7 == len(past.rows) == len(report.rows)
    assert report.history == "fixture" and report.source_universe == "data/universes/fake.json"
    assert report.source_built_at == u.header.built_at and report.as_of == AS_OF
    assert not report.refetched_windows
    assert any("not refetched" in c for c in report.caveats) and any("README" in c for c in report.caveats)
    note = next(n for n in report.rows if n.repo == "acme/quiver")
    assert (note.stars_now, note.stars_as_of, note.estimated) == (340, 170, False)
    c = past.header.counts
    assert c.rows == 7 and c.search_total == u.header.counts.search_total
    assert c.eligible == u.header.counts.eligible - 3
    assert c.excluded["backtest_created_after_as_of"] == 2 and c.excluded["fork"] == 1


def test_stars_age_and_velocity_recomputed(u):
    past, _ = build(u)
    row = past.get("acme/quiver")
    created = u.get("acme/quiver").metrics.created_at
    age = (END - created).days
    assert row.metrics.stars == 170
    assert row.metrics.age_days == age == 140 - 80
    assert row.metrics.stars_per_day == round(170 / age, 4)
    assert row.metrics.created_at == created
    assert row.repo_id == u.get("acme/quiver").repo_id and row.url == u.get("acme/quiver").url


def test_leaky_fields_nulled(u):
    past, _ = build(u)
    for row in past.rows:
        m = row.metrics
        assert m.forks is None and m.forks_30d is None
        assert m.star_events_30d is None and m.star_events_capped is None
        assert m.last_commit_at is None or m.last_commit_at <= END
        assert m.last_release_at is None or m.last_release_at <= END
        assert m.readme_chars == u.get(row.repo).metrics.readme_chars  # kept, with a caveat
    quiver = past.get("acme/quiver").metrics  # active now: nothing about its activity then is known
    assert (quiver.last_commit_at, quiver.commits_90d, quiver.authors_90d) == (None, None, None)
    assert (quiver.releases, quiver.last_release_at) == (None, None)
    # fmt-lite: last commit 210 days before built_at, before the as-of 90-day window -> known zero.
    fmt = past.get("oldmill/fmt-lite").metrics
    assert fmt.last_commit_at == u.get("oldmill/fmt-lite").metrics.last_commit_at
    assert (fmt.commits_90d, fmt.authors_90d) == (0, 0)
    assert fmt.releases == 4 and fmt.last_release_at == u.get("oldmill/fmt-lite").metrics.last_release_at
    # todo-term: last commit on the as-of day itself (kept), but inside the window -> counts unknown.
    todo = past.get("weekend/todo-term").metrics
    assert todo.last_commit_at == u.get("weekend/todo-term").metrics.last_commit_at
    assert (todo.commits_90d, todo.authors_90d) == (None, None)
    assert todo.releases == 0 and todo.last_release_at is None
    # manpage-rs: latest release 45 days ago, after the date -> release count then unknown.
    man = past.get("docsfirst/manpage-rs").metrics
    assert (man.releases, man.last_release_at) == (None, None)


def test_refetch_path(u):
    calls = []

    def refetch(repo: str, when: datetime) -> RepoMetrics:
        calls.append((repo, when))
        if repo == "bigco/turbo-lint":
            raise RuntimeError("boom")
        return RepoMetrics(
            stars=99_999,  # current: must be ignored
            forks=12_345,
            forks_30d=7,
            commits_90d=33,
            authors_90d=3,
            releases=5,
            last_release_at=END - timedelta(days=10),
            last_commit_at=END - timedelta(days=1),
            readme_chars=1,
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
            age_days=1,
        )

    past, report = build(u, refetch=refetch)
    assert {r for r, _ in calls} == {r.repo for r in past.rows} and all(w == END for _, w in calls)
    assert report.refetched_windows
    assert any("Refetch failed for 1" in c for c in report.caveats)
    m = past.get("acme/quiver").metrics
    assert (m.forks_30d, m.commits_90d, m.authors_90d) == (7, 33, 3)
    assert m.last_commit_at == END - timedelta(days=1)
    assert m.releases == 5 and m.last_release_at == END - timedelta(days=10)
    assert m.stars == 170 and m.forks is None and m.readme_chars == 5400
    assert m.created_at == u.get("acme/quiver").metrics.created_at
    # The failing repo falls back to the no-refetch rules.
    t = past.get("bigco/turbo-lint").metrics
    assert (t.forks_30d, t.commits_90d, t.last_commit_at) == (None, None, None)


def test_refetch_release_after_date_is_nulled(u):
    def refetch(repo, when):
        return RepoMetrics(stars=1, releases=9, last_release_at=END + timedelta(days=3),
                           last_commit_at=END + timedelta(days=1), created_at=datetime(2020, 1, 1, tzinfo=UTC), age_days=1)

    past, _ = build(u, refetch=refetch)
    m = past.get("acme/quiver").metrics
    assert (m.releases, m.last_release_at, m.last_commit_at) == (None, None, None)


def test_make_refetcher(monkeypatch):
    seen = {}
    metrics = RepoMetrics(stars=1, created_at=datetime(2020, 1, 1, tzinfo=UTC), age_days=1)

    def fake_fetch(gh, repo, *, as_of=None):
        seen.update(gh=gh, repo=repo, as_of=as_of)
        return RepoMetricsResult(repo=repo, repo_id=1, url="u", metrics=metrics, is_fork=False, is_archived=False)

    monkeypatch.setattr("sleeper.metrics.fetch_repo_metrics", fake_fetch)
    gh = object()
    assert make_refetcher(gh)("acme/quiver", END) is metrics
    assert seen == {"gh": gh, "repo": "acme/quiver", "as_of": END}


def test_custom_scorer_used_and_scoring_text_kept(u):
    got = []

    def scorer(metrics):
        got.append(list(metrics))
        return [CriteriaScores(popularity=1, momentum=2, quality=3, newness=4) for _ in metrics]

    past, _ = build(u, scorer=scorer)
    assert len(got) == 1 and [m.stars for m in got[0]] == [r.metrics.stars for r in past.rows]
    assert all(r.scores.quality == 3 for r in past.rows)
    assert past.header.scoring == u.header.scoring  # this test module has no SCORING string


def test_custom_scorer_module_scoring_text(u):
    past, _ = build(u, scorer=fakes.score_rows)
    assert past.header.scoring == fakes.SCORING


def test_default_scorer_looked_up_at_call_time(u, monkeypatch):
    calls = []

    def replacement(metrics):
        calls.append(len(metrics))
        return [CriteriaScores(popularity=9, momentum=9, quality=9, newness=9) for _ in metrics]

    monkeypatch.setattr(sleeper.scoring, "score_universe", replacement)
    monkeypatch.setattr(sleeper.scoring, "SCORING", "new scoring text")
    past, _ = build(u)
    assert calls == [len(past.rows)]
    assert all(r.scores.popularity == 9 for r in past.rows)
    assert past.header.scoring == "new scoring text"


def test_default_scorer_rescores_within_past_set(u):
    past, _ = build(u)
    assert past.header.scoring == sleeper.scoring.SCORING
    expected = sleeper.scoring.score_universe([r.metrics for r in past.rows])
    assert [r.scores for r in past.rows] == expected
    assert max(r.scores.popularity for r in past.rows) == 100.0


def test_header(u):
    past, _ = build(u)
    h = past.header
    assert h.built_at == END
    assert h.builder.startswith("sleeper ") and "backtest as-of 2026-08-01 from 2026-10-20" in h.builder
    assert h.created_before == AS_OF
    assert h.topic == u.header.topic and h.queries == u.header.queries and h.seed == u.header.seed
    later = u.model_copy(update={"header": u.header.model_copy(update={"created_before": date(2026, 7, 1)})})
    assert build(later)[0].header.created_before == date(2026, 7, 1)  # an earlier bound is kept
    Universe.model_validate_json(past.model_dump_json())  # round-trips as a valid universe file


def test_write_paths(u, tmp_path):
    past, report = build(u)
    path = write_past_universe(past, report, tmp_path / "bt")
    assert path == tmp_path / "bt" / "fake@2026-08-01.json"
    assert Universe.model_validate_json(path.read_text(encoding="utf-8")) == past
    rpath = tmp_path / "bt" / "fake@2026-08-01.report.json"
    assert PastUniverseReport.model_validate(json.loads(rpath.read_text(encoding="utf-8"))) == report
    assert sorted(p.name for p in (tmp_path / "bt").iterdir()) == ["fake@2026-08-01.json", "fake@2026-08-01.report.json"]
    assert pu.BACKTEST_DIR.as_posix() == "data/universes/backtest"


# ---- README as of the date ---------------------------------------------------


def client(tmp_path, handler) -> GitHubClient:
    return GitHubClient(
        token="test-token",
        offline=False,
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        max_wait=0,
        max_attempts=1,
        sleep=lambda s: None,
    )


def test_asof_readme_source_reads_at_ref(u, tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        seen.append((request.url.path, q, request.headers["accept"]))
        if request.url.path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "abc123"}])
        return httpx.Response(200, text="# Old quiver", headers=RAW_CT)

    src = asof_readme_source(END, client(tmp_path, handler))
    assert src(u.get("acme/quiver")) == "# Old quiver"
    assert seen[0][0] == "/repos/acme/quiver/commits"
    assert seen[0][1] == {"until": ["2026-08-01T23:59:59Z"], "per_page": ["1"]}
    assert seen[1][0] == "/repos/acme/quiver/readme" and seen[1][1] == {"ref": ["abc123"]}
    assert seen[1][2] == RAW_ACCEPT
    assert src.as_of == END


def test_asof_readme_source_none_cases(u, tmp_path):
    no_commits = asof_readme_source(END, client(tmp_path / "a", lambda r: httpx.Response(200, json=[])))
    assert no_commits(u.get("acme/quiver")) is None

    def readme_404(request):
        if request.url.path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "abc"}])
        return httpx.Response(404, json={"message": "Not Found"})

    assert asof_readme_source(END, client(tmp_path / "b", readme_404))(u.get("acme/quiver")) is None


def test_asof_readme_source_errors_raise(u, tmp_path):
    src = asof_readme_source(END, client(tmp_path, lambda r: httpx.Response(451, json={"message": "blocked"})))
    with pytest.raises(ReadmeUnavailable, match="451"):
        src(u.get("acme/quiver"))
