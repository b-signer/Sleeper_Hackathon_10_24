"""T03: the fake data is valid, internally consistent, and matches what is committed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sleeper.cli import main
from sleeper.contracts import (
    CRITERIA,
    TOOL_OUTPUT_ADAPTER,
    ErrorOutput,
    RunResult,
    RunSettings,
    SubmitOutput,
    ToolCall,
    ToolResult,
    Universe,
    parse_event,
)
from sleeper.fakes import FAKE_REPOS, RUN_ID, fake_readme, fake_run, fake_universe, write_fakes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fake"
RUN_DIR = FIXTURES / "runs" / RUN_ID


def resolve(doc, pointer: str):
    """RFC 6901 JSON Pointer lookup."""
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        doc = doc[int(part)] if isinstance(doc, list) else doc[part]
    return doc


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines()]
    return text.replace("\r\n", "\n")


# ---- Committed files --------------------------------------------------------


def test_committed_fixtures_match_generator(tmp_path):
    written = write_fakes(tmp_path)
    for path in written:
        committed = FIXTURES / path.relative_to(tmp_path)
        assert committed.exists(), f"{committed} missing: run `uv run sleeper fakes` and commit fixtures/fake/"
        assert load(committed) == load(path), f"{committed} is stale: run `uv run sleeper fakes` and commit fixtures/fake/"


def test_committed_files_validate():
    Universe.model_validate_json((FIXTURES / "universe.json").read_text(encoding="utf-8"))
    RunSettings.model_validate_json((RUN_DIR / "settings.json").read_text(encoding="utf-8"))
    RunResult.model_validate_json((RUN_DIR / "result.json").read_text(encoding="utf-8"))
    for line in (RUN_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines():
        parse_event(line)
    for outs in json.loads((FIXTURES / "tool_outputs.json").read_text(encoding="utf-8")).values():
        for out in outs.values():
            TOOL_OUTPUT_ADAPTER.validate_python(out)


def test_cli_writes_fakes(tmp_path):
    assert main(["fakes", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "runs" / RUN_ID / "events.jsonl").exists()


# ---- Universe ---------------------------------------------------------------


def test_universe_covers_the_profiles():
    u = fake_universe()
    assert len(u.rows) == 10
    profiles = {p for _, p, _ in FAKE_REPOS}
    assert {"rising", "stalled", "single maintainer", "already popular", "unknown metrics"} <= profiles


def test_unknown_metrics_stay_unknown():
    m = fake_universe().get("mystery/unknown-cli").metrics
    assert m.forks_30d is None and m.commits_90d is None and m.readme_chars is None


def test_scores_show_the_underrated_pattern():
    u = fake_universe()
    quiver = u.get("acme/quiver").scores
    assert quiver.quality > quiver.popularity and quiver.momentum > quiver.popularity
    assert u.get("bigco/turbo-lint").scores.popularity == 100
    assert u.get("oldmill/fmt-lite").scores.quality < quiver.quality


def test_readmes_match_readme_chars():
    for row in fake_universe().rows:
        text = fake_readme(row)
        assert (text is None) == (not row.metrics.readme_chars)
        if text is not None:
            assert len(text) == row.metrics.readme_chars


# ---- Recorded run -----------------------------------------------------------


@pytest.fixture(scope="module")
def run():
    return fake_run()


def test_events_are_ordered_and_complete(run):
    assert [e.seq for e in run.events] == list(range(len(run.events)))
    assert run.events[0].type == "run_started" and run.events[-1].type == "run_finished"
    assert run.events[-1].status == run.result.status == "ok"
    calls = [e.call_id for e in run.events if isinstance(e, ToolCall)]
    results = [e.call_id for e in run.events if isinstance(e, ToolResult)]
    assert sorted(calls) == sorted(results) and len(set(calls)) == len(calls)
    assert calls == [f"c_{i:03d}" for i in range(1, len(calls) + 1)]


def test_run_covers_the_paths_the_ui_needs(run):
    outputs = [e.output for e in run.events if isinstance(e, ToolResult)]
    codes = {o.error.code for o in outputs if isinstance(o, ErrorOutput)}
    assert {"NOT_IN_UNIVERSE", "NOT_FOUND"} <= codes
    submits = [o.ok for o in outputs if isinstance(o, SubmitOutput)]
    assert submits == [False, True]
    verdicts = {c.skeptic.verdict for c in run.result.cards}
    assert verdicts == {"survives", "weakened", "killed"}
    assert sum(len(c.fact_flags) for c in run.result.cards) == 1
    assert len(run.result.cards) == 3


def test_every_card_number_traces_to_its_source(run):
    outputs = {e.call_id: e for e in run.events if isinstance(e, ToolResult)}
    for card in run.result.cards:
        for cid in card.cited_call_ids():
            assert cid in outputs and outputs[cid].ok, f"{card.repo} cites missing or failed {cid}"
        for name in CRITERIA:
            rating = getattr(card.criteria, name)
            out = outputs[rating.score_call_id].output.model_dump(mode="json")
            assert rating.score == out["scores"][name]
            fig = rating.key_figure
            src = outputs[fig.source_call_id].output.model_dump(mode="json")
            assert resolve(src, fig.source_path) == fig.value


def test_the_fact_flag_points_at_a_real_mismatch(run):
    outputs = {e.call_id: e.output.model_dump(mode="json") for e in run.events if isinstance(e, ToolResult)}
    (card,) = [c for c in run.result.cards if c.fact_flags]
    (flag,) = card.fact_flags
    assert flag.card_path == "case[1].text" and flag.shown in card.case[1].text
    cited = outputs[card.case[1].source_call_ids[0]]
    assert int(flag.shown) not in cited["metrics"].values()


def test_usage_is_cumulative_and_matches_result(run):
    usage = [e for e in run.events if e.type == "usage"]
    totals = [e.usage.total_tokens for e in usage]
    assert totals == sorted(totals) and usage[-1].usage == run.result.usage
