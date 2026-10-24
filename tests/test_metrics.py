"""Tests for repo metrics (T06). Offline: a fake GitHub behind httpx.MockTransport."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sleeper.config import load_settings
from sleeper.github import GitHubClient, NotFound
from sleeper.metrics import EVENT_PAGES, fetch_repo_metrics

AS_OF = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)


def ago(days: float = 0, hours: float = 0) -> str:
    return (AS_OF - timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def author(login=None, email=None, name=None) -> dict:
    return {"author": {"email": email, "name": name, "user": {"login": login} if login else None}}


def page(nodes, cursor=None) -> dict:
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def base_repo(**over) -> dict:
    repo = {
        "databaseId": 42,
        "nameWithOwner": "Owner/Repo",
        "url": "https://github.com/Owner/Repo",
        "stargazerCount": 600,
        "forkCount": 12,
        "createdAt": ago(days=200, hours=1),
        "description": "A test repo",
        "isEmpty": False,
        "isFork": False,
        "isArchived": False,
        "primaryLanguage": {"name": "Rust"},
        "licenseInfo": {"spdxId": "MIT"},
        "releases": {"totalCount": 7, "nodes": [{"createdAt": ago(days=6), "publishedAt": ago(days=5)}]},
        "defaultBranchRef": {
            "target": {
                "id": "C_head",
                "last": {"nodes": [{"committedDate": ago(days=1)}]},
                "recent": {
                    "totalCount": 5,
                    **page(
                        [
                            author(login="alice"),
                            author(login="Alice", email="a@x.org"),  # same user, case differs
                            author(email="bob@x.org", name="Bob"),  # no linked account
                            author(name="Carol"),
                            {"author": None},  # unknown author is not counted
                        ]
                    ),
                },
            }
        },
        # Newest first: 3 inside the 30-day window, then an older one that stops the count.
        "forks": page([{"createdAt": ago(days=d)} for d in (1, 10, 29.9, 31, 40)]),
    }
    repo.update(over)
    return repo


def event(kind: str, days: float, actor: int) -> dict:
    return {"type": kind, "created_at": ago(days=days), "actor": {"id": actor, "login": f"u{actor}"}}


class FakeGitHub:
    """Serves one repo. Records every request so tests can count them."""

    def __init__(self, repo: dict | None = None, *, events=None, readme=(200, "# Hello\n\nWorld é")) -> None:
        self.repo = base_repo() if repo is None else repo
        self.events = (
            [event("WatchEvent", 1, 1), event("PushEvent", 2, 2), event("WatchEvent", 3, 1), event("WatchEvent", 5, 3)]
            if events is None
            else events
        )
        self.readme = readme
        self.commit_pages: dict[str, dict] = {}  # cursor -> history page
        self.fork_pages: dict[str, dict] = {}  # cursor -> forks page
        self.requests: list[httpx.Request] = []
        self.events_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/graphql":
            body = json.loads(request.content)
            q, v = body["query"], body["variables"]
            if "node(id:" in q:
                return httpx.Response(200, json={"data": {"node": {"history": self.commit_pages[v["after"]]}}})
            if "stargazerCount" not in q:
                return httpx.Response(200, json={"data": {"repository": {"forks": self.fork_pages[v["after"]]}}})
            return httpx.Response(200, json={"data": {"repository": self.repo}})
        if path == "/repos/Owner/Repo/readme":
            status, text = self.readme
            return httpx.Response(status, text=text, headers={"content-type": "application/vnd.github.raw+json"})
        if path == "/repos/Owner/Repo/events":
            if self.events_status != 200:
                return httpx.Response(self.events_status, json={"message": "nope"})
            n = int(request.url.params.get("page", "1"))
            chunk = self.events[(n - 1) * 100 : n * 100]
            headers = {}
            if n * 100 < len(self.events):
                headers["link"] = f'<https://api.github.com/repos/Owner/Repo/events?per_page=100&page={n + 1}>; rel="next"'
            return httpx.Response(200, json=chunk, headers=headers)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    def count(self, pred) -> int:
        return sum(1 for r in self.requests if pred(r))


@pytest.fixture
def run(tmp_path):
    def go(fake: FakeGitHub, repo: str = "owner/repo"):
        gh = GitHubClient(token="t", offline=False, cache_dir=tmp_path / "gh", transport=httpx.MockTransport(fake))
        return fetch_repo_metrics(gh, repo, as_of=AS_OF)

    return go


# ---- each metric --------------------------------------------------------------


def test_identity_and_display(run):
    r = run(FakeGitHub())
    assert (r.repo, r.repo_id, r.url) == ("Owner/Repo", 42, "https://github.com/Owner/Repo")
    assert (r.is_fork, r.is_archived) == (False, False)
    m = r.metrics
    assert (m.description, m.language, m.license) == ("A test repo", "Rust", "MIT")


def test_popularity_and_newness(run):
    m = run(FakeGitHub()).metrics
    assert m.stars == 600
    assert m.created_at == AS_OF - timedelta(days=200, hours=1)
    assert m.age_days == 200
    assert m.stars_per_day == 3.0
    assert m.forks == 12


def test_stars_per_day_uses_at_least_one_day(run):
    m = run(FakeGitHub(base_repo(createdAt=ago(hours=3), stargazerCount=50))).metrics
    assert m.age_days == 0
    assert m.stars_per_day == 50.0


def test_forks_30d_counts_window_only(run):
    assert run(FakeGitHub()).metrics.forks_30d == 3


def test_forks_30d_pages_until_old_fork(run):
    fake = FakeGitHub(base_repo(forks=page([{"createdAt": ago(days=1)}] * 100, cursor="f1")))
    fake.fork_pages["f1"] = page([{"createdAt": ago(days=2)}] * 20 + [{"createdAt": ago(days=45)}], cursor="f2")
    assert run(fake).metrics.forks_30d == 120
    assert fake.count(lambda r: r.url.path == "/graphql") == 2  # stopped at the old fork, no third page


def test_forks_30d_unknown_when_page_cap_hit(run):
    fake = FakeGitHub(base_repo(forks=page([{"createdAt": ago(days=1)}] * 100, cursor="f1")))
    for i in range(1, 10):
        fake.fork_pages[f"f{i}"] = page([{"createdAt": ago(days=1)}] * 100, cursor=f"f{i + 1}")
    assert run(fake).metrics.forks_30d is None
    assert fake.count(lambda r: r.url.path == "/graphql") == 5  # 1 repo query + 4 fork pages


def test_star_events_distinct_in_window(run):
    events = [
        event("WatchEvent", 1, 1),
        event("WatchEvent", 2, 1),  # same user again
        event("WatchEvent", 3, 2),
        event("ForkEvent", 4, 3),
        event("WatchEvent", 29, 4),
        event("WatchEvent", 31, 5),  # outside the window
    ]
    m = run(FakeGitHub(events=events)).metrics
    assert m.star_events_30d == 3
    assert m.star_events_capped is False


def test_star_events_capped_when_feed_full(run):
    events = [event("WatchEvent" if i % 3 == 0 else "PushEvent", i / 100, i) for i in range(300)]
    fake = FakeGitHub(events=events)
    m = run(fake).metrics
    assert m.star_events_30d == 100
    assert m.star_events_capped is True
    assert fake.count(lambda r: r.url.path.endswith("/events")) == EVENT_PAGES


def test_star_events_stop_paging_at_old_event(run):
    events = [event("WatchEvent", 1, i) for i in range(50)] + [event("PushEvent", 31, 99)] * 250
    fake = FakeGitHub(events=events)
    m = run(fake).metrics
    assert (m.star_events_30d, m.star_events_capped) == (50, False)
    assert fake.count(lambda r: r.url.path.endswith("/events")) == 1


def test_star_events_unknown_on_error(run):
    fake = FakeGitHub()
    fake.events_status = 403
    m = run(fake).metrics
    assert (m.star_events_30d, m.star_events_capped) == (None, None)
    assert m.stars == 600  # the rest still works


def test_commits_and_authors(run):
    m = run(FakeGitHub()).metrics
    assert m.commits_90d == 5
    assert m.authors_90d == 3  # alice, bob@x.org, carol
    assert m.last_commit_at == AS_OF - timedelta(days=1)


def test_authors_page_through_history(run):
    repo = base_repo()
    recent = repo["defaultBranchRef"]["target"]["recent"]
    recent.update(totalCount=150, **page([author(login="alice")] * 100, cursor="c1"))
    fake = FakeGitHub(repo)
    fake.commit_pages["c1"] = page([author(login="bob")] * 49 + [author(login="alice")])
    m = run(fake).metrics
    assert (m.commits_90d, m.authors_90d) == (150, 2)


def test_authors_unknown_beyond_page_cap(run):
    repo = base_repo()
    repo["defaultBranchRef"]["target"]["recent"].update(totalCount=5000, **page([author(login="a")] * 100, cursor="c1"))
    fake = FakeGitHub(repo)
    for i in range(1, 20):
        fake.commit_pages[f"c{i}"] = page([author(login=f"u{i}")] * 100, cursor=f"c{i + 1}")
    m = run(fake).metrics
    assert (m.commits_90d, m.authors_90d) == (5000, None)
    assert fake.count(lambda r: r.url.path == "/graphql") == 10


def test_empty_repo_has_zero_commits(run):
    m = run(
        FakeGitHub(base_repo(isEmpty=True, defaultBranchRef=None, releases={"totalCount": 0, "nodes": []}))
    ).metrics
    assert (m.commits_90d, m.authors_90d, m.last_commit_at) == (0, 0, None)
    assert (m.releases, m.last_release_at) == (0, None)


def test_missing_history_is_unknown_not_zero(run):
    # e.g. a GraphQL timeout on a huge history: partial data, fields null
    m = run(FakeGitHub(base_repo(defaultBranchRef=None))).metrics
    assert (m.commits_90d, m.authors_90d, m.last_commit_at) == (None, None, None)


def test_releases(run):
    m = run(FakeGitHub()).metrics
    assert m.releases == 7
    assert m.last_release_at == AS_OF - timedelta(days=5)  # publishedAt wins over createdAt


def test_readme_chars(run):
    assert run(FakeGitHub()).metrics.readme_chars == len("# Hello\n\nWorld é")


def test_readme_absent_is_zero(run):
    assert run(FakeGitHub(readme=(404, '{"message": "Not Found"}'))).metrics.readme_chars == 0


def test_readme_error_is_unknown(run):
    assert run(FakeGitHub(readme=(403, '{"message": "Forbidden"}'))).metrics.readme_chars is None


@pytest.mark.parametrize("info", [None, {"spdxId": "NOASSERTION"}, {"spdxId": None}])
def test_unknown_license_is_none(run, info):
    assert run(FakeGitHub(base_repo(licenseInfo=info))).metrics.license is None


def test_missing_display_fields(run):
    m = run(FakeGitHub(base_repo(description=None, primaryLanguage=None))).metrics
    assert (m.description, m.language) == (None, None)


def test_repo_not_found(run):
    fake = FakeGitHub()
    fake.repo = None
    with pytest.raises(NotFound):
        run(fake)


# ---- requests -------------------------------------------------------------------


def test_typical_repo_takes_three_requests(run):
    fake = FakeGitHub()
    run(fake)
    assert len(fake.requests) == 3  # 1 GraphQL, README, 1 events page


def test_reproducible_from_cache(tmp_path):
    fake = FakeGitHub()
    kw = dict(token="t", cache_dir=tmp_path / "gh", transport=httpx.MockTransport(fake))
    first = fetch_repo_metrics(GitHubClient(offline=False, **kw), "owner/repo", as_of=AS_OF)
    again = fetch_repo_metrics(GitHubClient(offline=True, **kw), "owner/repo", as_of=AS_OF)
    assert first == again
    assert len(fake.requests) == 3


# ---- live -------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("repo", ["BurntSushi/ripgrep", "astral-sh/uv", "sgasser/pasteguard"])
def test_live_metrics_sanity(repo):
    if not load_settings().github_token:
        pytest.skip("GITHUB_TOKEN not set")
    with GitHubClient(offline=False) as gh:
        r = fetch_repo_metrics(gh, repo)
        rest = gh.get(f"/repos/{repo}")
    m = r.metrics
    assert r.repo.lower() == repo.lower() and r.repo_id == rest["id"]
    assert m.stars == rest["stargazers_count"] > 100
    assert m.forks == rest["forks_count"]
    assert m.created_at.isoformat().replace("+00:00", "Z") == rest["created_at"]
    assert m.language == rest["language"]
    assert m.license == (rest.get("license") or {}).get("spdx_id")
    assert m.age_days > 30 and m.stars_per_day > 0
    assert m.readme_chars and m.readme_chars > 1000
    assert m.releases and m.releases > 0 and m.last_release_at is not None
    assert m.last_commit_at is not None and m.commits_90d is not None and m.commits_90d > 0
    assert m.authors_90d is None or 1 <= m.authors_90d <= m.commits_90d
    assert m.star_events_30d is not None and m.star_events_capped is not None
    assert m.forks_30d is None or m.forks_30d <= m.forks
