"""T13: card numbers are checked against the outputs they cite, allowing display rounding."""

from __future__ import annotations

import pytest

from sleeper.contracts import Claim, ErrorOutput, ToolError, ToolResult
from sleeper.factcheck import check_card, numbers_in, text_number_matches
from sleeper.fakes import fake_run


@pytest.fixture(scope="module")
def run():
    return fake_run()


@pytest.fixture(scope="module")
def outputs(run):
    return {e.call_id: e.output for e in run.events if isinstance(e, ToolResult)}


def card(run, repo):
    return next(c for c in run.result.cards if c.repo == repo)


# ---- The ticket's test: the fake run ---------------------------------------------------------


def test_flags_the_tampered_number_and_passes_the_rest(run, outputs):
    flags = {c.repo: check_card(c, outputs) for c in run.result.cards}
    assert flags["acme/quiver"] == [] and flags["ghostly/phantom-build"] == []
    (flag,) = flags["lumen-dev/tidepool"]
    assert flag.card_path == "case[1].text" and flag.shown == "14"  # the source says 9
    assert "c_004" in flag.message


def test_recorded_run_carries_the_same_flags(run, outputs):
    for c in run.result.cards:
        assert c.fact_flags == check_card(c, outputs)


# ---- Each kind of mismatch ---------------------------------------------------------------------


def test_tampered_key_figure(run, outputs):
    c = card(run, "acme/quiver")
    bad = c.model_copy(deep=True)
    bad.criteria.popularity.key_figure.value = 400  # source: 340 stars
    (flag,) = check_card(bad, outputs)
    assert flag.card_path == "criteria.popularity.key_figure.value"
    assert (flag.shown, flag.found, flag.source_path) == ("400", "340", "/metrics/stars")


def test_tampered_score(run, outputs):
    bad = card(run, "acme/quiver").model_copy(deep=True)
    bad.criteria.quality.score = 90  # source: 75
    (flag,) = check_card(bad, outputs)
    assert flag.card_path == "criteria.quality.score" and flag.found == "75"


def test_rounded_score_passes(run, outputs):
    ok = card(run, "acme/quiver").model_copy(deep=True)
    ok.criteria.momentum.score = round(ok.criteria.momentum.score)  # 81.2 -> 81
    assert check_card(ok, outputs) == []


def test_derived_number_in_text_is_flagged(run, outputs):
    bad = card(run, "acme/quiver").model_copy(update={"pitch": "Linter with 28% of its stars gained this month."})
    (flag,) = check_card(bad, outputs)
    assert flag.card_path == "pitch" and flag.shown == "28%"


def test_numbers_must_come_from_the_lines_own_citation(run, outputs):
    c = card(run, "acme/quiver")
    readme_id = c.case[2].source_call_ids[0]  # the README output has no metrics in it
    bad = c.model_copy(update={"case": [*c.case[:2], Claim(text="81 commits in 90 days", source_call_ids=[readme_id])]})
    (flag,) = check_card(bad, outputs)
    assert flag.card_path == "case[2].text" and flag.shown == "81"


def test_error_outputs_back_nothing(run, outputs):
    c = card(run, "acme/quiver")
    failed = {**outputs, "c_099": ErrorOutput(call_id="c_099", tool="repo_details", error=ToolError(code="UPSTREAM", message="x"))}
    bad = c.model_copy(update={"case": [Claim(text="81 commits from 4 authors", source_call_ids=["c_099"])]})
    assert [f.shown for f in check_card(bad, failed)] == ["81", "4"]


# ---- Reading numbers in text ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, shown",
    [
        ("81 commits in the last 90 days from 4 authors", ["81", "4"]),
        ("81 commits in 90 days", ["81"]),
        ("Young: 95 days old", ["95"]),
        ("1,234 stars and 1.2k forks", ["1,234", "1.2k"]),
        ("85% of commits", ["85%"]),
        ("20.0 stars per day since it was created", ["20.0"]),
        ("created 2025-06-02 in 2026, v0.4.0, cites c_003, uses gpt-5.6 and x86", []),
        ("a 30-day window, 12/30d", []),
    ],
)
def test_numbers_in(text, shown):
    assert [n.shown for n in numbers_in(text)] == shown


@pytest.mark.parametrize(
    "shown, source, ok",
    [
        ("1.2k", 1234, True),
        ("1.2k", 1300, False),
        ("85%", 0.85, True),
        ("85%", 85, True),
        ("85%", 0.9, False),
        ("2.4", 2.43, True),
        ("2.5", 2.43, False),
        ("1,230", 1234, True),  # within 1%
        ("1,200", 1234, False),
        ("14", 9, False),
    ],
)
def test_display_rounding(shown, source, ok):
    (n,) = numbers_in(shown)
    assert text_number_matches(n, source) is ok


# ---- Wired into the agent loop (T10) ----------------------------------------------------------------


def test_agent_run_fact_checks_before_finishing(tmp_path, monkeypatch):
    from sleeper.agent.runner import execute_run
    from sleeper.config import load_settings
    from sleeper.contracts import RunSettings
    from sleeper.server.store import read_events

    monkeypatch.setenv("SLEEPER_RUNS_DIR", str(tmp_path))
    result = execute_run(RunSettings(topic="fake", model="scripted/fake"), fake=True, cfg=load_settings())
    events = read_events(tmp_path / result.run_id / "events.jsonl")
    types = [e.type for e in events]
    checks = [e for e in events if e.type == "fact_check"]
    assert len(checks) == len(result.cards) >= 1
    assert types.index("fact_check") < types.index("run_finished")
    phases = [e.phase for e in events if e.type == "status"]
    assert phases.index("fact_checking") < phases.index("finished")
    assert {c.repo: c.fact_flags for c in result.cards} == {e.repo: e.flags for e in checks}
