"""`sleeper review <run_dir>` (T11): a review sheet for one recorded run.

Reads result.json and events.jsonl as plain JSON (so older run folders still load) and prints what a person
needs to judge the picks: run stats, research depth, each pick with its description, and automatic flags for
the baseline problems that can be checked mechanically. Topic fit stays a human judgment: the sheet prints
each pick's description so a reader can decide in seconds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CRITERIA = ("popularity", "momentum", "quality", "newness")
MIN_CONFIDENCE_SPREAD = 0.1
MIN_BROWSE_COVERAGE = 0.6  # share of the universe seen in browse rows
_WRONG_IF_METRICS = {
    "stars": r"\bstars?\b",
    "releases": r"\breleases?\b",
    "commits": r"\bcommits?\b",
    "authors": r"\b(?:authors?|contributors?|maintainers?)\b",
    "forks": r"\bforks?\b",
}

_BACKTEST = re.compile(r"(?i:\bas-of\b|\bas of the\b|\bresearch date\b)|\bat T\b")
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_TIMEFRAME = re.compile(
    rf"(?i)\b\d{{4}}-\d{{2}}(?:-\d{{2}})?\b"
    rf"|\b\d+\s*(?:days?|weeks?|months?|quarters?|years?)\b"
    rf"|\b(?:next|coming|following)\s+(?:\w+\s+)?(?:days?|weeks?|months?|quarters?|year)\b"
    rf"|\bQ[1-4]\b|\b{_MONTHS}\s+\d{{4}}\b|\b(?:by|before|until|through)\s+(?:end of\s+)?(?:20\d\d|{_MONTHS})"
)
_NUMBER = re.compile(r"(?i)\d|\b(?:no|zero|none|any|single|one|two|three|five|ten)\b")
# Wrong-if direction (heuristic): a failure condition needs a failure marker. Limits: it reads words, not
# meaning, so "fewer bugs than before by 2027-01-01" passes and an unusual failure phrasing ("is archived") is
# flagged; a person still reads the items.
_FAILURE = re.compile(
    r"(?i)\b(?:fewer|below|under|at most|no|not|never|drops?|dropping|falls?|stops?|stalls?|zero|fails?|"
    r"less than|declines?|shrinks?|only|abandon\w*|archived)\b"
)
_SUCCESS = re.compile(
    r"(?i)\b(?:grows?|growing|expands?|gains?|reach(?:es)?|exceeds?|surpass(?:es)?|more than|rises?|"
    r"increases?|continues?|becomes?|adopts?)\b"
)
_README_LENGTH = re.compile(
    r"(?i)readme[^.;]{0,40}\b(?:chars?|characters|length|long|lines|words)\b"
    r"|\b(?:chars?|characters|length|long)\b[^.;]{0,25}readme"
)


@dataclass(frozen=True)
class Flag:
    kind: str
    message: str
    repo: str | None = None

    def __str__(self) -> str:
        return f"[{self.kind}] " + (f"{self.repo}: " if self.repo else "") + self.message


@dataclass
class Run:
    path: Path
    result: dict[str, Any]
    events: list[dict[str, Any]]


def load_run(run_dir: str | Path) -> Run:
    path = Path(run_dir)
    result = json.loads((path / "result.json").read_text(encoding="utf-8"))
    events = []
    events_file = path / "events.jsonl"
    if events_file.is_file():
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return Run(path, result, events)


# ---- research ------------------------------------------------------------------


@dataclass
class Research:
    browses: list[dict[str, Any]]
    details: list[str]  # repos with a successful repo_details, in call order (deduped)
    readmes: list[tuple[str, int | None]]  # (repo, max_chars) with a successful readme
    descriptions: dict[str, str]  # lower-case repo -> description, from repo_details or browse rows
    seen: set[str]  # lower-case repos that appeared in any browse row


def research(events: list[dict[str, Any]]) -> Research:
    calls: dict[str, dict[str, Any]] = {}
    r = Research([], [], [], {}, set())
    for e in events:
        if e.get("type") == "tool_call":
            calls[e["call_id"]] = e
        elif e.get("type") == "tool_result":
            out = e.get("output") or {}
            call = calls.get(e.get("call_id"), {})
            args = call.get("args") or {}
            tool = call.get("tool") or out.get("tool")
            if not e.get("ok"):
                continue
            if tool == "browse":
                r.browses.append(args)
                for row in out.get("rows", []):
                    r.seen.add(row["repo"].lower())
                    if row.get("description"):
                        r.descriptions.setdefault(row["repo"].lower(), row["description"])
            elif tool == "repo_details":
                repo = out.get("repo") or args.get("repo")
                if repo not in r.details:
                    r.details.append(repo)
                desc = (out.get("metrics") or {}).get("description")
                if desc:
                    r.descriptions[repo.lower()] = desc  # the full one beats a truncated browse row
            elif tool == "readme":
                repo = out.get("repo") or args.get("repo")
                if repo not in [x for x, _ in r.readmes]:
                    r.readmes.append((repo, args.get("max_chars")))
    return r


def _browse_label(args: dict[str, Any]) -> str:
    parts = [f"{args.get('sort_by', 'stars')} {args.get('sort_dir', 'desc')}"]
    for f in args.get("filters") or []:
        parts.append(f"{f.get('field')}{f.get('op')}{f.get('value')}")
    if args.get("keyword"):
        parts.append(f"kw={args['keyword']!r}")
    if args.get("offset"):
        parts.append(f"offset={args['offset']}")
    return " ".join(parts)


# ---- flags ---------------------------------------------------------------------


def _card_texts(card: dict[str, Any]) -> list[tuple[str, str]]:
    """(where, text) for the free text a reader sees as the case for the pick."""
    out = [("pitch", card.get("pitch") or "")]
    out += [(f"case[{i}]", c.get("text", "")) for i, c in enumerate(card.get("case") or [])]
    out += [(f"risks[{i}]", c.get("text", "")) for i, c in enumerate(card.get("risks") or [])]
    out += [(f"proven_wrong_if[{i}]", t) for i, t in enumerate(card.get("proven_wrong_if") or [])]
    return out


def wrong_if_problems(text: str) -> list[str]:
    """What an observable 'proven wrong if' item lacks: a number/threshold and a time frame."""
    problems = []
    without_time = _TIMEFRAME.sub(" ", text)
    if not _NUMBER.search(without_time):
        problems.append("no number or threshold")
    if not _TIMEFRAME.search(text):
        problems.append("no time frame")
    return problems


def wrong_if_direction(text: str) -> str | None:
    """Why an item may describe success instead of failure, or None if it reads as a failure condition."""
    if _FAILURE.search(text):
        return None
    m = _SUCCESS.search(text)
    if m:
        return f"reads as success ({m.group(0)!r}, no failure marker)"
    return "no failure marker (fewer than / below / no / drops / stops ...)"


def card_flags(card: dict[str, Any]) -> list[Flag]:
    repo = card.get("repo")
    flags = []
    for where, text in _card_texts(card):
        m = _BACKTEST.search(text)
        if m:
            flags.append(Flag("backtest-wording", f"{where} says {m.group(0)!r}", repo))
    for i, text in enumerate(card.get("proven_wrong_if") or []):
        for problem in wrong_if_problems(text):
            flags.append(Flag("wrong-if-vague", f"proven_wrong_if[{i}] has {problem}: {text!r}", repo))
        direction = wrong_if_direction(text)
        if direction:
            flags.append(Flag("wrong-if-direction", f"proven_wrong_if[{i}] {direction}: {text!r}", repo))
    for name in CRITERIA:
        kf = ((card.get("criteria") or {}).get(name) or {}).get("key_figure") or {}
        path, label = kf.get("source_path") or "", kf.get("label") or ""
        if path.endswith(("readme_chars", "total_chars")) or "readme" in label.lower():
            flags.append(Flag("readme-length-as-quality", f"{name} key figure is README length ({label})", repo))
        elif name == "quality" and path.endswith("commits_90d"):
            flags.append(Flag("commits-as-quality", "quality key figure is a raw commit count (prefer authors or releases)", repo))
    for where, text in _card_texts(card):
        if where.startswith(("risks", "proven_wrong_if")):
            continue  # a short README is a fair risk
        if _README_LENGTH.search(text):
            flags.append(Flag("readme-length-as-quality", f"{where} cites README length: {text!r}", repo))
    return flags


def run_flags(result: dict[str, Any]) -> list[Flag]:
    cards = result.get("cards") or []
    settings = result.get("settings") or {}
    flags = []
    if result.get("status") != "ok":
        flags.append(Flag("status", f"run ended {result.get('status')}: {result.get('error')}"))
    if result.get("status") == "ok" and not 3 <= len(cards) <= 5:
        flags.append(Flag("card-count", f"{len(cards)} cards (target 3-5)"))
    min_ranked = settings.get("min_ranked")
    if cards and min_ranked and len(result.get("ranking") or []) < min_ranked:
        flags.append(Flag("ranking-short", f"{len(result['ranking'])} repos ranked (target {min_ranked})"))
    confidences = [c.get("confidence") for c in cards if isinstance(c.get("confidence"), int | float)]
    if len(confidences) >= 2 and max(confidences) - min(confidences) < MIN_CONFIDENCE_SPREAD:
        flags.append(
            Flag("confidence-flat", f"confidence spread {max(confidences) - min(confidences):.2f} < {MIN_CONFIDENCE_SPREAD}"
                 f" ({min(confidences):.2f}-{max(confidences):.2f})")
        )
    used = [
        frozenset(
            k for k, rx in _WRONG_IF_METRICS.items() if any(re.search(rx, t, re.I) for t in c.get("proven_wrong_if") or [])
        )
        for c in cards
    ]
    if len(cards) >= 3 and len(set(used)) == 1:
        metrics = ", ".join(sorted(used[0])) or "none"
        flags.append(Flag("wrong-if-repetitive", f"every card's wrong-if uses the same metrics ({metrics})"))
    summary = result.get("summary") or ""
    m = _BACKTEST.search(summary)
    if m:
        flags.append(Flag("backtest-wording", f"summary says {m.group(0)!r}"))
    for e in result.get("ranking") or []:
        m = _BACKTEST.search(e.get("note") or "")
        if m:
            flags.append(Flag("backtest-wording", f"ranking note says {m.group(0)!r}", e.get("repo")))
    for card in cards:
        flags += card_flags(card)
        if card.get("fact_flags"):
            flags.append(Flag("fact-check", f"{len(card['fact_flags'])} fact-check flag(s)", card.get("repo")))
    return flags


# ---- sheet ---------------------------------------------------------------------


def _wrap(prefix: str, text: str) -> str:
    return prefix + " ".join(str(text).split())


def render(run: Run) -> str:
    res, settings = run.result, run.result.get("settings") or {}
    usage = res.get("usage") or {}
    rs = research(run.events)
    budget = settings.get("token_budget") or 0
    used = usage.get("total_tokens", 0)
    universe = next((e.get("universe") for e in run.events if e.get("type") == "run_started"), None) or {}
    rows = (universe.get("counts") or {}).get("rows")
    lines = [
        f"Run {res.get('run_id')}  ({run.path})",
        f"status {res.get('status')}" + (f" ({res.get('error')})" if res.get("error") else "")
        + f" · submit attempts {res.get('submit_attempts', '?')}",
        f"model {settings.get('model')} · topic {settings.get('topic')}"
        + (f" ({universe.get('topic_label')}, {rows} repos)" if universe else ""),
        f"turns {res.get('turns')}/{settings.get('max_turns')} · tokens {used:,}/{budget:,}"
        + (f" ({100 * used / budget:.0f}%)" if budget else "")
        + f" · cost ${usage.get('cost_usd', 0):.4f}",
        "",
        f"Research: {len(rs.browses)} browse ({len(rs.seen)} distinct repos seen"
        + (f" of {rows}" if rows else "") + f"), {len(rs.details)} repo_details, {len(rs.readmes)} readme",
    ]
    for b in rs.browses:
        lines.append(f"  browse: {_browse_label(b)}")
    lines.append(_wrap("  repo_details: ", ", ".join(rs.details) or "-"))
    sizes = sorted({m for _, m in rs.readmes if m is not None})
    lines.append(_wrap("  readme: ", ", ".join(r for r, _ in rs.readmes) or "-")
                 + (f"  (max_chars {', '.join(map(str, sizes))})" if sizes else ""))

    cards = res.get("cards") or []
    lines += ["", f"Picks: {len(cards)} · ranked: {len(res.get('ranking') or [])}"]
    for i, card in enumerate(cards, 1):
        repo = card.get("repo", "?")
        lines += ["", f"#{i} {repo}   confidence {card.get('confidence')}"]
        lines.append(_wrap("   description: ", rs.descriptions.get(repo.lower(), "(not seen in this run)")))
        lines.append(_wrap("   pitch: ", card.get("pitch", "")))
        scores = []
        for name in CRITERIA:
            c = (card.get("criteria") or {}).get(name) or {}
            kf = c.get("key_figure") or {}
            value = kf.get("value")
            shown = f"{value:g}" if isinstance(value, int | float) else value
            scores.append(f"{name} {c.get('score')} ({kf.get('label')}: {shown})")
        lines.append("   scores: " + " · ".join(scores))
        for c in card.get("case") or []:
            lines.append(_wrap("   case: ", c.get("text", "")))
        for r in card.get("risks") or []:
            lines.append(_wrap("   risk: ", r.get("text", "")))
        for w in card.get("proven_wrong_if") or []:
            lines.append(_wrap("   wrong if: ", w))
    if res.get("summary"):
        lines += ["", _wrap("Summary: ", res["summary"])]
    if res.get("ranking"):
        lines += ["", "Ranking:"]
        for e in res["ranking"]:
            lines.append(f"  {e.get('rank')}. {e.get('repo')}" + (f" - {e['note']}" if e.get("note") else ""))

    flags = run_flags(res)
    if rows and run.events and len(rs.seen) < MIN_BROWSE_COVERAGE * rows:
        flags.append(Flag("coverage-low", f"browse showed {len(rs.seen)} of {rows} repos (< {MIN_BROWSE_COVERAGE:.0%})"))
    lines += ["", f"Flags: {len(flags)}" + ("" if flags else " (none)")]
    lines += [f"  {f}" for f in flags]
    lines += ["", "Human check: is each pick on topic (see descriptions)? Would a developer agree it is good and underrated?"]
    return "\n".join(lines)


def review(run_dir: str | Path) -> str:
    return render(load_run(run_dir))
