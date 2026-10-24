"""T10: StubTools (stand-in for T09) and the tool schemas."""

from __future__ import annotations

import json

from sleeper.agent.stub_tools import StubTools
from sleeper.agent.tool_specs import tool_specs
from sleeper.contracts import TOOL_ARGS, BrowseOutput, ErrorOutput, RepoDetailsOutput, Submission, SubmitOutput
from sleeper.fakes import canned_tool_outputs, fake_run, fake_universe


def strip_id(out) -> dict:
    d = out.model_dump(mode="json")
    d.pop("call_id")
    return d


def test_stub_tools_match_canned_outputs():
    u = fake_universe()
    tools = StubTools(u)
    canned = canned_tool_outputs(u)["acme/quiver"]
    assert strip_id(tools.execute("repo_details", {"repo": "acme/quiver"}, "c_001")) == strip_id(canned["repo_details"])
    assert strip_id(tools.execute("readme", {"repo": "acme/quiver"}, "c_002")) == strip_id(canned["readme"])
    out = tools.execute("browse", {"sort_by": "momentum", "limit": 3}, "c_003")
    assert isinstance(out, BrowseOutput) and len(out.rows) == 3 and out.total == len(u.rows)


def test_stub_tools_reject_repos_outside_universe():
    tools = StubTools(fake_universe())
    for name in ("repo_details", "readme"):
        out = tools.execute(name, {"repo": "rust-lang/cargo"}, "c_001")
        assert isinstance(out, ErrorOutput) and out.error.code == "NOT_IN_UNIVERSE"


def test_stub_tools_bad_args():
    tools = StubTools(fake_universe())
    for name, args in [("browse", {"limit": 500}), ("repo_details", {}), ("readme", {"repo": "not a repo"})]:
        out = tools.execute(name, args, "c_001")
        assert isinstance(out, ErrorOutput) and out.error.code == "BAD_ARGS", name


def _research(tools: StubTools) -> None:
    """Replay the recorded fake run's research calls with the same call ids (c_001..c_012)."""
    for e in fake_run().events:
        if e.type == "tool_call" and e.tool != "submit":
            tools.execute(e.tool, e.args, e.call_id)


def _final_submission() -> dict:
    return [e for e in fake_run().events if e.type == "tool_call" and e.tool == "submit"][-1].args


def test_stub_tools_submit_accepts_valid_submission():
    tools = StubTools(fake_universe())
    _research(tools)
    out = tools.execute("submit", _final_submission(), "c_013")
    assert isinstance(out, SubmitOutput) and out.ok, out.errors
    assert out.attempts_left == 2


def test_stub_tools_submit_rejects_missing_and_failed_citations():
    tools = StubTools(fake_universe())
    _research(tools)
    sub = Submission.model_validate(_final_submission())
    rd = tools.outputs["c_008"]  # rust-lang/cargo: NOT_IN_UNIVERSE
    assert isinstance(rd, ErrorOutput)
    sub.cards[0].case[0].source_call_ids = ["c_008"]
    sub.cards[1].case[0].source_call_ids = ["c_404"]
    out = tools.execute("submit", sub.model_dump(mode="json"), "c_013")
    assert not out.ok and out.attempts_left == 2
    text = " ".join(out.errors)
    assert "c_008" in text and "failed" in text and "c_404" in text and "does not exist" in text


def test_stub_tools_submit_rejects_too_many_picks_and_counts_attempts():
    tools = StubTools(fake_universe(), max_picks=2)
    _research(tools)
    out = tools.execute("submit", _final_submission(), "c_013")
    assert not out.ok and any("too many picks" in e for e in out.errors)
    bad = tools.execute("submit", {"cards": []}, "c_014")  # contract failure is a rejected attempt too
    assert not bad.ok and bad.attempts_left == 1
    tools.execute("submit", {}, "c_015")
    last = tools.execute("submit", _final_submission(), "c_016")
    assert not last.ok and last.attempts_left == 0 and tools.submit_attempts == 3


def test_tool_specs():
    specs = tool_specs()
    assert [s["function"]["name"] for s in specs] == list(TOOL_ARGS)
    text = json.dumps(specs)
    assert "$ref" not in text and "$defs" not in text and "prefixItems" not in text
    for s in specs:
        assert s["type"] == "function" and s["function"]["description"]
        assert s["function"]["parameters"]["type"] == "object"
    assert [s["function"]["name"] for s in tool_specs(["submit"])] == ["submit"]
    assert StubTools(fake_universe()).tool_specs() == specs
    # The submit schema still describes cards in full (inlined from $defs).
    card = tool_specs(["submit"])[0]["function"]["parameters"]["properties"]["cards"]["items"]
    assert "criteria" in card["properties"] and card["properties"]["criteria"]["properties"]["momentum"]["type"] == "object"
    assert isinstance(StubTools(fake_universe()).execute("repo_details", {"repo": "ACME/Quiver"}, "c_001"), RepoDetailsOutput)
