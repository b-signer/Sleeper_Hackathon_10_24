"""Tests for the topic relevance check (T18). Offline: fake model, fake GitHub README endpoint."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from sleeper.agent.spend import SpendGuardError
from sleeper.agent.types import ModelError, ModelReply
from sleeper.config import load_settings
from sleeper.contracts import Usage
from sleeper.contracts.universe import Universe
from sleeper.github import GitHubClient
from sleeper.relevance import (
    README_CHARS,
    CostCapExceeded,
    RelevanceChecker,
    RelevanceError,
    parse_verdict,
    prompt_sha256,
)
from sleeper.universe import Topic, build_universe

from test_universe import FakeFetch, FakeSearch, item  # noqa: E402  (tests dir is on sys.path)

AS_OF = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
SCOPE = "On topic: developer tools. Off topic: games."


class FakeModel:
    """A ChatModel: replies with on for even repo numbers, off for odd; or a fixed reply."""

    model_id = "fake/luna"

    def __init__(self, reply: str | None = None, cost: float = 0.0003, error: ModelError | None = None) -> None:
        self.reply, self.cost, self.error = reply, cost, error
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools, *, tool_choice="auto", timeout_s=120.0) -> ModelReply:
        assert tools == []
        self.calls.append(messages)
        if self.error:
            raise self.error
        text = self.reply
        if text is None:
            name = messages[1]["content"].split("\n")[0].removeprefix("Repository: ")
            n = int(name.split("/")[0].removeprefix("own"))
            text = json.dumps({"verdict": "on" if n % 2 == 0 else "off", "reason": f"repo {n}"})
        return ModelReply(content=text, tool_calls=[], usage=Usage(total_tokens=100, cost_usd=self.cost))


def readme_gh(tmp_path, readmes: dict[str, str] | None = None) -> tuple[GitHubClient, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        repo = path.removeprefix("/repos/").removesuffix("/readme")
        text = (readmes or {}).get(repo)
        if text is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, text=text)

    gh = GitHubClient(token="t", offline=False, cache_dir=tmp_path / "gh", transport=httpx.MockTransport(handler))
    return gh, seen


def checker(tmp_path, model=None, *, scope=SCOPE, spent=0.0, **kw) -> RelevanceChecker:
    return RelevanceChecker(
        "t", "Test topic", scope, model=model or FakeModel(), store_path=tmp_path / "rel" / "t.json",
        spent_usd=lambda: spent, **kw,
    )


# ---- parsing -------------------------------------------------------------------


def test_parse_verdict():
    assert parse_verdict('{"verdict": "on", "reason": "a linter"}') == ("on", "a linter")
    assert parse_verdict('Sure:\n```json\n{"verdict": "OFF", "reason": "a game"}\n```') == ("off", "a game")
    assert parse_verdict('{"verdict": "unsure", "reason": "thin"}') == ("unsure", "thin")
    for bad in (None, "", "on", "{not json}", '{"verdict": "maybe"}', "[1, 2]"):
        assert parse_verdict(bad)[0] == "unsure", bad


def test_malformed_reply_is_unsure_and_dropped(tmp_path):
    gh, _ = readme_gh(tmp_path)
    c = checker(tmp_path, FakeModel(reply="I think it is on topic"))
    v = c.check(gh, item(2))
    assert v.verdict == "unsure" and "unparseable" in v.reason


# ---- the check -----------------------------------------------------------------


def test_input_has_name_description_topics_and_readme_start(tmp_path):
    gh, seen = readme_gh(tmp_path, {"own2/repo2": "# Repo two\n" + "x" * 5000})
    model = FakeModel()
    c = checker(tmp_path, model)
    v = c.check(gh, item(2, description="A fast linter", topics=["cli", "linter"]))
    assert v.verdict == "on" and v.reason == "repo 2" and v.cost_usd == 0.0003
    system, user = model.calls[0][0]["content"], model.calls[0][1]["content"]
    assert SCOPE in system and "Test topic" in system
    assert "own2/repo2" in user and "A fast linter" in user and "cli, linter" in user
    assert "# Repo two" in user and "x" * README_CHARS not in user  # README truncated to README_CHARS
    assert seen == ["/repos/own2/repo2/readme"]  # the same request as metrics._readme_chars
    gh2, _ = readme_gh(tmp_path / "b")
    checker(tmp_path / "b", model).check(gh2, item(4))
    assert "(no README)" in model.calls[-1][1]["content"]


def test_verdicts_are_saved_and_reused(tmp_path):
    gh, _ = readme_gh(tmp_path)
    m1 = FakeModel()
    c1 = checker(tmp_path, m1)
    for i in range(1, 6):
        c1.check(gh, item(i))
    assert len(m1.calls) == 5
    data = json.loads((tmp_path / "rel" / "t.json").read_text())
    assert data["totals"]["checked"] == 5 and data["totals"]["on"] == 2 and data["totals"]["off"] == 3
    assert data["totals"]["cost_usd"] == pytest.approx(0.0015)
    assert data["model"] == "fake/luna" and data["prompt_sha256"] == prompt_sha256("Test topic", SCOPE)
    e = data["verdicts"][0]
    assert set(e) == {"repo_id", "repo", "verdict", "reason", "model", "prompt_sha256", "cost_usd"}

    m2 = FakeModel(error=ModelError("http", "must not be called"))
    c2 = checker(tmp_path, m2, spent=1e9)  # neither the model nor the spend guard is touched
    assert [c2.check(gh, item(i)).verdict for i in range(1, 6)] == ["off", "on", "off", "on", "off"]
    assert m2.calls == [] and c2.stats.new == 0 and c2.stats.checked == 5
    assert c2.summary() == "relevance fake/luna 5 checked, 3 off-topic, $0.00 (prompt %s)" % c2.sha[:8]


def test_changed_prompt_forces_a_recheck(tmp_path):
    gh, _ = readme_gh(tmp_path)
    checker(tmp_path).check(gh, item(2))
    m = FakeModel()
    c = checker(tmp_path, m, scope=SCOPE + " Also: libraries.")
    assert c.sha != prompt_sha256("Test topic", SCOPE)
    c.check(gh, item(2))
    assert len(m.calls) == 1
    data = json.loads((tmp_path / "rel" / "t.json").read_text())
    assert [v["prompt_sha256"] for v in data["verdicts"]] == [c.sha]  # stale verdicts are not kept


def test_cost_cap_aborts(tmp_path):
    gh, _ = readme_gh(tmp_path)
    c = checker(tmp_path, FakeModel(cost=0.2), cap_usd=0.5)
    for i in range(1, 4):  # 0.2, 0.4, 0.6: the third call goes over
        c.check(gh, item(i))
    with pytest.raises(CostCapExceeded, match=r"\$0.50 cap after 3 model calls"):
        c.check(gh, item(4))
    assert json.loads((tmp_path / "rel" / "t.json").read_text())["totals"]["checked"] == 3  # progress kept


def test_spend_guard_refuses_before_the_first_call(tmp_path, monkeypatch):
    gh, _ = readme_gh(tmp_path)
    m = FakeModel()
    c = checker(tmp_path, m, spent=44.8)
    with pytest.raises(SpendGuardError, match="would exceed"):
        c.check(gh, item(2))
    assert m.calls == []
    # the default path reads current_spend(cfg); stub it
    monkeypatch.setattr("sleeper.relevance.current_spend", lambda cfg: 44.9)
    c2 = RelevanceChecker("t", "T", SCOPE, model=m, store_path=tmp_path / "x.json", cfg=load_settings())
    with pytest.raises(SpendGuardError):
        c2.check(gh, item(2))
    monkeypatch.setattr("sleeper.relevance.current_spend", lambda cfg: 1.0)
    assert c2.check(gh, item(2)).verdict == "on" and len(m.calls) == 1


def test_model_error_is_unsure_and_not_saved(tmp_path):
    gh, _ = readme_gh(tmp_path)
    c = checker(tmp_path, FakeModel(error=ModelError("timeout", "stalled")))
    v = c.check(gh, item(2))
    assert v.verdict == "unsure" and "model error" in v.reason
    assert c.cached(2) is None


def test_default_checker_needs_a_key_only_for_new_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    gh, _ = readme_gh(tmp_path)
    cfg = load_settings()
    cfg = type(cfg)(**{**cfg.__dict__, "openrouter_api_key": None})
    c = RelevanceChecker.for_topic("t", "T", SCOPE, cfg=cfg, store_path=tmp_path / "x.json", spent_usd=lambda: 0.0)
    with pytest.raises(RelevanceError, match="needs a model"):
        c.check(gh, item(2))


# ---- in build_universe -------------------------------------------------------------


def test_build_with_fake_classifier_twice_reuses_verdicts(tmp_path):
    topic = Topic(id="t", label="Test topic", queries=("q1",), scope=SCOPE)
    results = {"q1": [item(i) for i in range(1, 31)]}

    def run(model, sub):
        gh = GitHubClient(
            token="t", offline=False, cache_dir=tmp_path / sub,
            transport=httpx.MockTransport(lambda r: FakeSearch(results)(r) if r.url.path.startswith("/search")
                                          else httpx.Response(404, json={})),
        )
        c = checker(tmp_path, model)
        u = build_universe(gh, topic, built_at=AS_OF, max_size=8, fetch=FakeFetch(), progress=lambda m: None, relevance=c)
        return u, c

    m1 = FakeModel()
    u1, c1 = run(m1, "a")
    assert u1.header.counts.rows == 8 and all(r.repo_id % 2 == 0 for r in u1.rows)
    assert u1.header.counts.excluded["off_topic"] == len(m1.calls) - 8
    assert "; relevance fake/luna" in u1.header.builder and "off-topic" in u1.header.builder
    Universe.model_validate_json(u1.model_dump_json())
    m2 = FakeModel(error=ModelError("http", "must not be called"))
    u2, c2 = run(m2, "b")
    assert m2.calls == [] and [r.repo for r in u2.rows] == [r.repo for r in u1.rows]
    assert u2.header.counts == u1.header.counts and c2.summary() == c1.summary()
