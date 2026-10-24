"""Fake data (T03): a small universe, canned tool outputs and one recorded run. No network.

Everything is generated from FAKE_REPOS, so the files stay valid when a contract changes:
re-run `uv run sleeper fakes` and commit `fixtures/fake/`. The repos are fictional.

The tools here are minimal stand-ins so the fake run has realistic outputs. The real tools are T09.
"""

from __future__ import annotations

import json
import math
import operator
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sleeper import __version__
from sleeper.contracts import (
    CRITERIA,
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
    KeyFigure,
    Narration,
    RankingEntry,
    ReadmeOutput,
    RepoDetailsOutput,
    RepoMetrics,
    ResultCard,
    Risk,
    RunEvent,
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
    ToolOutput,
    ToolResult,
    Universe,
    UniverseCounts,
    UniverseHeader,
    UniverseRow,
    Usage,
    UsageEvent,
    dump_event,
)

DEFAULT_OUT = Path("fixtures/fake")
TOPIC = "fake"
MODEL = "openai/gpt-5.6-luna"
RUN_ID = "20261024-140000_fake"
BUILT_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
RUN_START = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
PRICE_PER_M = (0.20, 1.20)  # D4: luna input / output, USD per million tokens


# ---- Universe ---------------------------------------------------------------


def _ago(days: int | None) -> datetime | None:
    return None if days is None else BUILT_AT - timedelta(days=days)


def _metrics(
    *,
    stars: int,
    forks: int,
    forks_30d: int,
    star_events: int,
    commits: int,
    authors: int,
    releases: int,
    readme: int,
    age: int,
    last_commit: int,
    last_release: int | None,
    description: str,
    license: str | None = "MIT",
) -> RepoMetrics:
    return RepoMetrics(
        stars=stars,
        stars_per_day=round(stars / max(1, age), 2),
        forks=forks,
        forks_30d=forks_30d,
        star_events_30d=star_events,
        star_events_capped=star_events >= 300,  # the events feed holds at most 300 events (D13)
        commits_90d=commits,
        authors_90d=authors,
        releases=releases,
        last_release_at=_ago(last_release),
        last_commit_at=_ago(last_commit),
        readme_chars=readme,
        created_at=BUILT_AT - timedelta(days=age),
        age_days=age,
        description=description,
        language="Rust",
        license=license,
    )


# (repo, profile, metrics). Profiles cover the cases other tickets need to handle.
FAKE_REPOS: list[tuple[str, str, RepoMetrics]] = [
    ("acme/quiver", "rising", _metrics(
        stars=340, forks=38, forks_30d=12, star_events=95, commits=81, authors=4, releases=6, readme=5400, age=140, last_commit=2,
        last_release=19, description="Incremental linter for Rust workspaces that only re-checks what changed")),
    ("lumen-dev/tidepool", "rising", _metrics(
        stars=180, forks=22, forks_30d=9, star_events=62, commits=120, authors=6, releases=3, readme=3900, age=95, last_commit=1,
        last_release=12, description="Connection-pool debugger: a live view of every pooled connection in a Rust service")),
    ("fernwork/cargo-sprout", "rising, very new", _metrics(
        stars=75, forks=9, forks_30d=6, star_events=41, commits=64, authors=2, releases=1, readme=2100, age=45, last_commit=3,
        last_release=20, description="Scaffold Rust crates from templates with sensible CI defaults", license="Apache-2.0")),
    ("solo-hacker/bytewise", "single maintainer", _metrics(
        stars=420, forks=30, forks_30d=5, star_events=48, commits=150, authors=1, releases=9, readme=4600, age=210, last_commit=1,
        last_release=6, description="Hex and binary diff viewer for the terminal")),
    ("oldmill/fmt-lite", "stalled", _metrics(
        stars=910, forks=80, forks_30d=0, star_events=2, commits=0, authors=0, releases=4, readme=2800, age=600, last_commit=210,
        last_release=300, description="Minimal, fast Rust formatter for CI pipelines")),
    ("bigco/turbo-lint", "already popular", _metrics(
        stars=1950, forks=240, forks_30d=25, star_events=210, commits=310, authors=25, releases=22, readme=9800, age=500, last_commit=0,
        last_release=4, description="Batteries-included linter suite for large Rust codebases")),
    ("ghostly/phantom-build", "suspicious star burst", _metrics(
        stars=1200, forks=4, forks_30d=1, star_events=300, commits=3, authors=1, releases=0, readme=420, age=60, last_commit=40,
        last_release=None, description="The fastest Rust build tool ever made")),
    ("docsfirst/manpage-rs", "steady, documentation-heavy", _metrics(
        stars=260, forks=15, forks_30d=2, star_events=19, commits=30, authors=3, releases=2, readme=11200, age=330, last_commit=6,
        last_release=45, description="Generate man pages from clap definitions")),
    ("weekend/todo-term", "low activity", _metrics(
        stars=55, forks=3, forks_30d=0, star_events=0, commits=2, authors=1, releases=0, readme=600, age=640, last_commit=80,
        last_release=None, description="A todo list in your terminal", license=None)),
    ("mystery/unknown-cli", "unknown metrics", RepoMetrics(
        stars=60, created_at=BUILT_AT - timedelta(days=120), age_days=120)),
]

