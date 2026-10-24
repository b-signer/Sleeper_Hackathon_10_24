"""T02: every contract round-trips through JSON and rejects bad input."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel, ValidationError

from sleeper.cli import main
from sleeper.contracts import (
    TOOL_ARGS,
    TOOL_OUTPUT_ADAPTER,
    BrowseArgs,
    BrowseOutput,
    BrowseRow,
    Card,
    CardCriteria,
    Claim,
    CriteriaScores,
    CriterionRating,
    ErrorOutput,
    FactCheckEvent,
    FactCheckFlag,
    Filter,
    KeyFigure,
    Narration,
    RankingEntry,
    Reasoning,
    ReadmeArgs,
    ReadmeOutput,
    RepoArgs,
    RepoDetailsOutput,
    RepoMetrics,
    ResultCard,
    Risk,
    RunFinished,
    RunResult,
    RunSettings,
    RunStarted,
    SkepticEvent,
    SkepticReview,
    StatusEvent,
    Submission,
    SubmitOutput,
    ToolCall,
    ToolError,
    ToolResult,
    Universe,
    UniverseCounts,
    UniverseHeader,
    UniverseRow,
    Usage,
    UsageEvent,
    dump_event,
    json_schemas,
    parse_event,
)

TS = datetime(2026, 10, 24, 14, 2, 11, tzinfo=UTC)


# ---- Examples ---------------------------------------------------------------


def settings() -> RunSettings:
    return RunSettings(topic="rust-devtools", model="openai/gpt-5.6-luna")


def metrics() -> RepoMetrics:
    return RepoMetrics(
        stars=340,
        stars_per_day=0.67,
        forks=22,
        forks_30d=9,
        star_events_30d=61,
        star_events_capped=False,
        commits_90d=81,
        authors_90d=4,
        releases=6,
        last_release_at=datetime(2026, 10, 1, tzinfo=UTC),
        last_commit_at=datetime(2026, 10, 22, 8, tzinfo=UTC),
        readme_chars=5400,
        created_at=datetime(2025, 6, 2, 10, 11, 12, tzinfo=UTC),
        age_days=509,
        description="Incremental linter",
        language="Rust",
        license="MIT",
    )


def scores() -> CriteriaScores:
    return CriteriaScores(popularity=42.0, momentum=91.5, quality=88.0, newness=60.0)


def row(repo: str = "acme/quiver") -> UniverseRow:
    return UniverseRow(repo=repo, repo_id=1, url=f"https://github.com/{repo}", metrics=metrics(), scores=scores())


def universe() -> Universe:
    header = UniverseHeader(
        topic="rust-devtools",
        topic_label="Rust developer tools",
        queries=["language:rust topic:cli stars:50..2000 created:>=2025-01-01 fork:false archived:false"],
        stars_min=50,
        stars_max=2000,
        created_after=date(2025, 1, 1),
        built_at=TS,
        builder="sleeper 0.1.0 @ abc1234",
        seed=7,
        github_requests=812,
        build_seconds=1430.5,
        scoring="Each score is the percentile of its metric within the universe.",
        counts=UniverseCounts(search_total=335, excluded={"fork": 0, "awesome_list": 2}, eligible=333, sampled=True, rows=2),
    )
    return Universe(header=header, rows=[row("acme/quiver"), row("bob/tool")])


def rating(path: str, value: float) -> CriterionRating:
    return CriterionRating(
        score=50.0,
        score_call_id="c_002",
        key_figure=KeyFigure(label=path, value=value, source_call_id="c_002", source_path=f"/metrics/{path}"),
    )


def card(repo: str = "acme/quiver") -> Card:
    return Card(
        repo=repo,
        pitch="Incremental Rust linter: 81 commits in 90 days, only 340 stars.",
        criteria=CardCriteria(
            popularity=rating("stars", 340),
            momentum=rating("forks_30d", 9),
            quality=rating("commits_90d", 81),
            newness=rating("age_days", 509),
        ),
        case=[Claim(text="81 commits in 90 days from 4 authors", source_call_ids=["c_002"])],
        risks=[Risk(text="Small contributor base", source_call_ids=["c_002"])],
        proven_wrong_if=["commits_90d drops below 10"],
        confidence=0.6,
    )


def submission() -> Submission:
    return Submission(
        cards=[card()],
        ranking=[RankingEntry(rank=1, repo="acme/quiver"), RankingEntry(rank=2, repo="bob/tool", note="stalled")],
        summary="One strong pick.",
    )


def tool_outputs() -> list[BaseModel]:
    return [
        BrowseOutput(
            call_id="c_001",
            total=2,
            offset=0,
            rows=[BrowseRow(repo="acme/quiver", stars=340, scores=scores(), extra={"commits_90d": 81})],
        ),
        RepoDetailsOutput(call_id="c_002", repo="acme/quiver", url="https://github.com/acme/quiver", metrics=metrics(), scores=scores()),
        ReadmeOutput(call_id="c_003", repo="acme/quiver", text="# quiver", truncated=False, total_chars=8),
        SubmitOutput(call_id="c_004", ok=False, errors=["card 1: repo not in universe"], attempts_left=2),
        SubmitOutput(call_id="c_005", ok=True, attempts_left=1),
        ErrorOutput(call_id="c_006", tool="readme", error=ToolError(code="NOT_IN_UNIVERSE", message="x/y is not in the universe")),
    ]


def tool_args() -> list[BaseModel]:
    return [
        BrowseArgs(),
        BrowseArgs(
            filters=[Filter(field="momentum", op=">=", value=80), Filter(field="stars", op="between", value=(50, 500))],
            keyword="lint",
            sort_by="quality",
            limit=50,
        ),
        RepoArgs(repo="acme/quiver"),
        ReadmeArgs(repo="acme/quiver", max_chars=3000),
        submission(),
    ]


def review() -> SkepticReview:
    return SkepticReview(verdict="weakened", argument="Momentum is one spike.", cited_call_ids=["c_002"], model="openai/gpt-5.6-luna")


def flag() -> FactCheckFlag:
    return FactCheckFlag(card_path="case[0].text", shown="91", source_call_id="c_002", source_path="/metrics/commits_90d", found="81", message="91 not in c_002")


def events() -> list[BaseModel]:
    base = {"ts": TS, "run_id": "20261024-140211_rust-devtools"}
    usage = Usage(prompt_tokens=1000, completion_tokens=200, total_tokens=1200, cost_usd=0.0004)
    return [
        RunStarted(seq=0, settings=settings(), universe=universe().header, git_sha="abc1234", **base),
        StatusEvent(seq=1, phase="researching", **base),
        Narration(seq=2, turn=1, text="Screening for high momentum, low stars.", **base),
        Reasoning(seq=3, turn=1, text="summary", **base),
        ToolCall(seq=4, turn=1, call_id="c_001", tool="browse", args={"sort_by": "momentum"}, **base),
        *[
            ToolResult(seq=5 + i, turn=1, call_id=o.call_id, ok=not isinstance(o, ErrorOutput), output=o, approx_tokens=120, **base)
            for i, o in enumerate(tool_outputs())
        ],
        UsageEvent(seq=20, turn=1, max_turns=25, usage=usage, token_budget=300_000, tokens_remaining=298_800, **base),
        SkepticEvent(seq=21, repo="acme/quiver", review=review(), **base),
        FactCheckEvent(seq=22, repo="acme/quiver", flags=[flag()], **base),
        RunFinished(seq=23, status="ok", **base),
    ]


def result() -> RunResult:
    sub = submission()
    rc = ResultCard(**card().model_dump(), skeptic=review(), fact_flags=[flag()])
    return RunResult(
        run_id="20261024-140211_rust-devtools",
        status="ok",
        settings=settings(),
        started_at=TS,
        finished_at=TS,
        turns=9,
        usage=Usage(total_tokens=120_000, cost_usd=0.04),
        cards=[rc],
        ranking=sub.ranking,
        summary=sub.summary,
        submit_attempts=1,
    )


ALL = [settings(), metrics(), scores(), row(), universe(), card(), submission(), result(), *tool_args(), *tool_outputs()]


# ---- Round trips ------------------------------------------------------------


@pytest.mark.parametrize("model", ALL, ids=lambda m: type(m).__name__)
def test_model_round_trip(model: BaseModel):
    assert type(model).model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("output", tool_outputs(), ids=lambda m: type(m).__name__)
def test_tool_output_union_picks_right_type(output: BaseModel):
    parsed = TOOL_OUTPUT_ADAPTER.validate_json(output.model_dump_json())
    assert type(parsed) is type(output) and parsed == output


@pytest.mark.parametrize("event", events(), ids=lambda e: e.type)
def test_event_line_round_trip(event):
    line = dump_event(event)
    assert "\n" not in line
    parsed = parse_event(line)
    assert type(parsed) is type(event) and parsed == event


def test_tool_args_cover_every_tool():
    assert set(TOOL_ARGS) == {"browse", "repo_details", "readme", "submit"}


def test_universe_lookup_is_case_insensitive():
    assert universe().get("ACME/Quiver").repo == "acme/quiver"
    assert universe().get("nobody/else") is None


def test_unknown_metrics_are_none():
    m = RepoMetrics(stars=10, created_at=TS, age_days=0)
    assert m.stars_per_day is None and m.forks_30d is None and m.commits_90d is None


def test_card_cited_call_ids():
    assert card().cited_call_ids() == {"c_002"}


def test_json_schemas_export(tmp_path):
    schemas = json_schemas()
    assert {"run_settings", "universe", "submission", "run_result", "tool_output", "run_event"} <= set(schemas)
    assert main(["schema", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "run_event.schema.json").exists()


# ---- Rejections -------------------------------------------------------------


def edit(model: BaseModel, **changes) -> dict:
    return {**model.model_dump(mode="json"), **changes}


@pytest.mark.parametrize(
    "cls, data",
    [
        (RunSettings, edit(settings(), stars_min=3000)),
        (RunSettings, edit(settings(), max_picks=6)),
        (RunSettings, edit(settings(), typo_field=1)),
        (CriteriaScores, edit(scores(), momentum=101)),
        (RepoMetrics, edit(metrics(), created_at="2025-06-02T10:11:12")),  # naive timestamp
        (UniverseRow, edit(row(), repo="not-a-repo")),
        (Universe, {**edit(universe()), "rows": [edit(row()), edit(row())]}),  # duplicate rows
        (Universe, {**edit(universe()), "rows": [edit(row())]}),  # header count mismatch
        (Card, edit(card(), case=[])),
        (Card, edit(card(), confidence=1.5)),
        (Card, edit(card(), proven_wrong_if=["a", "b", "c"])),
        (Card, edit(card(), risks=[{"text": "r"}] * 4)),
        (Claim, {"text": "uncited claim", "source_call_ids": []}),
        (KeyFigure, {"label": "x", "value": 1, "source_call_id": "call-1", "source_path": "/x"}),
        (KeyFigure, {"label": "x", "value": 1, "source_call_id": "c_001", "source_path": "x"}),
        (Submission, edit(submission(), cards=[edit(card())] * 6)),
        (Submission, edit(submission(), ranking=[{"rank": 1, "repo": "bob/tool"}])),  # card repo not ranked
        (Submission, edit(submission(), ranking=[{"rank": 1, "repo": "acme/quiver"}, {"rank": 3, "repo": "bob/tool"}])),
        (Filter, {"field": "stars", "op": "between", "value": 5}),
        (Filter, {"field": "is_planted", "op": ">", "value": 0}),
        (BrowseArgs, {"limit": 51}),
        (ReadmeArgs, {"repo": "acme/quiver", "max_chars": 100000}),
        (SubmitOutput, {"call_id": "c_001", "ok": False, "errors": [], "attempts_left": 1}),
        (ErrorOutput, {"call_id": "c_001", "tool": "readme", "error": {"code": "OOPS", "message": "x"}}),
        (RunResult, edit(result(), status="finish_invalid")),
        (SkepticReview, {"verdict": "maybe", "argument": "x"}),
    ],
    ids=lambda v: v.__name__ if isinstance(v, type) else "",
)
def test_rejects(cls, data):
    with pytest.raises(ValidationError):
        cls.model_validate(data)


def test_rejects_unknown_event_type():
    with pytest.raises(ValidationError):
        parse_event('{"seq": 0, "ts": "2026-10-24T14:02:11Z", "run_id": "r", "type": "assistant_message", "text": "hi"}')
