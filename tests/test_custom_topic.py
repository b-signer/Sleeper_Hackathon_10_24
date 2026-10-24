"""Tests for custom topics: drafting, counting and building (sleeper.custom_topic) and the launcher's
endpoints (sleeper.server.topics). Offline: fake model, fake GitHub search, fake metrics."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from sleeper import custom_topic, fakes, universe as universe_mod
from sleeper.agent.types import ModelReply, ToolCallRequest
from sleeper.config import Settings
from sleeper.contracts import RepoMetrics, Universe, Usage
from sleeper.custom_topic import (
    CUSTOM_MAX_SIZE,
    PREFETCH_SLACK,
    QueryCount,
    TopicDraft,
    build_custom_universe,
    clean_queries,
    count_matches,
    custom_topic_id,
    draft_topic,
    estimate_requests,
    make_topic,
)
from sleeper.github import GitHubClient
from sleeper.metrics import RepoMetricsResult
from sleeper.server import FakeRunner, RunRefused, RunStore, create_app, server_config
from sleeper.server.runner import FAKE_RUN_DIR
from sleeper.server.topics import TopicService
from sleeper.universe import DEFAULT_SEED, exclusion_reason, sample_order

AS_OF = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)


# ---- fakes ---------------------------------------------------------------------


class FakeModel:
    model_id = "fake/drafter"

    def __init__(self, arguments: str) -> None:
        self.arguments = arguments
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools, *, tool_choice="auto", timeout_s=120.0) -> ModelReply:
        self.calls.append(messages)
        assert tool_choice == {"type": "function", "function": {"name": "topic"}}
        return ModelReply(
            content=None,
            tool_calls=[ToolCallRequest(id="t1", name="topic", arguments=self.arguments)],
            usage=Usage(prompt_tokens=300, completion_tokens=40, total_tokens=340, cost_usd=0.0001),
        )


def item(i: int, **over) -> dict:
    it = {"id": i, "full_name": f"own{i}/repo{i}", "name": f"repo{i}", "fork": False, "archived": False, "topics": []}
    return {**it, **over}


class FakeSearch:
    """/search/repositories: `results` maps the query text before the qualifiers to items."""

    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.requests = 0
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests += 1
        params = parse_qs(urlparse(str(request.url)).query)
        items = self.results[params["q"][0].split(" stars:")[0]]
        page, per = int(params.get("page", ["1"])[0]), int(params["per_page"][0])
        chunk = items[(page - 1) * per : page * per]
        return httpx.Response(200, json={"total_count": len(items), "incomplete_results": False, "items": chunk})


def factory(fake: FakeSearch, tmp_path: Path):
    return lambda: GitHubClient(token="t", offline=False, cache_dir=tmp_path / "gh", transport=httpx.MockTransport(fake))


def fake_metrics(gh, repo: str, *, as_of=None) -> RepoMetricsResult:
    i = int(repo.split("/")[0].removeprefix("own"))
    m = RepoMetrics(
        stars=50 + i, stars_per_day=i / 10, forks=3, forks_30d=1, star_events_30d=2, star_events_capped=False,
        commits_90d=10 + i, authors_90d=2, releases=1, last_commit_at=AS_OF - timedelta(days=3), readme_chars=900,
        created_at=AS_OF - timedelta(days=200 - i), age_days=200 - i,
    )
    return RepoMetricsResult(repo=repo, repo_id=i, url=f"https://github.com/{repo}", metrics=m, is_fork=False, is_archived=False)


# ---- ids and queries -----------------------------------------------------------------


def test_custom_topic_id():
    assert custom_topic_id("Python static-site generators!") == "custom-python-static-site-generators"
    assert custom_topic_id("???") == "custom-topic"
    assert len(custom_topic_id("x" * 200)) <= len("custom-") + 48


def test_clean_queries():
    assert clean_queries(["  topic:cli   language:Rust ", "", "language:rust TOPIC:CLI", "linter"]) == [
        "topic:cli language:Rust",
        "linter",
    ]
    with pytest.raises(ValueError, match="leave out stars:"):
        clean_queries(["topic:cli stars:>100"])
    with pytest.raises(ValueError, match="at least one"):
        clean_queries(["  ", ""])
    with pytest.raises(ValueError, match="at most 4"):
        clean_queries(["a", "b", "c", "d", "e"])


def test_make_topic_uses_product_defaults():
    topic = make_topic(TopicDraft(label="  Rust   linters ", queries=["topic:linter language:Rust"]))
    assert topic.id == "custom-rust-linters" and topic.label == "Rust linters"
    assert (topic.stars_min, topic.stars_max, topic.created_after.isoformat()) == (50, 2000, "2025-01-01")


# ---- draft -------------------------------------------------------------------------------


def test_draft_topic_parses_the_tool_call_and_drops_reserved_qualifiers():
    model = FakeModel(json.dumps({"label": "Rust linters", "queries": ["topic:linter language:Rust", "lint stars:>50", "topic:linter language:Rust"]}))
    draft, usage = draft_topic(model, "  rust   linters ")
    assert draft == TopicDraft(label="Rust linters", queries=["topic:linter language:Rust"])
    assert usage.cost_usd == 0.0001
    assert model.calls[0][-1]["content"] == "Topic: rust linters"


@pytest.mark.parametrize("arguments", ["not json", json.dumps({"label": "x"}), json.dumps({"label": "x", "queries": []})])
def test_draft_topic_unusable_reply(arguments):
    with pytest.raises(ValueError, match="unusable"):
        draft_topic(FakeModel(arguments), "rust linters")


def test_draft_topic_rejects_empty_and_long_text():
    with pytest.raises(ValueError, match="describe"):
        draft_topic(FakeModel("{}"), "   ")
    with pytest.raises(ValueError, match="under 300"):
        draft_topic(FakeModel("{}"), "x" * 301)


# ---- count -------------------------------------------------------------------------------


def test_count_matches_one_request_per_query(tmp_path):
    fake = FakeSearch({"q1": [item(i) for i in range(1, 8)], "q2": []})
    counts = count_matches(factory(fake, tmp_path)(), make_topic(TopicDraft(label="T", queries=["q1", "q2"])))
    assert counts == [QueryCount(query="q1", total=7), QueryCount(query="q2", total=0)]
    assert fake.requests == 2


def test_estimate_requests():
    assert estimate_requests([QueryCount(query="a", total=250), QueryCount(query="b", total=0)]) == 3 + 1 + 6 * 40
    assert estimate_requests([QueryCount(query="a", total=5000)]) == 10 + 6 * 40  # search stops at 1,000 results
    assert estimate_requests([QueryCount(query="a", total=7)]) == 1 + 6 * 7


# ---- build -------------------------------------------------------------------------------


def test_build_prefetches_the_sample_then_builds_from_it(tmp_path, monkeypatch):
    prefetched: list[tuple[str, datetime]] = []
    lock = threading.Lock()

    def recording_prefetch(gh, repo, *, as_of=None):
        with lock:
            prefetched.append((repo, as_of))
        return fake_metrics(gh, repo, as_of=as_of)

    built: list[str] = []
    monkeypatch.setattr(custom_topic, "fetch_repo_metrics", recording_prefetch)
    monkeypatch.setattr(universe_mod, "fetch_repo_metrics", lambda gh, repo, *, as_of=None: built.append(repo) or fake_metrics(gh, repo, as_of=as_of))
    items = [item(i) for i in range(1, 61)] + [item(99, fork=True)]
    fake = FakeSearch({"q1": items, "q2": items[:10]})
    topic = make_topic(TopicDraft(label="Test topic", queries=["q1", "q2"]))

    u = build_custom_universe(factory(fake, tmp_path), topic, built_at=AS_OF)

    eligible = [it for it in items if exclusion_reason(it) is None]
    expected = [it["full_name"] for it in sample_order(eligible, DEFAULT_SEED)[: CUSTOM_MAX_SIZE + PREFETCH_SLACK]]
    assert sorted(r for r, _ in prefetched) == sorted(expected)  # exactly the repos the builder will sample
    assert {a for _, a in prefetched} == {AS_OF}  # same as_of, so the builder's requests hit the cache
    assert built == expected[:CUSTOM_MAX_SIZE]
    assert u.header.topic == "custom-test-topic" and u.header.counts.rows == CUSTOM_MAX_SIZE
    assert u.header.counts.excluded == {"fork": 1}
    assert u.header.github_requests == fake.requests  # every client's requests, not just the builder's
    Universe.model_validate_json(u.model_dump_json())


def test_build_with_no_eligible_repos(tmp_path):
    fake = FakeSearch({"q1": [item(1, fork=True)]})
    with pytest.raises(ValueError, match="no repos matched"):
        build_custom_universe(factory(fake, tmp_path), make_topic(TopicDraft(label="T", queries=["q1"])))


# ---- service and endpoints -------------------------------------------------------------


def fake_universe_for(topic) -> Universe:
    u = fakes.fake_universe()
    header = u.header.model_copy(update={"topic": topic.id, "topic_label": topic.label})
    return u.model_copy(update={"header": header})


def topics_client(tmp_path, *, totals=None, build=None, refuse_github=None, refuse_draft=None) -> tuple[TestClient, Path]:
    totals = totals if totals is not None else {"topic:cli": 120, "cli in:name": 30}
    out = tmp_path / "custom"
    service = TopicService(
        out_dir=out,
        draft=lambda text: (TopicDraft(label=f"About {text}", queries=list(totals)), 0.0002),
        count=lambda topic: [QueryCount(query=q, total=totals.get(q, 0)) for q in topic.queries],
        build=build or (lambda topic, progress: (progress("  fetched metrics 2/2"), fake_universe_for(topic))[1]),
        refuse_draft=refuse_draft or (lambda: None),
        refuse_github=refuse_github or (lambda: None),
    )
    runner = FakeRunner(runs_dir=tmp_path / "runs", source=FAKE_RUN_DIR, delay_s=0)
    app = create_app(RunStore(tmp_path / "runs"), universe_dirs=[out], runner=runner, topics=service)
    return TestClient(app), out


def wait_job(client, job_id, timeout_s=5.0) -> dict:
    deadline = datetime.now().timestamp() + timeout_s
    while datetime.now().timestamp() < deadline:
        job = client.get(f"/api/topics/build/{job_id}").json()
        if job["state"] != "running":
            return job
    raise AssertionError("build did not finish")


def test_draft_endpoint_returns_a_counted_plan(tmp_path):
    client, _ = topics_client(tmp_path)
    plan = client.post("/api/topics/draft", json={"text": "rust clis"}).json()
    assert plan["label"] == "About rust clis" and plan["topic"] == "custom-about-rust-clis"
    assert plan["queries"] == [{"query": "topic:cli", "total": 120}, {"query": "cli in:name", "total": 30}]
    assert plan["total"] == 150 and plan["max_size"] == 40 and plan["warning"] is None and plan["cost_usd"] == 0.0002


@pytest.mark.parametrize(("totals", "warning"), [({"q": 0}, "No repos match"), ({"q": 7}, "Only 7"), ({"q": 4000}, "4000 matches")])
def test_plan_warns_when_too_few_or_too_many(tmp_path, totals, warning):
    client, _ = topics_client(tmp_path, totals=totals)
    assert warning in client.post("/api/topics/count", json={"label": "T", "queries": ["q"]}).json()["warning"]


def test_bad_queries_are_a_readable_400(tmp_path):
    client, _ = topics_client(tmp_path)
    res = client.post("/api/topics/count", json={"label": "T", "queries": ["topic:cli created:>2020-01-01"]})
    assert res.status_code == 400
    assert res.json()["detail"] == {"reason": "other", "message": "leave out created: in 'topic:cli created:>2020-01-01'; the star range and dates are added for you"}


def test_refusals_come_before_any_work(tmp_path):
    offline = lambda: RunRefused("Offline mode (SLEEPER_OFFLINE=1): new topics need the network.", "offline")  # noqa: E731
    client, _ = topics_client(tmp_path, refuse_github=offline, refuse_draft=offline)
    for path, body in [("/api/topics/draft", {"text": "x"}), ("/api/topics/count", {"label": "T", "queries": ["q"]}), ("/api/topics/build", {"label": "T", "queries": ["q"]})]:
        res = client.post(path, json=body)
        assert res.status_code == 400 and res.json()["detail"]["reason"] == "offline", path


def test_build_job_runs_to_done_and_the_universe_is_listed(tmp_path):
    client, out = topics_client(tmp_path)
    res = client.post("/api/topics/build", json={"label": "Rust CLIs", "queries": ["topic:cli"]})
    assert res.status_code == 202 and res.json()["state"] == "running"
    job = wait_job(client, res.json()["id"])
    assert job["state"] == "done" and job["fetched"] == 2 and job["to_fetch"] == 2
    assert job["universe"]["topic"] == "custom-rust-clis" and job["universe"]["file"] == (out / "custom-rust-clis.json").as_posix()
    listed = client.get("/api/universes").json()
    assert [u["topic"] for u in listed] == ["custom-rust-clis"]


def test_build_errors_are_reported_on_the_job(tmp_path):
    def failing(topic, progress):
        progress("Building universe ...")
        raise ValueError("no repos matched these searches")

    client, out = topics_client(tmp_path, build=failing)
    job = wait_job(client, client.post("/api/topics/build", json={"label": "T", "queries": ["q"]}).json()["id"])
    assert job["state"] == "error" and "no repos matched" in job["error"] and job["lines"] == ["Building universe ..."]
    assert not out.exists()


def test_one_build_at_a_time(tmp_path):
    release = threading.Event()

    def blocking(topic, progress):
        release.wait(5)
        return fake_universe_for(topic)

    client, _ = topics_client(tmp_path, build=blocking)
    first = client.post("/api/topics/build", json={"label": "A", "queries": ["q"]})
    second = client.post("/api/topics/build", json={"label": "B", "queries": ["q"]})
    release.set()
    assert first.status_code == 202 and second.status_code == 409
    assert wait_job(client, first.json()["id"])["state"] == "done"


def test_no_topic_service_is_404(tmp_path):
    runner = FakeRunner(runs_dir=tmp_path / "runs", source=FAKE_RUN_DIR, delay_s=0)
    client = TestClient(create_app(RunStore(tmp_path / "runs"), universe_dirs=[], runner=runner))
    assert client.post("/api/topics/draft", json={"text": "x"}).status_code == 404
    assert client.get("/api/topics/build/abc").status_code == 404


def settings(tmp_path, *, offline=False, github_token="t", openrouter_api_key="k") -> Settings:
    return Settings(
        github_token=github_token, openrouter_api_key=openrouter_api_key, dev_model="openai/gpt-5.6-luna",
        demo_model="openai/gpt-5.6-terra", offline=offline, cache_dir=tmp_path / "cache", runs_dir=tmp_path / "runs",
        universes_dir=tmp_path / "universes",
    )


def test_config_says_when_custom_topics_are_off(tmp_path):
    assert server_config(settings(tmp_path)).custom_topics_reason is None
    assert "SLEEPER_OFFLINE" in server_config(settings(tmp_path, offline=True)).custom_topics_reason
    assert "GITHUB_TOKEN" in server_config(settings(tmp_path, github_token=None)).custom_topics_reason
    assert "OPENROUTER_API_KEY" in server_config(settings(tmp_path, openrouter_api_key=None)).custom_topics_reason
