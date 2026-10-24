"""T08 universe checks: header sanity, the sample sheet, cache readiness and warming. Offline."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from sleeper import universe_check as uc
from sleeper.fakes import fake_universe
from sleeper.github import GitHubClient

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def u():
    return fake_universe().model_copy(deep=True)  # fake rows share objects with FAKE_REPOS


def gh(tmp_path, handler, offline=False):
    return GitHubClient(token="t", offline=offline, cache_dir=tmp_path / "github",
                        transport=httpx.MockTransport(handler), max_wait=0, max_attempts=1, sleep=lambda s: None)


def test_fake_universe_header_adds_up(u):
    assert uc.header_issues(u) == []


@pytest.mark.parametrize("name", ["local-first", "rust-devtools"])
def test_committed_universes_header_adds_up(name):
    path = REPO_ROOT / "data" / "universes" / f"{name}.json"
    if not path.is_file():
        pytest.skip("universe not built")
    assert uc.header_issues(uc.load(path)) == []


def test_header_issues_catch_bad_counts_and_rows(u):
    h = u.header
    bad = u.model_copy(update={"header": h.model_copy(update={
        "counts": h.counts.model_copy(update={"search_total": h.counts.search_total + 5}),
    })})
    assert any("search_total" in i for i in uc.header_issues(bad))

    row = u.rows[0]
    u.rows[0] = row.model_copy(update={"metrics": row.metrics.model_copy(update={"stars": 99_999})})
    assert any("far outside stars" in i for i in uc.header_issues(u))


def test_sample_is_deterministic_and_sheet_has_boxes(u):
    a, b = uc.sample(u, 5, 1), uc.sample(u, 5, 1)
    assert [r.repo for r in a] == [r.repo for r in b]
    sheet = uc.check_sheet(u, "fake.json", n=5, seed=1)
    assert sheet.count("[ ] [") == 5 and "Header issues:** 0" in sheet


def test_cache_report_and_warm(tmp_path, u):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path == "/repos/acme/quiver/readme":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, text="# readme, no papers")

    need = sum(1 for r in u.rows if r.metrics.readme_chars != 0)  # None (unknown) is fetched too
    before = uc.cache_report(u, tmp_path / "github")
    assert before.cached == 0 and len(before.missing) == need

    client = gh(tmp_path, handler)
    fetched, failed, stopped = uc.warm(u, client, floor=0)
    assert (fetched, failed, stopped) == (need, 0, False)
    after = uc.cache_report(u, tmp_path / "github")
    assert after.cached == need and after.missing == []  # a cached 404 counts as cached

    calls.clear()
    assert uc.warm(u, client, floor=0) == (0, 0, False) and calls == []  # nothing left to fetch


def test_warm_stops_at_the_rate_floor(tmp_path, u):
    client = gh(tmp_path, lambda req: httpx.Response(200, text="x"))
    client.rate_limits["core"] = (500, 0.0)
    assert uc.warm(u, client, floor=1_000) == (0, 0, True)


def test_cache_report_counts_papers(tmp_path, u):
    from sleeper.contracts.tools import LinkedPaper

    readme = "See https://arxiv.org/abs/2106.09685 for the method."
    client = gh(tmp_path, lambda req: httpx.Response(200, text=readme))
    assert uc.warm(u, client, floor=0, papers=_NoPapers())[2] is False
    r = uc.cache_report(u, tmp_path / "github")
    assert r.paper_repos == r.rows and len(r.papers_missing) == r.rows  # READMEs cached, papers not

    class Papers:
        def lookup(self, ids):
            return [LinkedPaper(arxiv_id=i, url="u", found=True) for i in ids]

    fetched, failed, _ = uc.warm(u, client, floor=0, papers=Papers())
    assert fetched == r.rows and failed == 0


class _NoPapers:
    def lookup(self, ids):
        from sleeper.papers import PapersUnavailable

        raise PapersUnavailable("offline")
