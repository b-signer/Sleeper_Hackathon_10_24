"""T10: the agent loop, offline, with a scripted model and stub tools."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

import pytest

from sleeper.agent.loop import NUDGE, run_agent
from sleeper.agent.scripted import FAKE_NARRATION, ScriptedModel, fake_script, reply
from sleeper.agent.stub_tools import StubTools
from sleeper.agent.tool_specs import tool_specs
from sleeper.agent.types import ModelError
from sleeper.contracts import (
    ErrorOutput,
    RunEvent,
    RunResult,
    RunSettings,
    SubmitOutput,
    ToolOutput,
    dump_event,
    parse_event,
)
from sleeper.fakes import fake_universe


class ListSink:
    """EventSink that keeps events in memory, round-tripping each through dump_event/parse_event."""

    def __init__(self, run_id: str = "20261024-120000_fake") -> None:
        self.run_id = run_id
        self.events: list[RunEvent] = []
        self._lock = threading.Lock()

    def emit(self, event_type: type[Any], /, **payload: Any) -> RunEvent:
        with self._lock:
            event = event_type(seq=len(self.events), ts=datetime.now(UTC), run_id=self.run_id, **payload)
            event = parse_event(dump_event(event))
            self.events.append(event)
            return event

    def of(self, type_: str) -> list[RunEvent]:
        return [e for e in self.events if e.type == type_]


def settings(**kw: Any) -> RunSettings:
    return RunSettings(**{"topic": "fake", "model": "scripted/fake", "min_ranked": 5, **kw})


def run(steps, *, s: RunSettings | None = None, tools=None, **kw) -> tuple[RunResult, ListSink, ScriptedModel]:
    u = fake_universe()
    model = ScriptedModel(steps)
    sink = ListSink()
    result = run_agent(s or settings(), u, model, tools or StubTools(u), sink, **kw)
    assert sink.events[0].type == "run_started" and sink.events[-1].type == "run_finished"
    assert sink.events[-1].status == result.status
    assert [e.seq for e in sink.events] == list(range(len(sink.events)))
    RunResult.model_validate_json(result.model_dump_json())
    return result, sink, model


BROWSE = ("browse", {"limit": 3})


# ---- ok run -----------------------------------------------------------------


def test_status_ok_full_run():
    result, sink, model = run(fake_script(fake_universe()))
    assert result.status == "ok", result.error
    assert 1 <= len(result.cards) <= 5 and result.ranking
    assert result.submit_attempts == 2 and result.turns == 6
    assert result.summary and result.ranking_below_min is False
    calls = sink.of("tool_call")
    results = {e.call_id: e for e in sink.of("tool_result")}
    assert [c.call_id for c in calls] == [f"c_{i:03d}" for i in range(1, len(calls) + 1)]
    assert {c.call_id for c in calls} == set(results)
    errors = {results[c].output.error.code for c in results if isinstance(results[c].output, ErrorOutput)}
    assert errors == {"NOT_IN_UNIVERSE", "NOT_FOUND"}
    submits = [r.output for r in results.values() if isinstance(r.output, SubmitOutput)]
    assert [s.ok for s in submits] == [False, True]
    usage_events = sink.of("usage")
    assert [e.turn for e in usage_events] == list(range(1, 7))
    assert result.usage == usage_events[-1].usage
    assert usage_events[-1].tokens_remaining == 300_000 - result.usage.total_tokens
    assert [e.text for e in sink.of("narration")] == list(FAKE_NARRATION)
    phases = [e.phase for e in sink.of("status")]
    assert phases[:2] == ["starting", "researching"] and phases[-1] == "finished"
    started = sink.events[0]
    assert started.universe.topic == "fake" and len(started.prompt_sha256) == 64
    assert RunResult.model_validate_json(result.model_dump_json()) == result
    # history sent to the model: tool messages answer the provider ids, in order
    last = model.calls[-1]["messages"]
    assert last[0]["role"] == "system"
    assert all("tool_call_id" in m for m in last if m["role"] == "tool")


# ---- tool calls ---------------------------------------------------------------


class BarrierTools:
    """Every execute() waits until 3 run at the same time: serial execution would time out."""

    def __init__(self) -> None:
        self.inner = StubTools(fake_universe())
        self.barrier = threading.Barrier(3, timeout=2)

    def tool_specs(self):
        return tool_specs()

    def execute(self, name: str, args: dict, call_id: str) -> ToolOutput:
        if name != "submit":
            self.barrier.wait()
        return self.inner.execute(name, args, call_id)


def test_parallel_tool_calls():
    steps = [
        reply("three at once", BROWSE, ("repo_details", {"repo": "acme/quiver"}), ("readme", {"repo": "acme/quiver"})),
        reply("done"),
        reply(None),
        reply(None),
    ]
    _, sink, _ = run(steps, tools=BarrierTools())
    first = [e for e in sink.of("tool_result") if e.turn == 1]
    assert len(first) == 3 and all(e.ok for e in first)


def test_submit_runs_after_other_calls():
    script = fake_script(fake_universe())
    research, submits = script[:4], script[4:]
    # last research turn also carries the (rejected) submit, listed before a browse
    combined = lambda msgs: (lambda r: reply(r.content, ("submit", r.tool_calls[0].arguments), BROWSE))(submits[0](msgs))
    _, sink, _ = run([*research, combined, submits[1]])
    turn5 = [e for e in sink.events if e.type == "tool_result" and e.turn == 5]
    assert [type(e.output).__name__ for e in turn5] == ["BrowseOutput", "SubmitOutput"]
    calls5 = [e for e in sink.of("tool_call") if e.turn == 5]
    assert [c.tool for c in calls5] == ["submit", "browse"]


def test_bad_json_args():
    script = fake_script(fake_universe())
    steps = [reply("oops", ("browse", "{not json")), *script]
    result, sink, _ = run(steps)
    bad = sink.of("tool_result")[0]
    assert isinstance(bad.output, ErrorOutput) and bad.output.error.code == "BAD_ARGS" and not bad.ok
    assert sink.of("tool_call")[0].args == {"_raw": "{not json"}
    assert result.status == "ok"


def test_unknown_tool():
    script = fake_script(fake_universe())
    steps = [reply("search the web", ("web_search", {"q": "rust linters"})), *script]
    result, sink, model = run(steps)
    assert any(e.message and "web_search" in e.message for e in sink.of("status"))
    assert "c_001" not in {e.call_id for e in sink.of("tool_result")}
    tool_msg = [m for m in model.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "unknown tool" in tool_msg["content"]
    assert result.status == "ok"


def test_tool_exception_is_upstream_error():
    class Boom(StubTools):
        def execute(self, name, args, call_id):
            if name == "browse":
                raise RuntimeError("cache down")
            return super().execute(name, args, call_id)

    result, sink, _ = run(fake_script(fake_universe()), tools=Boom(fake_universe()))
    first = sink.of("tool_result")[0]
    assert isinstance(first.output, ErrorOutput) and first.output.error.code == "UPSTREAM"
    assert result.status == "ok"


# ---- budget -----------------------------------------------------------------


def test_budget_line_each_turn():
    s = settings(max_turns=10)
    _, _, model = run(fake_script(fake_universe()), s=s)
    for i, call in enumerate(model.calls[1:], start=2):
        last = call["messages"][-1]
        assert last["role"] == "user"
        assert f"turn {i}/10" in last["content"] and "left" in last["content"]


def test_submit_only_phase():
    s = settings(token_budget=100_000)
    steps = [reply("big read", BROWSE, prompt_tokens=91_000), reply("still browsing", BROWSE)]
    result, sink, model = run(steps, s=s)
    assert "submit_only" in [e.phase for e in sink.of("status")]
    second = model.calls[1]
    assert [t["function"]["name"] for t in second["tools"]] == ["submit"]
    assert second["tool_choice"] == {"type": "function", "function": {"name": "submit"}}
    blocked = [e for e in sink.of("tool_result") if e.turn == 2][0]
    assert blocked.output.error.code == "BUDGET_EXHAUSTED"


def test_spend_headroom_forces_submit_only():
    steps = [reply("costly", BROWSE, cost_usd=0.01), reply("again", BROWSE), reply(None), reply(None)]
    _, sink, model = run(steps, spend_headroom_usd=0.001)
    assert "submit_only" in [e.phase for e in sink.of("status")]
    assert [t["function"]["name"] for t in model.calls[1]["tools"]] == ["submit"]


def test_status_budget_exhausted():
    s = settings(token_budget=10_000)
    steps = [reply("a", BROWSE, prompt_tokens=6_000), reply("b", BROWSE, prompt_tokens=6_000), reply("c", BROWSE)]
    result, sink, _ = run(steps, s=s)
    assert result.status == "budget_exhausted" and result.turns == 2
    assert result.usage.total_tokens > 10_000
    assert sink.of("usage")[-1].tokens_remaining < 0


def test_status_turns_exhausted():
    s = settings(max_turns=3)
    steps = [reply(str(i), BROWSE) for i in range(5)]
    result, _, model = run(steps, s=s)
    assert result.status == "budget_exhausted" and result.turns == 3 and len(model.calls) == 3


def test_status_invalid():
    def bad(msgs):
        return reply("submit", ("submit", {"cards": [], "ranking": [], "summary": ""}))

    result, sink, _ = run([reply("look", BROWSE), bad, bad, bad, reply("never reached", BROWSE)])
    assert result.status == "invalid" and result.submit_attempts == 3
    assert result.last_submit_errors and not result.cards


def test_status_invalid_when_budget_runs_out_after_rejected_submit():
    s = settings(max_turns=2)
    bad = reply("submit", ("submit", {"cards": []}))
    result, _, _ = run([reply("look", BROWSE), bad], s=s)
    assert result.status == "invalid" and result.submit_attempts == 1 and result.last_submit_errors


# ---- timeouts and errors ----------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_status_timeout():
    clock = Clock()

    def slow(msgs):
        clock.t += 700  # past max_wall_clock_s=600
        return reply("slow", BROWSE)

    result, sink, model = run([reply("fast", BROWSE), slow, reply("never", BROWSE)], now=clock)
    assert result.status == "timeout" and len(model.calls) == 2
    assert model.calls[0]["timeout_s"] == 120


def test_model_timeout_past_wall_clock_is_timeout():
    clock = Clock()

    def stall(msgs):
        clock.t += 700
        raise ModelError("timeout", "read timed out")

    result, _, _ = run([stall], now=clock)
    assert result.status == "timeout"


def test_status_error_model_error():
    result, sink, _ = run([reply("look", BROWSE), ModelError("http", "502 from provider")])
    assert result.status == "error" and "502" in result.error
    assert sink.events[-1].error == result.error
    assert result.usage == sink.of("usage")[-1].usage


def test_status_error_stalled():
    result, _, _ = run([ModelError("timeout", "read timed out")])
    assert result.status == "error" and "stalled" in result.error

    result, sink, model = run([reply("thinking"), reply("still thinking"), reply("hmm"), reply("never")])
    assert result.status == "error" and "stalled" in result.error and len(model.calls) == 3
    for call in model.calls[1:]:
        assert NUDGE in call["messages"][-1]["content"]


def test_unexpected_model_exception_is_error():
    result, _, _ = run([RuntimeError("bug")])
    assert result.status == "error" and "bug" in result.error


@pytest.mark.parametrize("turns", [1])
def test_single_turn_run_is_submit_only_from_start(turns):
    _, sink, model = run([reply(None), reply(None)], s=settings(max_turns=turns))
    assert [t["function"]["name"] for t in model.calls[0]["tools"]] == ["submit"]
