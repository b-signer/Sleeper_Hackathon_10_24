"""S1 outcome snapshots: growth, from a current universe, from GitHub (mocked), and files."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime

import httpx
import pytest

from sleeper.backtest.outcomes import (
    GRAPHQL_BATCH,
    growth,
    load_snapshot,
    save_snapshot,
    snapshot_from_github,
    snapshot_from_universe,
    snapshot_path,
)
from sleeper.contracts import Universe, UniverseRow
from sleeper.fakes import fake_universe as _fake_universe
from sleeper.github import GitHubClient, GitHubError

TAKEN = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


def fake_universe() -> Universe:
    # Deep copy: fake rows share RepoMetrics objects with sleeper.fakes, and tests mutate them.
    return _fake_universe().model_copy(deep=True)


def _past(n: int | None = None) -> Universe:
    """The fake universe, moved to an as-of date (stars as they were), optionally cut to n rows."""
    u = fake_universe()
    u.header.built_at = datetime(2026, 4, 1, 23, 59, tzinfo=UTC)
    if n is not None:
        u.rows = u.rows[:n]
        u.header.counts.rows = n
    return u


def test_growth():
    assert growth(9, 99) == pytest.approx(math.log(10))
    assert growth(0, 0) == 0.0
    assert growth(10, 4) < 0
    assert growth(None, 5) is None and growth(5, None) is None


def test_snapshot_from_universe_matches_by_id_then_name():
    past, current = _past(), fake_universe()
    for r in current.rows:
        r.metrics.stars *= 2
    current.rows[0].repo = "acme-org/quiver"  # renamed; same repo_id
    current.rows[1].repo_id = 1  # id changed (unlikely) but name matches
    dropped = current.rows.pop()  # deleted since
    current.header.counts.rows -= 1

    snap = snapshot_from_universe(past, current, source="universe:current.json")
    assert (snap.topic, snap.as_of, snap.source) == ("fake", date(2026, 4, 1), "universe:current.json")
    assert snap.taken_at == current.header.built_at
    assert snap.missing == [dropped.repo]
    by = {o.repo: o for o in snap.outcomes}
    assert len(by) == len(past.rows)
    q = by["acme/quiver"]
    assert (q.stars_as_of, q.stars_now) == (340, 680)
    assert q.growth == pytest.approx(math.log(681 / 341))
    assert by["lumen-dev/tidepool"].stars_now == 360
    gone = by[dropped.repo]
    assert gone.stars_now is None and gone.growth is None and gone.stars_as_of == 60


# ---- GitHub path (offline: MockTransport) -------------------------------------


class FakeGitHub:
    """Answers batched repository() queries and /repositories/{id}."""

    def __init__(self, past: Universe) -> None:
        self.stars = {r.repo.lower(): (r.repo_id, r.metrics.stars + 100) for r in past.rows}
        self.by_id: dict[int, int] = {}  # REST-only answers: id -> stars
        self.rest_status: dict[int, int] = {}
        self.graphql_calls = 0
        self.rest_calls: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            self.graphql_calls += 1
            q = json.loads(request.content)["query"]
            data, errors = {}, []
            for alias, owner, name in re.findall(r'(r\d+): repository\(owner: "([^"]+)", name: "([^"]+)"\)', q):
                hit = self.stars.get(f"{owner}/{name}".lower())
                if hit is None:
                    data[alias] = None
                    errors.append({"type": "NOT_FOUND", "path": [alias]})
                else:
                    data[alias] = {"databaseId": hit[0], "nameWithOwner": f"{owner}/{name}", "stargazerCount": hit[1]}
            return httpx.Response(200, json={"data": data, **({"errors": errors} if errors else {})})
        m = re.fullmatch(r"/repositories/(\d+)", request.url.path)
        if m:
            rid = int(m.group(1))
            self.rest_calls.append(rid)
            if rid in self.by_id:
                return httpx.Response(200, json={"id": rid, "stargazers_count": self.by_id[rid]})
            return httpx.Response(self.rest_status.get(rid, 404), json={"message": "Not Found"})
        raise AssertionError(f"unexpected request {request.method} {request.url}")


def _gh(tmp_path, fake) -> GitHubClient:
    return GitHubClient(token="t", offline=False, cache_dir=tmp_path / "gh", transport=httpx.MockTransport(fake))


def test_snapshot_from_github_all_found(tmp_path):
    past = _past()
    fake = FakeGitHub(past)
    snap = snapshot_from_github(past, _gh(tmp_path, fake), taken_at=TAKEN)
    assert snap.source == "github" and snap.taken_at == TAKEN and snap.missing == []
    assert all(o.stars_now == o.stars_as_of + 100 for o in snap.outcomes)
    assert fake.graphql_calls == 1 and fake.rest_calls == []


def test_snapshot_from_github_renamed_reused_deleted(tmp_path):
    past = _past()
    renamed, reused, deleted, private = past.rows[0], past.rows[1], past.rows[2], past.rows[3]
    fake = FakeGitHub(past)
    del fake.stars[renamed.repo.lower()]  # old name gone; REST by id finds it
    fake.by_id[renamed.repo_id] = 999
    fake.stars[reused.repo.lower()] = (123, 5)  # name now belongs to another repo
    fake.by_id[reused.repo_id] = 777
    del fake.stars[deleted.repo.lower()]
    del fake.stars[private.repo.lower()]
    fake.rest_status[private.repo_id] = 403

    snap = snapshot_from_github(past, _gh(tmp_path, fake), taken_at=TAKEN)
    by = {o.repo: o for o in snap.outcomes}
    assert by[renamed.repo].stars_now == 999
    assert by[reused.repo].stars_now == 777
    assert sorted(snap.missing) == sorted([deleted.repo, private.repo])
    assert by[deleted.repo].stars_now is None and by[deleted.repo].growth is None  # missing, never zero
    assert sorted(fake.rest_calls) == sorted(r.repo_id for r in (renamed, reused, deleted, private))


def test_snapshot_from_github_batches(tmp_path):
    base = fake_universe()
    rows = [
        UniverseRow(**{**base.rows[0].model_dump(), "repo": f"o/r{i}", "repo_id": 10_000 + i, "url": f"https://github.com/o/r{i}"})
        for i in range(GRAPHQL_BATCH + 7)
    ]
    header = base.header.model_copy(update={"counts": base.header.counts.model_copy(update={"rows": len(rows)})})
    past = Universe(header=header, rows=rows)
    fake = FakeGitHub(past)
    snap = snapshot_from_github(past, _gh(tmp_path, fake), taken_at=TAKEN)
    assert fake.graphql_calls == 2
    assert len(snap.outcomes) == len(rows) and snap.missing == []


def test_github_server_error_raises_not_missing(tmp_path):
    past = _past(2)
    fake = FakeGitHub(past)
    del fake.stars[past.rows[0].repo.lower()]
    fake.rest_status[past.rows[0].repo_id] = 500
    gh = GitHubClient(
        token="t", offline=False, cache_dir=tmp_path / "gh", transport=httpx.MockTransport(fake), max_attempts=2, sleep=lambda s: None
    )
    with pytest.raises(GitHubError):
        snapshot_from_github(past, gh, taken_at=TAKEN)


# ---- Files ----------------------------------------------------------------------


def test_snapshot_path_and_roundtrip(tmp_path):
    assert str(snapshot_path(tmp_path / "fake-past.json")) == str(tmp_path / "fake-past.outcomes.json")
    snap = snapshot_from_universe(_past(), fake_universe(), source="universe:x", taken_at=TAKEN)
    path = save_snapshot(snap, snapshot_path(tmp_path / "fake-past.json"))
    assert load_snapshot(path) == snap