SCORING = (
    "Each score is the percentile rank (0-100) of one raw value among the repos in this universe, "
    "ties sharing the average rank; an unknown raw value scores 0. Popularity: stars. "
    "Momentum (D13): the mean of the stars_per_day and forks_30d percentiles, over whichever are known. "
    "Quality: quality_signal() in "
    "sleeper.fakes, built from commits, authors, releases and README length. Newness: younger is higher."
)


def quality_signal(m: RepoMetrics) -> float | None:
    """One raw number for project health, higher is better.

    Scores are percentiles of this value, so only the order between repos matters, not the scale.
    Return None when the metrics are too unknown to judge; the repo then scores 0 on quality.
    """
    if m.commits_90d is None and m.authors_90d is None and m.releases is None:
        return None
    # Logs so a 300-commit project doesn't drown out a steady 80-commit one; authors count as much as commits.
    activity = math.log1p(m.commits_90d or 0) + math.log1p(m.authors_90d or 0)
    releases = 0.5 * math.log1p(m.releases or 0)
    readme = 0.2 * min(m.readme_chars or 0, 5000) / 5000  # weak signal, capped
    return activity + releases + readme


def _percentiles(values: list[float | None]) -> list[float]:
    known = sorted(v for v in values if v is not None)
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
        elif len(known) == 1:
            out.append(100.0)
        else:
            below = sum(k < v for k in known)
            equal = sum(k == v for k in known)
            out.append(round(100 * (below + (equal - 1) / 2) / (len(known) - 1), 1))
    return out


def _mean_percentiles(*columns: list[float | None]) -> list[float]:
    """Per repo, the mean of its percentiles over the columns where its value is known (0 if none)."""
    ranked = [_percentiles(c) for c in columns]
    out = []
    for i in range(len(columns[0])):
        known = [r[i] for c, r in zip(columns, ranked, strict=True) if c[i] is not None]
        out.append(round(sum(known) / len(known), 1) if known else 0.0)
    return out


def score_rows(metrics: list[RepoMetrics]) -> list[CriteriaScores]:
    columns = {
        "popularity": _percentiles([m.stars for m in metrics]),
        "momentum": _mean_percentiles([m.stars_per_day for m in metrics], [m.forks_30d for m in metrics]),
        "quality": _percentiles([quality_signal(m) for m in metrics]),
        "newness": _percentiles([-m.age_days for m in metrics]),
    }
    return [CriteriaScores(**{c: columns[c][i] for c in CRITERIA}) for i in range(len(metrics))]


def fake_universe() -> Universe:
    scores = score_rows([m for _, _, m in FAKE_REPOS])
    rows = [
        UniverseRow(repo=repo, repo_id=900_000 + i, url=f"https://github.com/{repo}", metrics=m, scores=s)
        for i, ((repo, _, m), s) in enumerate(zip(FAKE_REPOS, scores, strict=True), start=1)
    ]
    header = UniverseHeader(
        topic=TOPIC,
        topic_label="Fake Rust dev tools (test data)",
        queries=["fake: generated by sleeper.fakes, no GitHub search"],
        stars_min=50,
        stars_max=2000,
        created_after=date(2025, 1, 1),
        built_at=BUILT_AT,
        builder=f"sleeper {__version__} fakes",
        seed=7,
        github_requests=0,
        build_seconds=0.0,
        scoring=SCORING,
        counts=UniverseCounts(
            search_total=len(rows) + 2,
            excluded={"fork": 1, "awesome_list": 1},
            eligible=len(rows),
            sampled=False,
            rows=len(rows),
        ),
    )
    return Universe(header=header, rows=rows)


