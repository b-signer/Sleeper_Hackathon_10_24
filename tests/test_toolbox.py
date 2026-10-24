"""T09 W2: the Toolbox (real ToolExecutor), offline on the fake universe."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest

from sleeper.agent import tool_specs as tool_specs_mod
from sleeper.agent.loop import run_agent
from sleeper.agent.scripted import ScriptedModel, fake_script
from sleeper.agent.toolbox import Toolbox
from sleeper.contracts import (
    BrowseArgs,
    BrowseOutput,
    ErrorOutput,
    ReadmeArgs,
    ReadmeOutput,
    RepoArgs,
    RepoDetailsOutput,
    RunEvent,
    RunSettings,
    SubmitOutput,
    Universe,
    dump_event,
    parse_event,
)
from sleeper.fakes import browse_output, fake_universe, readme_output, repo_details_output

class FakesData:
    """DataToolsLike over the sleeper.fakes helpers (stands in for W1's DataTools)."""

    def __init__(self, universe: Universe) -> None:
        self.u = universe

    def browse(self, args: BrowseArgs, call_id: str) -> BrowseOutput:
        return browse_output(self.u, args, call_id)

    def repo_details(self, args: RepoArgs, call_id: str) -> RepoDetailsOutput | ErrorOutput:
        return repo_details_output(self.u, args.repo, call_id)

    def readme(self, args: ReadmeArgs, call_id: str) -> ReadmeOutput | ErrorOutput:
        return readme_output(self.u, args.repo, call_id, args.max_chars)

class ListSink:
    """EventSink that keeps events in memory (copied from tests/test_agent_loop.py)."""

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

def toolbox(**kw: Any) -> Toolbox:
    u = fake_universe()
    return Toolbox(u, FakesData(u), **kw)

def test_toolbox_is_tool_executor():
    assert toolbox().tool_specs() == tool_specs_mod.tool_specs()

def test_toolbox_bad_args():
    tb = toolbox()
    out = tb.execute("browse", {"limit": 500}, "c_001")
    assert isinstance(out, ErrorOutput) and out.error.code == "BAD_ARGS" and "limit" in out.error.message
    out = tb.execute("repo_details", {}, "c_002")
    assert isinstance(out, ErrorOutput) and out.error.code == "BAD_ARGS" and "repo" in out.error.message
    assert set(tb.outputs) == {"c_001", "c_002"}

def test_toolbox_unknown_tool_raises():
    with pytest.raises(ValueError):
        toolbox().execute("web_search", {}, "c_001")

def test_toolbox_submit_bad_shape_is_submit_output():
    tb = toolbox()
    out = tb.execute("submit", {"cards": []}, "c_001")
    assert isinstance(out, SubmitOutput) and out.ok is False and out.errors
    assert out.attempts_left == 2 and tb.submit_attempts == 1

def test_toolbox_records_outputs():
    tb = toolbox()
    calls = [
        ("browse", {"limit": 3}),
        ("repo_details", {"repo": "acme/quiver"}),
        ("readme", {"repo": "acme/quiver"}),
        ("repo_details", {"repo": "rust-lang/cargo"}),
        ("readme", {"repo": "mystery/unknown-cli"}),
        ("submit", {"cards": []}),
    ]
    outs = {f"c_{i:03d}": tb.execute(name, args, f"c_{i:03d}") for i, (name, args) in enumerate(calls, 1)}
    assert tb.outputs == outs
    assert all(o.call_id == cid for cid, o in tb.outputs.items())

def test_toolbox_researched():
    tb = toolbox()
    tb.execute("browse", {"limit": 10}, "c_001")
    tb.execute("repo_details", {"repo": "rust-lang/cargo"}, "c_002")  # NOT_IN_UNIVERSE
    tb.execute("readme", {"repo": "mystery/unknown-cli"}, "c_003")  # NOT_FOUND
    assert tb.researched == {}
    tb.execute("repo_details", {"repo": "ACME/Quiver"}, "c_010")
    tb.execute("readme", {"repo": "acme/quiver"}, "c_009")  # finished later, smaller id wins
    tb.execute("readme", {"repo": "acme/quiver"}, "c_011")
    tb.execute("readme", {"repo": "lumen-dev/tidepool"}, "c_012")
    assert tb.researched == {"acme/quiver": ("acme/quiver", "c_009"), "lumen-dev/tidepool": ("lumen-dev/tidepool", "c_012")}

def test_toolbox_submit_attempts():
    tb = toolbox()
    lefts = []
    for i in range(1, 4):
        out = tb.execute("submit", {"cards": []}, f"c_{i:03d}")
        assert isinstance(out, SubmitOutput) and not out.ok
        lefts.append(out.attempts_left)
    assert lefts == [2, 1, 0]
    out = tb.execute("submit", {"cards": []}, "c_004")
    assert isinstance(out, SubmitOutput) and out.errors == ["no submit attempts left"] and out.attempts_left == 0
    assert tb.submit_attempts == 3 and "c_004" in tb.outputs

def test_toolbox_parallel():
    tb = toolbox()
    repos = [r.repo for r in tb.universe.rows[:4]]
    jobs = [("repo_details", {"repo": r}) for r in repos] + [("readme", {"repo": r}) for r in repos]
    barrier = threading.Barrier(len(jobs))

    def run(i: int) -> Any:
        barrier.wait()
        name, args = jobs[i]
        return tb.execute(name, args, f"c_{i + 1:03d}")

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        outs = list(pool.map(run, range(len(jobs))))
    assert len(tb.outputs) == 8 and all(tb.outputs[o.call_id] is o for o in outs)
    assert len(tb.researched) == 4

def test_toolbox_runs_fake_script():
    u = fake_universe()
    tb = Toolbox(u, FakesData(u))
    settings = RunSettings(topic="fake", model="scripted/fake", min_ranked=5)
    sink = ListSink()
    result = run_agent(settings, u, ScriptedModel(fake_script(u)), tb, sink)
    assert result.status == "ok", (result.error, result.last_submit_errors)
    assert result.submit_attempts == 2 and tb.submit_attempts == 2
    first = next(o for o in tb.outputs.values() if isinstance(o, SubmitOutput))
    assert any("failed (NOT_IN_UNIVERSE)" in e for e in first.errors)
