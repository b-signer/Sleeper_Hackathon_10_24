"""T12: the skeptic review, offline, with the fake run and scripted reviewers."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from sleeper.agent.budget import add_usage
from sleeper.agent.scripted import FakeSkeptic, fake_script, reply
from sleeper.agent.skeptic import FORCE_VERDICT, MAX_OUTPUT_CHARS, parse_review, review_card, review_messages
from sleeper.agent.types import ModelError, ModelReply
from sleeper.contracts import Card, ReadmeOutput, Usage
from sleeper.fakes import fake_universe
from test_agent_loop import run  # tests/ is on sys.path (rootdir conftest-less import)


class RecordingSkeptic:
    """Wraps a reviewer and records every call's messages (thread-safe)."""

    def __init__(self, inner: Any = None, fail: Exception | None = None) -> None:
        self.inner = inner or FakeSkeptic()
        self.model_id = "scripted/recording-skeptic"
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def complete(self, messages, tools, *, tool_choice="auto", timeout_s=120.0) -> ModelReply:
        with self._lock:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice, "timeout_s": timeout_s})
        if self.fail is not None:
            raise self.fail
        return self.inner.complete(messages, tools, tool_choice=tool_choice, timeout_s=timeout_s)


def full_run(skeptic: Any, **kw: Any):
    return run(fake_script(fake_universe()), skeptic=skeptic, **kw)


# ---- in the loop --------------------------------------------------------------


def test_each_card_reviewed_and_added_to_result():
    skeptic = RecordingSkeptic()
    result, sink, _ = full_run(skeptic)
    assert result.status == "ok"
    assert len(skeptic.calls) == len(result.cards) == 3
    assert {c.repo: c.skeptic.verdict for c in result.cards} == {
        "acme/quiver": "weakened",
        "lumen-dev/tidepool": "survives",
        "solo-hacker/bytewise": "killed",
    }
    events = sink.of("skeptic_review")
    assert {e.repo for e in events} == {c.repo for c in result.cards}
    for card in result.cards:
        assert card.skeptic.argument and card.skeptic.model == skeptic.model_id
        assert set(card.skeptic.cited_call_ids) <= card.cited_call_ids()
    phases = [e.phase for e in sink.of("status")]
    assert phases.index("reviewing") < phases.index("fact_checking") < phases.index("finished")
    assert all(c["tool_choice"] == FORCE_VERDICT for c in skeptic.calls)
    assert [t["function"]["name"] for t in skeptic.calls[0]["tools"]] == ["verdict"]


def test_review_usage_counts_toward_the_run():
    plain, _, _ = run(fake_script(fake_universe()))
    reviewed, sink, _ = full_run(FakeSkeptic())
    per_review = reply(None, prompt_tokens=2500, completion_tokens=120).usage
    expected = plain.usage
    for _ in reviewed.cards:
        expected = add_usage(expected, per_review)
    assert reviewed.usage == expected
    assert sink.of("usage")[-1].usage == reviewed.usage  # the budget meter shows it
    assert reviewed.turns == plain.turns  # reviews are not agent turns


def test_reviewer_sees_only_the_card_and_its_cited_outputs():
    skeptic = RecordingSkeptic()
    _, sink, _ = full_run(skeptic)
    all_ids = {e.call_id for e in sink.of("tool_result")}
    for call in skeptic.calls:
        system, user = call["messages"]
        assert system["role"] == "system" and "Use only these outputs" in system["content"]
        body = user["content"]
        card = Card.model_validate_json(body.split("## Card\n", 1)[1].split("\n\n## Cited tool outputs", 1)[0])
        shown = {line[4:] for line in body.splitlines() if line.startswith("### c_")}
        assert shown == card.cited_call_ids()
        outputs_part = body.split("## Cited tool outputs", 1)[1]
        assert not {i for i in all_ids - shown if f'"call_id":"{i}"' in outputs_part}


def test_failed_review_leaves_card_without_one():
    result, sink, _ = full_run(RecordingSkeptic(fail=ModelError("timeout", "slow")))
    assert result.status == "ok" and result.cards
    assert all(c.skeptic is None for c in result.cards)
    assert not sink.of("skeptic_review")
    messages = [e.message for e in sink.of("status") if e.phase == "reviewing" and e.message]
    assert len(messages) == len(result.cards) and all("skeptic call failed" in m for m in messages)


def test_no_skeptic_no_review():
    result, sink, _ = run(fake_script(fake_universe()))
    assert all(c.skeptic is None for c in result.cards)
    assert "reviewing" not in [e.phase for e in sink.of("status")]


def test_spend_limit_skips_review():
    plain, _, _ = run(fake_script(fake_universe()))
    skeptic = RecordingSkeptic()
    result, sink, _ = full_run(skeptic, spend_headroom_usd=plain.usage.cost_usd)  # reached by the last agent turn
    assert result.status == "ok" and not skeptic.calls
    assert any("spend limit" in (e.message or "") for e in sink.of("status"))


# ---- parsing --------------------------------------------------------------------


@pytest.fixture
def card() -> Card:
    result, _, _ = run(fake_script(fake_universe()))
    return Card.model_validate(result.cards[0].model_dump(exclude={"skeptic", "fact_flags"}))


def test_parse_review_drops_foreign_citations_and_trims(card: Card):
    cited = sorted(card.cited_call_ids())
    args = {"verdict": "killed", "argument": "x" * 2000, "cited_call_ids": [cited[0], "c_999", cited[0], 7]}
    review = parse_review(reply(None, ("verdict", args)), card, "m")
    assert review.verdict == "killed" and review.cited_call_ids == [cited[0]]
    assert len(review.argument) <= 1200 and review.model == "m"


def test_parse_review_falls_back_to_json_content(card: Card):
    content = json.dumps({"verdict": "survives", "argument": "Fine.", "cited_call_ids": []})
    assert parse_review(reply(content), card, "m").verdict == "survives"


@pytest.mark.parametrize("bad", ["not json", json.dumps({"verdict": "maybe", "argument": "?"}), json.dumps([1])])
def test_unusable_reply_counts_usage_but_gives_no_review(card: Card, bad: str):
    class Bad:
        model_id = "bad"

        def complete(self, *a, **k):
            return reply(bad, prompt_tokens=100, completion_tokens=10)

    out = review_card(Bad(), card, {}, fake_universe().header)
    assert out.review is None and "unusable" in out.error and out.usage.total_tokens == 110


def test_long_outputs_are_truncated(card: Card):
    cid = sorted(card.cited_call_ids())[0]
    big = ReadmeOutput(call_id=cid, repo=card.repo, text="y" * (MAX_OUTPUT_CHARS * 2), truncated=False, total_chars=MAX_OUTPUT_CHARS * 2)
    user = review_messages(card, {cid: big}, fake_universe().header)[1]["content"]
    assert "...[truncated]" in user and len(user) < MAX_OUTPUT_CHARS * 2


def test_usage_zero_when_call_fails(card: Card):
    out = review_card(RecordingSkeptic(fail=RuntimeError("boom")), card, {}, fake_universe().header)
    assert out.review is None and out.usage == Usage()
