"""T11: the review sheet (`sleeper review`) and the system prompt."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from sleeper.agent.prompt import TOPIC_SCOPE, system_prompt
from sleeper.agent.tool_specs import DESCRIPTIONS
from sleeper.cli import main
from sleeper.contracts import RunSettings, Universe
from sleeper.review import card_flags, load_run, research, review, run_flags, wrong_if_direction, wrong_if_problems

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/findings/2026-10-24-real-data-baseline"


def _kinds(flags):
    return [f.kind for f in flags]


def _good_card(repo: str = "a/b", confidence: float = 0.7) -> dict:
    kf = lambda path, label: {"label": label, "value": 3, "unit": None, "source_call_id": "c_004", "source_path": path}  # noqa: E731
    return {
        "repo": repo,
        "pitch": "A Rust linter for GDScript that developers can run in CI.",
        "criteria": {
            "popularity": {"score": 20, "score_call_id": "c_004", "key_figure": kf("/metrics/stars", "Stars")},
            "momentum": {"score": 70, "score_call_id": "c_004", "key_figure": kf("/metrics/forks_30d", "Forks, 30d")},
            "quality": {"score": 60, "score_call_id": "c_004", "key_figure": kf("/metrics/authors_90d", "Authors, 90d")},
            "newness": {"score": 50, "score_call_id": "c_004", "key_figure": kf("/metrics/age_days", "Age")},
        },
        "case": [{"text": "Formats and lints GDScript with 40 rules; the last commit was on 2026-10-23.", "source_call_ids": ["c_010"]}],
        "risks": [{"text": "Single maintainer; the README is short.", "source_call_ids": ["c_010"]}],
        "proven_wrong_if": ["Fewer than 300 stars by 2027-04-24 (120 now).", "No new release by 2027-01-24."],
        "confidence": confidence,
    }


# ---- baseline run: every mechanical problem from the findings is flagged ----


def test_baseline_flags_the_known_problems():
    run = load_run(BASELINE)
    kinds = _kinds(run_flags(run.result))
    assert "confidence-flat" in kinds  # problem 5: 0.82-0.90
    assert "backtest-wording" in kinds  # problem 6: "a commit on the as-of date"
    assert "readme-length-as-quality" in kinds  # problem 2: "53,762 README characters"
    assert kinds.count("wrong-if-vague") >= 10  # problem 4: no number, no time frame
    assert "commits-as-quality" in kinds
    baken = [f for f in run_flags(run.result) if f.kind == "wrong-if-direction" and f.repo == "M-Igashi/baken"]
    assert len(baken) == 2  # "Usage expands beyond a niche DJ audience..." is success, not failure


def test_baseline_research_counts():
    rs = research(load_run(BASELINE).events)
    assert len(rs.browses) == 3 and len(rs.details) == 10 and len(rs.readmes) == 8
    assert {m for _, m in rs.readmes} == {5000}
    assert len(rs.seen) == 25  # three browses of the whole 25-repo universe
    assert "rekordbox" in rs.descriptions["m-igashi/baken"]


def test_review_sheet_shows_picks_descriptions_and_stats():
    sheet = review(BASELINE)
    for text in ("status ok", "turns 3/12", "36,776/120,000", "$0.0108", "#2 M-Igashi/baken", "description: Bake'n Deck",
                 "wrong if:", "confidence 0.88", "[confidence-flat]", "Ranking:"):
        assert text in sheet, text


def test_cli_review(capsys):
    assert main(["review", str(BASELINE)]) == 0
    assert "M-Igashi/baken" in capsys.readouterr().out
    assert main(["review", "no-such-run-folder"]) == 2


# ---- flags on hand-made cards ----


def test_good_cards_have_no_flags():
    result = {"status": "ok", "settings": {"min_ranked": 2}, "summary": "Three picks.",
              "cards": [_good_card("a/b", 0.8), _good_card("c/d", 0.65), _good_card("e/f", 0.5)],
              "ranking": [{"rank": i, "repo": r} for i, r in enumerate(["a/b", "c/d", "e/f"], 1)]}
    assert run_flags(result) == []


@pytest.mark.parametrize(
    "text, problems",
    [
        ("Fewer than 300 stars by 2027-04-24.", []),
        ("No new release in the next 3 months.", []),
        ("Commits in the 90 days before 2027-01-31 drop below 20.", []),
        ("Still a single commit author by end of January 2027.", []),
        ("Adoption fails to grow.", ["no number or threshold", "no time frame"]),
        ("Stars stay below 500.", ["no time frame"]),
        ("No new release by 2027-01-24.", []),
    ],
)
def test_wrong_if_problems(text, problems):
    assert wrong_if_problems(text) == problems


def test_card_flags_catch_wording_and_readme_length():
    card = _good_card()
    card["case"].append({"text": "Its README runs to 12,000 characters.", "source_call_ids": ["c_010"]})
    card["pitch"] = "Strong as of the snapshot."
    card["criteria"]["quality"]["key_figure"]["source_path"] = "/metrics/readme_chars"
    kinds = _kinds(card_flags(card))
    assert kinds.count("readme-length-as-quality") == 2
    assert "backtest-wording" in kinds


def test_run_flags_status_cards_and_ranking():
    result = {"status": "ok", "settings": {"min_ranked": 10}, "cards": [_good_card()], "ranking": [{"rank": 1, "repo": "a/b"}]}
    kinds = _kinds(run_flags(result))
    assert "card-count" in kinds and "ranking-short" in kinds
    failed = {"status": "budget_exhausted", "error": "x", "cards": [], "ranking": []}
    assert _kinds(run_flags(failed)) == ["status"]


def test_review_handles_run_without_events(tmp_path):
    result = json.loads((BASELINE / "result.json").read_text())
    (tmp_path / "result.json").write_text(json.dumps(result))
    sheet = review(tmp_path)
    assert "(not seen in this run)" in sheet and "Research: 0 browse (0 distinct" in sheet


# ---- prompt and tool descriptions ----


@pytest.fixture
def universe() -> Universe:
    return Universe.model_validate_json((BASELINE / "universe-rust-devtools-25.json").read_text())


def _prompt(universe: Universe, **kw) -> str:
    settings = RunSettings(topic="rust-devtools", model="openai/gpt-5.6-luna", **kw)
    return system_prompt(settings, universe.header, today=date(2026, 10, 24))


def test_prompt_is_live_only_and_dated(universe):
    p = _prompt(universe)
    assert "metrics as of" not in p and "as of {" not in p
    assert "Today is 2026-10-24" in p and "live run" in p
    assert "2027-01-23" in p and "2027-04-24" in p  # example time frames, ~3 and ~6 months out


def test_prompt_covers_the_t11_rules(universe):
    p = _prompt(universe)
    for text in (
        TOPIC_SCOPE["rust-devtools"],
        "Never pick an off-topic repo",
        "Your memory is not a source",
        "max_chars 2500",
        "Never present README length",
        "If this happens, was the pick wrong?",
        "is below <threshold> by <date> (now <current>)",
        "Never above 0.85",
        "Do not submit early",
        universe.header.scoring,
    ):
        assert text in p, text


def test_prompt_research_targets_scale_with_universe(universe):
    small = _prompt(universe, min_ranked=8)  # 25 repos
    assert "Shortlist about 12 on-topic" in small and "best 8 or so" in small
    big_header = universe.header.model_copy(update={"counts": universe.header.counts.model_copy(update={"rows": 200})})
    settings = RunSettings(topic="rust-devtools", model="m")
    big = system_prompt(settings, big_header, today=date(2026, 10, 24))
    assert "Shortlist about 18 on-topic" in big and "best 10 or so" in big


def test_topic_scopes_are_principles_and_examples_are_synthetic(universe):
    assert all(text.startswith("Principle:") and "not exhaustive" in text for text in TOPIC_SCOPE.values())
    p = _prompt(universe)
    assert "(now 600)" in p and "175" not in p  # no real repo's numbers (holon had 175 stars)


def test_prompt_unknown_topic_falls_back(universe):
    header = universe.header.model_copy(update={"topic": "zz", "topic_label": "Zig build tools"})
    p = system_prompt(RunSettings(topic="zz", model="m"), header, today=date(2026, 10, 24))
    assert '"Zig build tools"' in p and "Principle:" in p


def test_tool_descriptions_steer_budget():
    assert "2500" in DESCRIPTIONS["readme"]
    assert "limit <= 25" in DESCRIPTIONS["browse"] and "description" in DESCRIPTIONS["browse"]


def test_copy_does_not_mutate():
    card = _good_card()
    before = copy.deepcopy(card)
    card_flags(card)
    assert card == before


@pytest.mark.parametrize(
    "text, ok",
    [
        ("Usage expands beyond a niche DJ audience into a durable general music-library tool.", False),
        ("Stars grow past 2,000 by 2027-04-24.", False),
        ("It gains more than 500 stars by 2027-04-24 (now 120).", False),
        ("Real-world CDJ compatibility and release cadence continue without serious regressions.", False),
        ("Stars below 1,000 by 2027-04-24 (now 600).", True),
        ("No new release by 2027-01-23.", True),
        ("Commits in the 90 days before 2027-01-23 drop under 20 (now 140).", True),
        ("Gains fewer than 100 stars by 2027-04-24.", True),
        ("At most 1 commit author by 2027-04-24.", True),
    ],
)
def test_wrong_if_direction(text, ok):
    assert (wrong_if_direction(text) is None) == ok