def fake_readme(row: UniverseRow) -> str | None:
    """README text exactly `readme_chars` long, or None if the repo has no README."""
    m = row.metrics
    if not m.readme_chars:
        return None
    name = row.repo.split("/")[1]
    head = f"# {name}\n\n{m.description}\n\n## Install\n\n    cargo install {name}\n\n## Usage\n\n"
    filler = "Placeholder README text for a fictional repo used in tests. "
    return (head + filler * (m.readme_chars // len(filler) + 1))[: m.readme_chars]


# ---- Minimal tools (stand-ins for T09) --------------------------------------

_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}
_ROW_FIELDS = {"stars", *CRITERIA}  # already on every browse row, so never repeated in `extra`


def _field(row: UniverseRow, name: str) -> float | None:
    return getattr(row.scores, name) if name in CRITERIA else getattr(row.metrics, name)


def _error(tool: str, call_id: str, code: str, message: str) -> ErrorOutput:
    return ErrorOutput(call_id=call_id, tool=tool, error=ToolError(code=code, message=message))


def browse_output(u: Universe, args: BrowseArgs, call_id: str) -> BrowseOutput:
    rows = list(u.rows)
    for f in args.filters:
        if f.op == "between":
            lo, hi = f.value
            rows = [r for r in rows if (v := _field(r, f.field)) is not None and lo <= v <= hi]
        else:
            rows = [r for r in rows if (v := _field(r, f.field)) is not None and _OPS[f.op](v, f.value)]
    if args.keyword:
        kw = args.keyword.lower()
        rows = [r for r in rows if kw in r.repo.lower() or kw in (r.metrics.description or "").lower()]
    if args.language:
        rows = [r for r in rows if (r.metrics.language or "").lower() == args.language.lower()]
    if args.sort_by == "random":
        random.Random(u.header.seed).shuffle(rows)
    else:
        known = [r for r in rows if _field(r, args.sort_by) is not None]
        known.sort(key=lambda r: _field(r, args.sort_by), reverse=args.sort_dir == "desc")
        rows = known + [r for r in rows if _field(r, args.sort_by) is None]  # unknowns last
    extra = [n for n in dict.fromkeys([*(f.field for f in args.filters), args.sort_by]) if n not in _ROW_FIELDS | {"random"}]
    return BrowseOutput(
        call_id=call_id,
        total=len(rows),
        offset=args.offset,
        rows=[
            BrowseRow(
                repo=r.repo,
                description=(r.metrics.description or "")[:120] or None,
                language=r.metrics.language,
                stars=r.metrics.stars,
                scores=r.scores,
                extra={n: _field(r, n) for n in extra},
            )
            for r in rows[args.offset : args.offset + args.limit]
        ],
    )


def repo_details_output(u: Universe, repo: str, call_id: str) -> RepoDetailsOutput | ErrorOutput:
    row = u.get(repo)
    if row is None:
        return _error("repo_details", call_id, "NOT_IN_UNIVERSE", f"{repo} is not in the universe")
    return RepoDetailsOutput(call_id=call_id, repo=row.repo, url=row.url, metrics=row.metrics, scores=row.scores)


def readme_output(u: Universe, repo: str, call_id: str, max_chars: int = 6000) -> ReadmeOutput | ErrorOutput:
    row = u.get(repo)
    if row is None:
        return _error("readme", call_id, "NOT_IN_UNIVERSE", f"{repo} is not in the universe")
    text = fake_readme(row)
    if text is None:
        return _error("readme", call_id, "NOT_FOUND", f"{row.repo} has no README")
    return ReadmeOutput(call_id=call_id, repo=row.repo, text=text[:max_chars], truncated=len(text) > max_chars, total_chars=len(text))


def canned_tool_outputs(u: Universe) -> dict[str, dict[str, ToolOutput]]:
    """repo_details and readme for every fake repo, with the placeholder call_id c_000."""
    return {r.repo: {"repo_details": repo_details_output(u, r.repo, "c_000"), "readme": readme_output(u, r.repo, "c_000")} for r in u.rows}


# ---- Recorded run -----------------------------------------------------------


@dataclass
class FakeRun:
    settings: RunSettings
    events: list[RunEvent]
    result: RunResult


@dataclass
class _RunBuilder:
    u: Universe
    settings: RunSettings
    events: list[RunEvent] = field(default_factory=list)
    outputs: dict[str, ToolOutput] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    turns: int = 0
    calls: int = 0
    submit_attempts: int = 0
    submit_errors: list[list[str]] = field(default_factory=list)

    def event(self, cls: type, **payload: Any) -> None:
        seq = len(self.events)
        self.events.append(cls(seq=seq, ts=RUN_START + timedelta(seconds=2 * seq), run_id=RUN_ID, **payload))

    def spend(self, prompt: int, completion: int) -> None:
        u = self.usage
        p, c = u.prompt_tokens + prompt, u.completion_tokens + completion
        cost = round((p * PRICE_PER_M[0] + c * PRICE_PER_M[1]) / 1e6, 6)
        self.usage = Usage(prompt_tokens=p, completion_tokens=c, total_tokens=p + c, cost_usd=cost)
        self.event(
            UsageEvent,
            turn=self.turns,
            max_turns=self.settings.max_turns,
            usage=self.usage,
            token_budget=self.settings.token_budget,
            tokens_remaining=self.settings.token_budget - self.usage.total_tokens,
        )

    def turn(self, text: str, calls: list[tuple[str, dict[str, Any]]], tokens: tuple[int, int]) -> list[ToolOutput]:
        """One agent turn: narration, then all tool calls, then their results (as a concurrent loop logs them)."""
        self.turns += 1
        self.event(Narration, turn=self.turns, text=text)
        ids = []
        for tool, args in calls:
            self.calls += 1
            ids.append(f"c_{self.calls:03d}")
            self.event(ToolCall, turn=self.turns, call_id=ids[-1], tool=tool, args=args)
        outs = []
        for call_id, (tool, args) in zip(ids, calls, strict=True):
            out = self._run_tool(tool, args, call_id)
            self.outputs[call_id] = out
            self.event(
                ToolResult,
                turn=self.turns,
                call_id=call_id,
                ok=not isinstance(out, ErrorOutput),
                output=out,
                approx_tokens=len(out.model_dump_json()) // 4,
                duration_ms=5 if tool == "submit" else 35,
                cache_hit=True if tool in ("repo_details", "readme") else None,
            )
            outs.append(out)
        self.spend(*tokens)
        return outs

    def _run_tool(self, tool: str, args: dict[str, Any], call_id: str) -> ToolOutput:
        if tool == "browse":
            return browse_output(self.u, BrowseArgs.model_validate(args), call_id)
        if tool == "repo_details":
            return repo_details_output(self.u, args["repo"], call_id)
        if tool == "readme":
            return readme_output(self.u, args["repo"], call_id, args.get("max_chars", 6000))
        self.submit_attempts += 1
        errors = self.submit_errors.pop(0)
        return SubmitOutput(call_id=call_id, ok=not errors, errors=errors, attempts_left=3 - self.submit_attempts)


def _rating(d: RepoDetailsOutput, criterion: str, metric: str, label: str, unit: str) -> CriterionRating:
    return CriterionRating(
        score=getattr(d.scores, criterion),
        score_call_id=d.call_id,
        key_figure=KeyFigure(
            label=label, value=getattr(d.metrics, metric), unit=unit, source_call_id=d.call_id, source_path=f"/metrics/{metric}"
        ),
    )


def _criteria(d: RepoDetailsOutput) -> CardCriteria:
    return CardCriteria(
        popularity=_rating(d, "popularity", "stars", "Stars", "stars"),
        momentum=_rating(d, "momentum", "forks_30d", "Forks, last 30 days", "forks"),
        quality=_rating(d, "quality", "commits_90d", "Commits, last 90 days", "commits"),
        newness=_rating(d, "newness", "age_days", "Age", "days"),
    )


def fake_run(universe: Universe | None = None) -> FakeRun:
    """A complete `ok` run: 6 turns, 2 tool errors, 1 rejected submit, 3 cards (one killed, one fact-check flag)."""
    u = universe or fake_universe()
    settings = RunSettings(topic=TOPIC, universe_file=str(DEFAULT_OUT / "universe.json"), model=MODEL, min_ranked=5)
    b = _RunBuilder(u, settings)
    b.event(RunStarted, settings=settings, universe=u.header)
    b.event(StatusEvent, phase="starting")
    b.event(StatusEvent, phase="researching")

    b.turn(
        "Start broad: browse the universe by momentum to see which repos are gaining attention fastest.",
        [("browse", {"sort_by": "momentum", "limit": 10})],
        (3100, 160),
    )
    b.turn(
        "Momentum alone rewards hype. Screen for active, decent-quality repos that are not popular yet.",
        [("browse", {
            "filters": [
                {"field": "commits_90d", "op": ">", "value": 20},
                {"field": "popularity", "op": "<=", "value": 70},
            ],
            "sort_by": "momentum",
            "limit": 10,
        })],
        (5200, 190),
    )
    picks = ["acme/quiver", "lumen-dev/tidepool", "fernwork/cargo-sprout", "solo-hacker/bytewise", "ghostly/phantom-build"]
    outs = b.turn(
        "Look closely at the strongest candidates and the star burst, and check whether rust-lang/cargo is in scope.",
        [("repo_details", {"repo": r}) for r in [*picks, "rust-lang/cargo"]],
        (9800, 240),
    )
    d = {o.repo: o for o in outs if isinstance(o, RepoDetailsOutput)}
    outs = b.turn(
        "Read the READMEs to judge whether these projects deliver what they claim.",
        [("readme", {"repo": r}) for r in ["acme/quiver", "lumen-dev/tidepool", "ghostly/phantom-build", "mystery/unknown-cli"]],
        (16900, 310),
    )
    rd = {o.repo: o for o in outs if isinstance(o, ReadmeOutput)}

    q, t, p = d["acme/quiver"], d["lumen-dev/tidepool"], d["ghostly/phantom-build"]
    qm, tm, pm = q.metrics, t.metrics, p.metrics
    wrong_forks = tm.forks_30d + 5  # deliberately wrong, so the UI has a fact-check flag to show (T13, T16)
    cards = [
        Card(
            repo=q.repo,
            pitch=f"Incremental Rust linter: {qm.commits_90d} commits in 90 days and {qm.forks_30d} new forks this month, at only {qm.stars} stars.",
            criteria=_criteria(q),
            case=[
                Claim(text=f"{qm.commits_90d} commits in the last 90 days from {qm.authors_90d} authors", source_call_ids=[q.call_id]),
                Claim(text=f"Ships regularly: {qm.releases} releases so far", source_call_ids=[q.call_id]),
                Claim(text="The README documents installation and usage", source_call_ids=[rd[q.repo].call_id]),
            ],
            risks=[Risk(text=f"Only {qm.authors_90d} active authors in the last 90 days", source_call_ids=[q.call_id])],
            proven_wrong_if=["No new release in the next 60 days", "Commits in the last 90 days fall below 20"],
            confidence=0.7,
        ),
        Card(
            repo=t.repo,
            pitch=f"Live connection-pool debugger with {tm.authors_90d} active authors and {tm.stars} stars.",
            criteria=_criteria(t),
            case=[
                Claim(text=f"{tm.commits_90d} commits in the last 90 days from {tm.authors_90d} authors", source_call_ids=[t.call_id]),
                Claim(text=f"{wrong_forks} forks created in the last 30 days", source_call_ids=[t.call_id]),
            ],
            risks=[Risk(text=f"Young: {tm.age_days} days old", source_call_ids=[t.call_id])],
            proven_wrong_if=["Fewer than 3 new forks in a month"],
            confidence=0.6,
        ),
        Card(
            repo=p.repo,
            pitch=f"Build tool gaining {pm.stars_per_day} stars a day: a momentum bet.",
            criteria=_criteria(p),
            case=[
                Claim(text=f"{pm.stars_per_day} stars per day since it was created", source_call_ids=[p.call_id]),
                Claim(text=f"At least {pm.star_events_30d} star events in the last 30 days", source_call_ids=[p.call_id]),
            ],
            risks=[
                Risk(text=f"Only {pm.commits_90d} commits in the last 90 days", source_call_ids=[p.call_id]),
                Risk(text="The README is short and makes big claims", source_call_ids=[rd[p.repo].call_id]),
            ],
            proven_wrong_if=["Fewer than 10 commits in the next 90 days"],
            confidence=0.4,
        ),
    ]
    ranking = [
        RankingEntry(rank=1, repo=q.repo),
        RankingEntry(rank=2, repo=t.repo),
        RankingEntry(rank=3, repo=p.repo, note="Momentum bet; activity is thin"),
        RankingEntry(rank=4, repo="fernwork/cargo-sprout", note="Promising but very new"),
        RankingEntry(rank=5, repo="solo-hacker/bytewise", note=f"Single maintainer ({d['solo-hacker/bytewise'].metrics.authors_90d} author in 90 days)"),
    ]
    summary = (
        "Two young Rust tools pair steady commits with fast star growth at low star counts. "
        "The third pick is a momentum bet that the skeptic should test."
    )
    sprout = d["fernwork/cargo-sprout"]
    # The first try leaves out a repo it looked at closely, so the trace shows a rejected submit.
    short = [e.model_copy(update={"rank": i}) for i, e in enumerate((e for e in ranking if e.repo != sprout.repo), 1)]
    first = Submission(cards=cards, ranking=short, summary=summary)
    final = Submission(cards=cards, ranking=ranking, summary=summary)
    b.submit_errors = [[f"ranking: missing {sprout.repo}, which you looked at closely ({sprout.call_id})"], []]
    b.turn("Submitting three picks and the ranking.", [("submit", first.model_dump(mode="json"))], (24100, 1450))
    b.turn(
        f"The ranking missed {sprout.repo}; adding it and resubmitting.",
        [("submit", final.model_dump(mode="json"))],
        (25600, 1500),
    )

    b.event(StatusEvent, phase="reviewing")
    reviews = {
        q.repo: SkepticReview(
            verdict="survives",
            argument=f"Activity is broad ({qm.authors_90d} authors) and releases are regular; the star count is low for the work shipped.",
            cited_call_ids=[q.call_id],
            model=MODEL,
        ),
        t.repo: SkepticReview(
            verdict="weakened",
            argument=f"Only {tm.age_days} days old: recent momentum may be launch interest that fades.",
            cited_call_ids=[t.call_id],
            model=MODEL,
        ),
        p.repo: SkepticReview(
            verdict="killed",
            argument=f"At least {pm.star_events_30d} star events in 30 days, but only {pm.commits_90d} commits in 90 days "
            f"and {pm.forks_30d} new fork. "
            "That looks like a bought or viral star burst, not a project gaining users.",
            cited_call_ids=[p.call_id, rd[p.repo].call_id],
            model=MODEL,
        ),
    }
    for repo, review in reviews.items():
        b.event(SkepticEvent, repo=repo, review=review)
        b.spend(2400, 220)

    b.event(StatusEvent, phase="fact_checking")
    flags = {
        q.repo: [],
        t.repo: [FactCheckFlag(
            card_path="case[1].text",
            shown=str(wrong_forks),
            source_call_id=t.call_id,
            source_path="/metrics/forks_30d",
            found=str(tm.forks_30d),
            message=f"{wrong_forks} does not match forks_30d = {tm.forks_30d} in {t.call_id}",
        )],
        p.repo: [],
    }
    for repo, repo_flags in flags.items():
        b.event(FactCheckEvent, repo=repo, flags=repo_flags)

    b.event(StatusEvent, phase="finished")
    b.event(RunFinished, status="ok")
    result = RunResult(
        run_id=RUN_ID,
        status="ok",
        settings=settings,
        started_at=RUN_START,
        finished_at=b.events[-1].ts,
        turns=b.turns,
        usage=b.usage,
        cards=[ResultCard(**c.model_dump(), skeptic=reviews[c.repo], fact_flags=flags[c.repo]) for c in cards],
        ranking=final.ranking,
        summary=final.summary,
        ranking_below_min=len(final.ranking) < settings.min_ranked,
        submit_attempts=b.submit_attempts,
    )
    return FakeRun(settings=settings, events=b.events, result=result)


# ---- Files ------------------------------------------------------------------


def _write(path: Path, text: str, final_newline: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n" if final_newline else text, encoding="utf-8", newline="\n")
    return path


def write_fakes(out: Path = DEFAULT_OUT) -> list[Path]:
    """Write the universe, READMEs, canned tool outputs and the recorded run under `out`."""
    u = fake_universe()
    run = fake_run(u)
    canned = {repo: {k: o.model_dump(mode="json") for k, o in outs.items()} for repo, outs in canned_tool_outputs(u).items()}
    run_dir = out / "runs" / RUN_ID
    paths = [
        _write(out / "universe.json", u.model_dump_json(indent=2)),
        _write(out / "tool_outputs.json", json.dumps(canned, indent=2)),
        # Exactly readme_chars long, so no trailing newline is added.
        *[_write(out / "readmes" / f"{r.repo.replace('/', '__')}.md", text, final_newline=False) for r in u.rows if (text := fake_readme(r))],
        _write(run_dir / "settings.json", run.settings.model_dump_json(indent=2)),
        _write(run_dir / "events.jsonl", "\n".join(dump_event(e) for e in run.events)),
        _write(run_dir / "result.json", run.result.model_dump_json(indent=2)),
    ]
    return paths
