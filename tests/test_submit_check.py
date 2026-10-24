"""T09 W2: check_submission, built from real tool outputs on the fake universe."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from sleeper.agent.submit_check import COPY_HINT, MAX_ERRORS, SubmitContext, check_submission, resolve_pointer
from sleeper.agent.toolbox import Toolbox
from sleeper.contracts import (
    BrowseArgs,
    ErrorOutput,
    ReadmeArgs,
    ReadmeOutput,
    RepoArgs,
    RepoDetailsOutput,
    SubmitOutput,
    ToolOutput,
    parse_event,
)
from sleeper.fakes import browse_output, fake_universe, readme_output, repo_details_output

RECORDED = Path(__file__).resolve().parents[1] / "fixtures/fake/runs/20261024-140000_fake/events.jsonl"


class FakesData:
    def __init__(self, u):
        self.u = u

    def browse(self, args: BrowseArgs, call_id: str):
        return browse_output(self.u, args, call_id)

    def repo_details(self, args: RepoArgs, call_id: str):
        return repo_details_output(self.u, args.repo, call_id)

    def readme(self, args: ReadmeArgs, call_id: str):
        return readme_output(self.u, args.repo, call_id, args.max_chars)


@pytest.fixture
def tb() -> Toolbox:
    """c_001 browse by momentum (top 10), c_002 repo_details acme/quiver, c_003 repo_details lumen-dev/tidepool,
    c_004 readme acme/quiver, c_005 NOT_IN_UNIVERSE, c_006 repo_details mystery/unknown-cli, c_007 a submit."""
    u = fake_universe()
    t = Toolbox(u, FakesData(u))
    t.execute("browse", {"sort_by": "momentum", "limit": 10}, "c_001")
    t.execute("repo_details", {"repo": "acme/quiver"}, "c_002")
    t.execute("repo_details", {"repo": "lumen-dev/tidepool"}, "c_003")
    t.execute("readme", {"repo": "acme/quiver"}, "c_004")
    t.execute("repo_details", {"repo": "rust-lang/cargo"}, "c_005")
    t.execute("repo_details", {"repo": "mystery/unknown-cli"}, "c_006")
    t.execute("submit", {"cards": []}, "c_007")
    return t


def ctx(t: Toolbox, max_picks: int = 5) -> SubmitContext:
    return SubmitContext(universe=t.universe, max_picks=max_picks, outputs=t.outputs, researched=t.researched)


FIGURES = {"popularity": "stars", "momentum": "forks_30d", "quality": "commits_90d", "newness": "age_days"}


def card(d: RepoDetailsOutput) -> dict[str, Any]:
    criteria = {
        c: {
            "score": getattr(d.scores, c),
            "score_call_id": d.call_id,
            "key_figure": {"label": m, "value": getattr(d.metrics, m), "source_call_id": d.call_id, "source_path": f"/metrics/{m}"},
        }
        for c, m in FIGURES.items()
    }
    return {
        "repo": d.repo,
        "pitch": "A pick",
        "criteria": criteria,
        "case": [{"text": "Has stars", "source_call_ids": [d.call_id]}],
        "proven_wrong_if": ["No commits"],
        "confidence": 0.5,
    }


def valid(t: Toolbox) -> dict[str, Any]:
    return {
        "cards": [card(t.outputs["c_002"]), card(t.outputs["c_003"])],
        "ranking": [
            {"rank": 1, "repo": "acme/quiver"},
            {"rank": 2, "repo": "lumen-dev/tidepool"},
            {"rank": 3, "repo": "mystery/unknown-cli"},
        ],
        "summary": "Two picks.",
    }


def errors_of(args: dict[str, Any], c: SubmitContext) -> list[str]:
    sub, errors = check_submission(args, c)
    assert errors, "expected a rejection"
    return errors


def test_resolve_pointer():
    doc = {"a": [{"b": 1}], "x/y": {"m~n": 2}, "n": None}
    assert resolve_pointer(doc, "/a/0/b") == 1
    assert resolve_pointer(doc, "/x~1y/m~0n") == 2
    assert resolve_pointer(doc, "/n") is None
    assert resolve_pointer(doc, "") is doc
    for bad in ["/a/1/b", "/a/x", "/zz", "/a/0/b/c", "/a/01"]:
        with pytest.raises(KeyError):
            resolve_pointer(doc, bad)


def test_check_accepts_valid(tb):
    sub, errors = check_submission(valid(tb), ctx(tb))
    assert errors == [] and sub is not None and len(sub.cards) == 2


def test_check_shape_error(tb):
    sub, errors = check_submission({"cards": [], "summary": ""}, ctx(tb))
    assert sub is None
    assert any(e.startswith("cards:") for e in errors) and any(e.startswith("ranking:") for e in errors)


def test_check_too_many_picks(tb):
    errors = errors_of(valid(tb), ctx(tb, max_picks=1))
    assert "too many picks: 2 cards, at most 1" in errors


def test_check_repo_not_in_universe(tb):
    args = valid(tb)
    args["ranking"].append({"rank": 4, "repo": "rust-lang/cargo"})
    assert errors_of(args, ctx(tb)) == ["rust-lang/cargo is not in the universe"]


def test_check_missing_citation(tb):
    args = valid(tb)
    args["cards"][0]["case"][0]["source_call_ids"] = ["c_099"]
    assert errors_of(args, ctx(tb)) == ["cards[0] (acme/quiver) cites c_099, which does not exist"]


def test_check_cites_error_output(tb):
    args = valid(tb)
    args["cards"][0]["case"].append({"text": "Not a fork", "source_call_ids": ["c_005"]})
    assert errors_of(args, ctx(tb)) == [
        "cards[0] (acme/quiver) cites c_005, which failed (NOT_IN_UNIVERSE); error outputs can't be cited"
    ]


def test_check_cites_submit_output(tb):
    args = valid(tb)
    args["cards"][1]["risks"] = [{"text": "Rejected before", "source_call_ids": ["c_007"]}]
    assert errors_of(args, ctx(tb)) == ["cards[1] (lumen-dev/tidepool) cites c_007, a submit output; cite research outputs"]


def test_check_score_mismatch(tb):
    args = valid(tb)
    args["cards"][0]["criteria"]["momentum"]["score"] = 80  # source 81.2
    assert errors_of(args, ctx(tb)) == ["cards[0] (acme/quiver) criteria.momentum.score 80 does not match 81.2 in c_002"]


def test_check_score_rounded_ok(tb):
    args = valid(tb)
    args["cards"][0]["criteria"]["momentum"]["score"] = 81  # 81.2 shown as 81
    assert check_submission(args, ctx(tb))[1] == []


def test_check_score_from_browse_row_ok(tb):
    args = valid(tb)
    browse = tb.outputs["c_001"]
    row = next(r for r in browse.rows if r.repo == "acme/quiver")
    for c in FIGURES:
        args["cards"][0]["criteria"][c]["score"] = getattr(row.scores, c)
        args["cards"][0]["criteria"][c]["score_call_id"] = "c_001"
    assert check_submission(args, ctx(tb))[1] == []


def test_check_score_wrong_source(tb):
    args = valid(tb)
    args["cards"][0]["criteria"]["quality"]["score_call_id"] = "c_003"  # tidepool's details
    args["cards"][1]["criteria"]["quality"]["score_call_id"] = "c_004"  # a readme
    errors = errors_of(args, ctx(tb))
    assert any("cards[0] (acme/quiver) criteria.quality.score: c_003 is repo_details for lumen-dev/tidepool" in e for e in errors)
    assert any("cards[1] (lumen-dev/tidepool) criteria.quality.score: c_004 is a readme output" in e for e in errors)


def test_check_key_figure_mismatch(tb):
    args = valid(tb)
    args["cards"][0]["criteria"]["momentum"]["key_figure"]["value"] = 15  # source forks_30d 12
    assert errors_of(args, ctx(tb)) == [
        "cards[0] (acme/quiver) criteria.momentum.key_figure: value 15 does not match 12 at /metrics/forks_30d in c_002; " + COPY_HINT
    ]
    assert COPY_HINT.endswith("copy the number as it appears at source_path, in the source's unit (no k/months conversion)")


def test_check_key_figure_rounding_ok(tb):
    args = valid(tb)
    kf = args["cards"][0]["criteria"]["momentum"]["key_figure"]
    kf.update(value=2.4, source_path="/metrics/stars_per_day")  # 2.43 shown as 2.4
    assert check_submission(args, ctx(tb))[1] == []
    kf.update(value=337, source_path="/metrics/stars")  # 340 within 1%
    assert check_submission(args, ctx(tb))[1] == []
    kf.update(value=330)  # 340 off by ~3%
    assert len(errors_of(args, ctx(tb))) == 1


def test_check_key_figure_bad_pointer(tb):
    args = valid(tb)
    args["cards"][0]["criteria"]["quality"]["key_figure"]["source_path"] = "/metrics/commit_count"
    args["cards"][1]["criteria"]["quality"]["key_figure"]["source_path"] = "/metrics/created_at"
    errors = errors_of(args, ctx(tb))
    assert errors == [
        "cards[0] (acme/quiver) criteria.quality.key_figure: no number at /metrics/commit_count in c_002 (no such path)",
        "cards[1] (lumen-dev/tidepool) criteria.quality.key_figure: no number at /metrics/created_at in c_003 (a str)",
    ]


def test_check_key_figure_null(tb):
    d = tb.outputs["c_006"]  # mystery/unknown-cli: unknown metrics are null
    c = card(d)
    c["criteria"]["momentum"]["key_figure"]["source_path"] = "/metrics/forks_30d"
    c["criteria"]["momentum"]["key_figure"]["value"] = 0
    c["criteria"]["quality"]["key_figure"]["value"] = 0
    args = valid(tb)
    args["cards"] = [c]
    errors = errors_of(args, ctx(tb))
    assert "cards[0] (mystery/unknown-cli) criteria.momentum.key_figure: no number at /metrics/forks_30d in c_006 (null)" in errors


def test_check_key_figure_other_repo(tb):
    args = valid(tb)
    kf = args["cards"][0]["criteria"]["popularity"]["key_figure"]
    kf.update(source_call_id="c_003", value=180)  # tidepool's stars on quiver's card
    browse = tb.outputs["c_001"]
    i_other = next(i for i, r in enumerate(browse.rows) if r.repo != "lumen-dev/tidepool")
    kf2 = args["cards"][1]["criteria"]["popularity"]["key_figure"]
    kf2.update(source_call_id="c_001", source_path=f"/rows/{i_other}/stars", value=browse.rows[i_other].stars)
    kf3 = args["cards"][1]["criteria"]["newness"]["key_figure"]
    kf3.update(source_call_id="c_001", source_path="/total", value=browse.total)
    errors = errors_of(args, ctx(tb))
    assert errors == [
        "cards[0] (acme/quiver) criteria.popularity.key_figure: c_003 is repo_details for lumen-dev/tidepool, not acme/quiver; "
        "cite this repo's own number",
        f"cards[1] (lumen-dev/tidepool) criteria.popularity.key_figure: /rows/{i_other}/stars in c_001 is "
        f"{browse.rows[i_other].repo}'s row, not lumen-dev/tidepool's",
        "cards[1] (lumen-dev/tidepool) criteria.newness.key_figure: /total in c_001 (browse) is not inside a row; "
        "point under /rows/<i>/ at lumen-dev/tidepool's row",
    ]


def test_check_key_figure_from_browse_row_and_readme_ok(tb):
    args = valid(tb)
    browse = tb.outputs["c_001"]
    i = next(i for i, r in enumerate(browse.rows) if r.repo == "acme/quiver")
    args["cards"][0]["criteria"]["popularity"]["key_figure"].update(source_call_id="c_001", source_path=f"/rows/{i}/stars", value=340)
    readme = tb.outputs["c_004"]
    args["cards"][0]["criteria"]["quality"]["key_figure"].update(
        source_call_id="c_004", source_path="/total_chars", value=readme.total_chars
    )
    assert check_submission(args, ctx(tb))[1] == []


def test_check_ranking_missing_researched(tb):
    args = valid(tb)
    args["ranking"] = args["ranking"][:2]
    assert errors_of(args, ctx(tb)) == ["ranking: missing mystery/unknown-cli, which you looked at closely (c_006)"]


def test_check_messages_are_specific(tb):
    args = valid(tb)
    q = args["cards"][0]["criteria"]
    q["popularity"]["score"] = 10
    q["momentum"]["key_figure"]["value"] = 99
    q["newness"]["score_call_id"] = "c_005"
    errors = errors_of(args, ctx(tb))
    assert "cards[0] (acme/quiver) criteria.popularity.score 10 does not match 55.6 in c_002" in errors
    assert any(e.startswith("cards[0] (acme/quiver) criteria.momentum.key_figure: value 99 does not match 12 ") and "c_002" in e for e in errors)
    assert "cards[0] (acme/quiver) cites c_005, which failed (NOT_IN_UNIVERSE); error outputs can't be cited" in errors
    for e in errors:
        assert "cards[0] (acme/quiver)" in e and "c_00" in e


def test_check_errors_capped(tb):
    args = valid(tb)
    for c in args["cards"]:
        for crit in FIGURES:
            c["criteria"][crit]["score"] = 100 if c["criteria"][crit]["score"] < 50 else 0
            c["criteria"][crit]["key_figure"]["value"] = 99999
        c["case"] = [{"text": f"x{i}", "source_call_ids": [f"c_{900 + i}"]} for i in range(4)]
    errors = errors_of(args, ctx(tb))
    assert len(errors) == MAX_ERRORS and errors[-1].startswith("... and ")


# ---- the recorded fake run (T03) ----------------------------------------------


def test_check_recorded_fake_run():
    u = fake_universe()
    outputs: dict[str, ToolOutput] = {}
    researched: dict[str, tuple[str, str]] = {}
    submits: list[dict[str, Any]] = []
    verdicts: list[tuple[Any, list[str]]] = []
    for line in RECORDED.read_text().splitlines():
        e = parse_event(line)
        if e.type == "tool_call" and e.tool == "submit":
            c = SubmitContext(universe=u, max_picks=5, outputs=dict(outputs), researched=dict(researched))
            verdicts.append(check_submission(copy.deepcopy(e.args), c))
            submits.append(e.args)
        elif e.type == "tool_result":
            out = e.output
            outputs[e.call_id] = out
            if isinstance(out, RepoDetailsOutput | ReadmeOutput):
                key = out.repo.lower()
                if key not in researched or int(e.call_id[2:]) < int(researched[key][1][2:]):
                    researched[key] = (out.repo, e.call_id)
    assert len(verdicts) == 2
    (sub1, errors1), (sub2, errors2) = verdicts
    assert "ranking: missing fernwork/cargo-sprout, which you looked at closely (c_005)" in errors1
    assert errors2 == [] and sub2 is not None
    assert not any(isinstance(o, ErrorOutput) and o.call_id == "c_013" for o in outputs.values())
    assert isinstance(outputs["c_013"], SubmitOutput)
