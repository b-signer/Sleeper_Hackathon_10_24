"""Tests for the data tools (T09, W1). No network except tests marked live."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from sleeper import fakes
from sleeper.agent import data_tools
from sleeper.agent.data_tools import (
    README_ACCEPT,
    README_PATH,
    DataTools,
    ReadmeUnavailable,
    default_readme_source,
    fake_readme_source,
    github_readme_source,
)
from sleeper.config import load_settings
from sleeper.contracts.tools import BrowseArgs, ErrorOutput, Filter, ReadmeArgs, ReadmeOutput, RepoArgs
from sleeper.contracts.universe import Universe, UniverseRow
from sleeper.github import RAW_ACCEPT, GitHubClient

RAW_CT = {"content-type": "application/vnd.github.raw; charset=utf-8"}


@pytest.fixture(scope="module")
def u() -> Universe:
    return fakes.fake_universe()


def real_universe(u: Universe) -> Universe:
    """The fake universe under a real-looking topic, so it uses the GitHub source."""
    return u.model_copy(update={"header": u.header.model_copy(update={"topic": "rust-devtools"})})


def client(tmp_path, handler=None, *, offline=False) -> GitHubClient:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network touched: {request.url}")

    return GitHubClient(
        token="test-token",
        offline=offline,
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler or fail),
        max_wait=0,
        max_attempts=1,
        sleep=lambda s: None,
    )


def code(out) -> str:
    assert isinstance(out, ErrorOutput), out
    return out.error.code


# ---- browse / repo_details -------------------------------------------------


def test_browse_and_details_delegate(u):
    args = BrowseArgs(filters=[Filter(field="stars", op=">=", value=100)], sort_by="momentum", sort_dir="desc", limit=10)
    dt = DataTools(u)
    assert dt.browse(args, "c_001") == fakes.browse_output(u, args, "c_001")
    out = dt.repo_details(RepoArgs(repo="ACME/Quiver"), "c_002")
    assert out == fakes.repo_details_output(u, "acme/quiver", "c_002")
    assert out.repo == "acme/quiver"
    assert code(dt.repo_details(RepoArgs(repo="nobody/nothing"), "c_003")) == "NOT_IN_UNIVERSE"


# ---- readme: fake ----------------------------------------------------------


def test_readme_fake(u):
    dt = DataTools(u)
    row = u.get("acme/quiver")
    full = fakes.fake_readme(row)
    assert full
    for max_chars in (500, 6000, 12000):
        out = dt.readme(ReadmeArgs(repo="ACME/Quiver", max_chars=max_chars), "c_001")
        assert isinstance(out, ReadmeOutput)
        assert out.repo == "acme/quiver"
        assert out.text == full[:max_chars]
        assert out.truncated == (len(full) > max_chars)
        assert out.total_chars == len(full)
    assert dt.readme(ReadmeArgs(repo="acme/quiver", max_chars=500), "c_001").truncated is (len(full) > 500)
    assert code(dt.readme(ReadmeArgs(repo="mystery/unknown-cli"), "c_002")) == "NOT_FOUND"
    assert code(dt.readme(ReadmeArgs(repo="nobody/nothing"), "c_003")) == "NOT_IN_UNIVERSE"
    # Same as T03's stand-in for every repo.
    for r in u.rows:
        assert dt.readme(ReadmeArgs(repo=r.repo), "c_009") == fakes.readme_output(u, r.repo, "c_009")


# ---- readme: GitHub (mocked) -----------------------------------------------


def test_readme_github_ok(u, tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["accept"]))
        return httpx.Response(200, text="# Quiver\n" + "x" * 7000, headers=RAW_CT)

    dt = DataTools(real_universe(u), readme_source=github_readme_source(client(tmp_path, handler)))
    out = dt.readme(ReadmeArgs(repo="ACME/Quiver"), "c_001")
    assert isinstance(out, ReadmeOutput)
    assert out.text == ("# Quiver\n" + "x" * 7000)[:6000]
    assert out.truncated and out.total_chars == 7009
    assert seen == [("/repos/acme/quiver/readme", RAW_ACCEPT)]
    assert README_PATH.format(repo="acme/quiver") == "/repos/acme/quiver/readme" and README_ACCEPT == RAW_ACCEPT


def test_readme_github_empty_body_is_empty_text(u, tmp_path):
    src = github_readme_source(client(tmp_path, lambda r: httpx.Response(200, content=b"", headers=RAW_CT)))
    out = DataTools(real_universe(u), readme_source=src).readme(ReadmeArgs(repo="acme/quiver"), "c_001")
    assert isinstance(out, ReadmeOutput)
    assert out.text == "" and out.total_chars == 0 and not out.truncated


def test_readme_github_404_is_not_found(u, tmp_path):
    src = github_readme_source(client(tmp_path, lambda r: httpx.Response(404, json={"message": "Not Found"})))
    out = DataTools(real_universe(u), readme_source=src).readme(ReadmeArgs(repo="acme/quiver"), "c_001")
    assert code(out) == "NOT_FOUND"


def test_readme_http_error_is_upstream(u, tmp_path):
    src = github_readme_source(client(tmp_path, lambda r: httpx.Response(451, json={"message": "blocked"})))
    out = DataTools(real_universe(u), readme_source=src).readme(ReadmeArgs(repo="acme/quiver"), "c_001")
    assert code(out) == "UPSTREAM" and "451" in out.error.message


def test_readme_offline_cache_miss_is_upstream(u, tmp_path):
    src = github_readme_source(client(tmp_path, offline=True))
    with pytest.raises(ReadmeUnavailable):
        src(u.get("acme/quiver"))
    out = DataTools(real_universe(u), readme_source=src).readme(ReadmeArgs(repo="acme/quiver"), "c_001")
    assert code(out) == "UPSTREAM"
    assert "offline cache" in out.error.message


def test_readme_offline_served_from_cache(u, tmp_path):
    online = client(tmp_path, lambda r: httpx.Response(200, text="# Cached readme", headers=RAW_CT))
    real = real_universe(u)
    first = DataTools(real, readme_source=github_readme_source(online)).readme(ReadmeArgs(repo="acme/quiver"), "c_001")
    assert first.text == "# Cached readme"
    offline = client(tmp_path, offline=True)  # same cache, network fails the test
    out = DataTools(real, readme_source=github_readme_source(offline)).readme(ReadmeArgs(repo="acme/quiver"), "c_002")
    assert isinstance(out, ReadmeOutput) and out.text == "# Cached readme"
    assert offline.stats.cache_hits == 1 and offline.stats.network_requests == 0


def test_readme_zero_chars_no_request(u):
    calls = []

    def source(row: UniverseRow) -> str | None:
        calls.append(row.repo)
        return "should not be used"

    row = next(r for r in u.rows if r.metrics.readme_chars is not None and r.metrics.readme_chars > 0)
    zero = row.model_copy(update={"metrics": row.metrics.model_copy(update={"readme_chars": 0})})
    real = real_universe(u)
    real = real.model_copy(update={"rows": [zero if r.repo == row.repo else r for r in real.rows]})
    out = DataTools(real, readme_source=source).readme(ReadmeArgs(repo=row.repo), "c_001")
    assert code(out) == "NOT_FOUND" and calls == []


def test_unexpected_source_error_is_upstream(u):
    def boom(row):
        raise RuntimeError("bug")

    assert code(DataTools(u, readme_source=boom).readme(ReadmeArgs(repo="acme/quiver"), "c_001")) == "UPSTREAM"


# ---- source selection and laziness -----------------------------------------


def test_default_readme_source(u, monkeypatch):
    made = []
    monkeypatch.setattr(data_tools, "GitHubClient", lambda **kw: made.append(kw) or pytest.fail("client created"))
    assert default_readme_source(u) is fakes.fake_readme
    assert fake_readme_source() is fakes.fake_readme
    src = default_readme_source(real_universe(u))
    assert getattr(src, "is_github", False)
    dt = DataTools(real_universe(u))
    dt.browse(BrowseArgs(), "c_001")
    dt.repo_details(RepoArgs(repo="acme/quiver"), "c_002")
    assert made == []  # no client (and so no network) until the first readme


def test_github_default_client_settings(u, monkeypatch, tmp_path):
    made = []

    def factory(**kw):
        made.append(kw)
        return client(tmp_path, lambda r: httpx.Response(200, text="hi", headers=RAW_CT))

    monkeypatch.setattr(data_tools, "GitHubClient", factory)
    src = github_readme_source()
    assert made == []
    assert src(u.get("acme/quiver")) == "hi"
    assert src(u.get("acme/quiver")) == "hi"
    assert made == [{"max_wait": 30, "max_attempts": 2, "timeout": 10}]


def test_lazy_source_created_once(u, monkeypatch):
    calls = []

    def factory(universe):
        calls.append(1)
        time.sleep(0.05)  # widen the race window
        return fakes.fake_readme

    monkeypatch.setattr(data_tools, "default_readme_source", factory)
    dt = DataTools(u)
    assert calls == []
    barrier = threading.Barrier(8)

    def call(i):
        barrier.wait()
        return dt.readme(ReadmeArgs(repo="acme/quiver"), f"c_{i:03d}")

    with ThreadPoolExecutor(8) as pool:
        outs = list(pool.map(call, range(8)))
    assert len(calls) == 1
    assert all(isinstance(o, ReadmeOutput) for o in outs)


def test_parallel_readmes(u):
    dt = DataTools(u)
    repos = [r.repo for r in u.rows if r.metrics.readme_chars]
    with ThreadPoolExecutor(8) as pool:
        outs = list(pool.map(lambda i: dt.readme(ReadmeArgs(repo=repos[i % len(repos)]), f"c_{i:03d}"), range(32)))
    assert all(isinstance(o, ReadmeOutput) for o in outs)
    assert [o.call_id for o in outs] == [f"c_{i:03d}" for i in range(32)]


# ---- output sizes ----------------------------------------------------------


def synthetic_universe(n: int = 200) -> Universe:
    base = fakes.fake_universe()
    desc = ("A fast, friendly command-line tool for developers that does useful things with code, files and builds. " * 2)[:120]
    rows = []
    for i in range(n):
        r = base.rows[i % len(base.rows)]
        m = r.metrics.model_copy(update={"description": desc, "readme_chars": max(r.metrics.readme_chars or 0, 9000)})
        rows.append(r.model_copy(update={"repo": f"owner{i}/repo{i}", "repo_id": 1_000_000 + i,
                                         "url": f"https://github.com/owner{i}/repo{i}", "metrics": m}))
    counts = base.header.counts.model_copy(update={"search_total": n, "eligible": n, "rows": n})
    header = base.header.model_copy(update={"counts": counts})
    return Universe.model_validate({"header": header.model_dump(), "rows": [r.model_dump() for r in rows]})


def size(out) -> int:
    return len(out.model_dump_json())


def test_output_sizes():
    big = synthetic_universe()
    dt = DataTools(big)
    sizes = {}
    for limit in (10, 25, 50):
        args = BrowseArgs(filters=[Filter(field="forks_30d", op=">=", value=0)], sort_by="stars_per_day", limit=limit)
        sizes[f"browse_{limit}"] = size(dt.browse(args, "c_001"))
    sizes["repo_details"] = max(size(dt.repo_details(RepoArgs(repo=r.repo), "c_001")) for r in big.rows)
    sizes["readme_default"] = size(dt.readme(ReadmeArgs(repo="owner0/repo0"), "c_001"))
    print({k: (v, v // 4) for k, v in sizes.items()})  # chars, ~tokens
    assert sizes["browse_50"] <= 16_000
    assert sizes["repo_details"] <= 1_500
    assert sizes["readme_default"] <= 6_300


# ---- live ------------------------------------------------------------------


@pytest.mark.live
def test_readme_live(u, tmp_path):
    if not load_settings().github_token:
        pytest.skip("GITHUB_TOKEN not set")
    gh = GitHubClient(cache_dir=tmp_path, offline=False, max_wait=30, max_attempts=2, timeout=10)
    row = u.rows[0].model_copy(update={"repo": "astral-sh/uv"})
    text = github_readme_source(gh)(row)
    assert text and "uv" in text
    assert not text.lstrip().startswith("{")  # raw markdown, not the JSON envelope
    print(f"astral-sh/uv README: {len(text)} chars")
