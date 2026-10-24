"""Tests for the GitHub access layer (T05). No network except tests marked live."""

from __future__ import annotations

import json
import os
import shutil

import httpx
import pytest
import respx

from sleeper.config import load_settings
from sleeper.github import (
    STAR_ACCEPT,
    GitHubClient,
    GitHubError,
    GraphQLError,
    MissingTokenError,
    NotFound,
    OfflineCacheMiss,
    RateLimitExceeded,
    request_key,
)

API = "https://api.github.com"


class FakeClock:
    """Injected clock + sleep: sleeping just advances time, so tests are instant."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def fail_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network touched: {request.method} {request.url}")

    return httpx.MockTransport(handler)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(tmp_path, clock):
    def make(**kw) -> GitHubClient:
        kw.setdefault("token", "test-token")
        kw.setdefault("offline", False)
        kw.setdefault("cache_dir", tmp_path / "github")
        return GitHubClient(sleep=clock.sleep, clock=clock.time, **kw)

    return make


# ---- token ------------------------------------------------------------------


@respx.mock
def test_requests_use_configured_token(make_client):
    route = respx.get(f"{API}/repos/o/r").mock(return_value=httpx.Response(200, json={"id": 1}))
    assert make_client(token="abc123").get("/repos/o/r") == {"id": 1}
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer abc123"
    assert sent.headers["accept"] == "application/vnd.github+json"


def test_missing_token_fails_clearly(make_client):
    gh = make_client(token="", transport=fail_transport())
    with pytest.raises(MissingTokenError, match="GITHUB_TOKEN is not set"):
        gh.get("/repos/o/r")


def test_token_and_settings_read_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    monkeypatch.setenv("SLEEPER_OFFLINE", "true")
    monkeypatch.setenv("SLEEPER_CACHE_DIR", str(tmp_path / "c"))
    gh = GitHubClient()
    assert gh.token == "from-env"
    assert gh.offline is True
    assert gh.cache.dir == tmp_path / "c" / "github"


# ---- cache ------------------------------------------------------------------


@respx.mock
def test_repeated_request_served_from_cache(make_client):
    route = respx.get(f"{API}/repos/o/r").mock(return_value=httpx.Response(200, json={"stars": 5}))
    gh = make_client()
    first = gh.request("GET", "/repos/o/r")
    second = gh.request("GET", "/repos/o/r")
    assert route.call_count == 1
    assert (first.from_cache, second.from_cache) == (False, True)
    assert second.data == {"stars": 5}
    assert gh.stats.network_requests == 1 and gh.stats.cache_hits == 1


@respx.mock
def test_refresh_bypasses_cache(make_client):
    route = respx.get(f"{API}/repos/o/r").mock(return_value=httpx.Response(200, json={}))
    gh = make_client()
    gh.get("/repos/o/r")
    gh.get("/repos/o/r", refresh=True)
    assert route.call_count == 2


@respx.mock
def test_cache_is_portable_and_secret_free(make_client, tmp_path):
    respx.get(f"{API}/repos/o/r").mock(return_value=httpx.Response(200, json={"name": "r"}))
    make_client(token="SECRET").get("/repos/o/r")

    files = list((tmp_path / "github").glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "SECRET" not in text and str(tmp_path) not in text
    assert json.loads(text)["data"] == {"name": "r"}

    # "Another machine": copy the cache elsewhere, go offline with no token.
    other = tmp_path / "elsewhere" / "github"
    shutil.copytree(tmp_path / "github", other)
    gh = make_client(token="", offline=True, cache_dir=other, transport=fail_transport())
    assert gh.get("/repos/o/r") == {"name": "r"}


def test_request_key_ignores_param_order():
    a = request_key("GET", f"{API}/search/repositories?q=x&per_page=100", "a")
    b = request_key("get", f"{API}/search/repositories?per_page=100&q=x", "a")
    assert a == b
    assert a != request_key("GET", f"{API}/search/repositories?q=y&per_page=100", "a")
    assert a != request_key("GET", f"{API}/search/repositories?q=x&per_page=100", "other-accept")


@respx.mock
def test_404_is_cached_and_raises_not_found(make_client):
    route = respx.get(f"{API}/repos/o/gone").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    gh = make_client()
    for _ in range(2):
        with pytest.raises(NotFound):
            gh.get("/repos/o/gone")
    assert route.call_count == 1


# ---- offline ----------------------------------------------------------------


def test_offline_cache_miss_raises_distinct_error(make_client):
    gh = make_client(offline=True, transport=fail_transport())
    with pytest.raises(OfflineCacheMiss, match="not in the response cache"):
        gh.get("/repos/o/missing")
    assert gh.stats.network_requests == 0


def test_offline_with_no_token_and_cache_miss_is_still_offline_error(make_client):
    gh = make_client(token="", offline=True, transport=fail_transport())
    with pytest.raises(OfflineCacheMiss):
        gh.graphql("{ viewer { login } }")


# ---- rate limits ------------------------------------------------------------


def scripted(responses):
    """MockTransport that returns the given responses in order and records requests."""
    calls = []

    def handler(request):
        calls.append(request)
        return responses[len(calls) - 1]

    return httpx.MockTransport(handler), calls


def test_primary_rate_limit_exhausted_waits_for_reset_then_succeeds(make_client, clock):
    reset = clock.now + 120
    transport, calls = scripted([
        httpx.Response(403, json={"message": "API rate limit exceeded"},
                       headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset))}),
        httpx.Response(200, json={"ok": True}, headers={"x-ratelimit-remaining": "4999", "x-ratelimit-reset": str(int(reset + 3600))}),
    ])
    gh = make_client(transport=transport)
    assert gh.get("/repos/o/r") == {"ok": True}
    assert len(calls) == 2
    assert clock.sleeps == [121.0]  # until reset, plus one second


def test_secondary_rate_limit_uses_retry_after(make_client, clock):
    transport, calls = scripted([
        httpx.Response(429, headers={"retry-after": "30"}),
        httpx.Response(403, json={"message": "You have exceeded a secondary rate limit"}),
        httpx.Response(200, json=[]),
    ])
    gh = make_client(transport=transport)
    assert gh.get("/repos/o/r/commits") == []
    assert clock.sleeps == [30.0, 120.0]  # retry-after, then 60 * 2**attempt


def test_proactive_wait_when_remaining_hits_zero(make_client, clock):
    reset = clock.now + 50
    transport, calls = scripted([
        httpx.Response(200, json={"a": 1}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset))}),
        httpx.Response(200, json={"b": 2}),
    ])
    gh = make_client(transport=transport)
    gh.get("/repos/o/a")
    assert clock.sleeps == []
    gh.get("/repos/o/b")  # waits for the reset before sending
    assert clock.sleeps == [51.0]


def test_gives_up_past_max_wait_without_crashing(make_client, clock):
    reset = clock.now + 3000
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset))})
    transport, calls = scripted([limited] * 10)
    gh = make_client(transport=transport, max_wait=600)
    with pytest.raises(RateLimitExceeded, match="max_wait is 600s"):
        gh.get("/repos/o/r")
    assert clock.sleeps == []  # refuses up front rather than sleeping 50 minutes


def test_gives_up_after_bounded_retries(make_client, clock):
    transport, calls = scripted([httpx.Response(429, headers={"retry-after": "1"})] * 10)
    gh = make_client(transport=transport, max_attempts=4)
    with pytest.raises(RateLimitExceeded, match="after 4 attempts"):
        gh.get("/repos/o/r")
    assert len(calls) == 4 and sum(clock.sleeps) <= gh.max_wait


def test_server_errors_are_retried(make_client, clock):
    transport, calls = scripted([httpx.Response(502), httpx.Response(200, json={"ok": 1})])
    assert make_client(transport=transport).get("/x") == {"ok": 1}
    assert clock.sleeps == [1.0]


def test_plain_403_is_not_retried(make_client, clock):
    transport, calls = scripted([httpx.Response(403, json={"message": "Resource not accessible"})])
    with pytest.raises(GitHubError, match="403"):
        make_client(transport=transport).get("/x")
    assert len(calls) == 1 and clock.sleeps == []


def test_graphql_rate_limited_is_retried(make_client, clock):
    transport, calls = scripted([
        httpx.Response(200, json={"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]},
                       headers={"x-ratelimit-reset": str(int(clock.now + 10))}),
        httpx.Response(200, json={"data": {"viewer": {"login": "me"}}}),
    ])
    assert make_client(transport=transport).graphql("{ viewer { login } }") == {"viewer": {"login": "me"}}
    assert clock.sleeps == [11.0]


# ---- convenience methods ----------------------------------------------------


@respx.mock
def test_paginate_follows_link_header_and_caches_each_page(make_client):
    page2 = f"{API}/repos/o/r/stargazers?per_page=2&page=2"
    route = respx.get(url__startswith=f"{API}/repos/o/r/stargazers").mock(side_effect=[
        httpx.Response(200, json=[{"starred_at": "a"}, {"starred_at": "b"}],
                       headers={"link": f'<{page2}>; rel="next", <{page2}>; rel="last"'}),
        httpx.Response(200, json=[{"starred_at": "c"}]),
    ])
    gh = make_client()
    stars = list(gh.paginate("/repos/o/r/stargazers", per_page=2, accept=STAR_ACCEPT))
    assert [s["starred_at"] for s in stars] == ["a", "b", "c"]
    assert route.calls[0].request.headers["accept"] == STAR_ACCEPT
    # second pass is fully cached
    assert len(list(gh.paginate("/repos/o/r/stargazers", per_page=2, accept=STAR_ACCEPT))) == 3
    assert route.call_count == 2


@respx.mock
def test_paginate_search_items_and_max_pages(make_client):
    nxt = f"{API}/search/repositories?q=x&page=2"
    respx.get(url__startswith=f"{API}/search/repositories").mock(return_value=httpx.Response(
        200, json={"total_count": 500, "items": [{"id": 1}]}, headers={"link": f'<{nxt}>; rel="next"'}))
    items = list(make_client().paginate("/search/repositories", {"q": "x"}, max_pages=1))
    assert items == [{"id": 1}]


@respx.mock
def test_graphql_post_returns_data_and_caches(make_client):
    route = respx.post(f"{API}/graphql").mock(return_value=httpx.Response(200, json={"data": {"x": 1}}))
    gh = make_client()
    assert gh.graphql("query{x}", {"a": 1}) == {"x": 1}
    assert gh.graphql("query{x}", {"a": 1}) == {"x": 1}
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {"query": "query{x}", "variables": {"a": 1}}


@respx.mock
def test_graphql_errors_raise_unless_partial_allowed(make_client):
    body = {"data": {"a": {"id": 1}, "b": None}, "errors": [{"type": "NOT_FOUND", "message": "no repo b"}]}
    respx.post(f"{API}/graphql").mock(return_value=httpx.Response(200, json=body))
    gh = make_client()
    with pytest.raises(GraphQLError, match="no repo b"):
        gh.graphql("q")
    assert gh.graphql("q", allow_partial=True) == {"a": {"id": 1}, "b": None}


@respx.mock
def test_raw_readme_returned_as_text(make_client):
    respx.get(f"{API}/repos/o/r/readme").mock(return_value=httpx.Response(
        200, text="# Hello", headers={"content-type": "application/vnd.github.raw; charset=utf-8"}))
    from sleeper.github import RAW_ACCEPT

    assert make_client().get("/repos/o/r/readme", accept=RAW_ACCEPT) == "# Hello"


# ---- live -------------------------------------------------------------------


@pytest.mark.live
def test_live_repo_and_graphql(tmp_path):
    if not load_settings().github_token:  # reads .env too
        pytest.skip("GITHUB_TOKEN not set")
    with GitHubClient(cache_dir=tmp_path, offline=False) as gh:
        assert gh.get("/repos/astral-sh/uv")["full_name"] == "astral-sh/uv"
        data = gh.graphql('{ repository(owner:"astral-sh", name:"uv") { stargazerCount } }')
        assert data["repository"]["stargazerCount"] > 0
