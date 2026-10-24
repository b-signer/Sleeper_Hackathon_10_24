"""T08 checks for a built universe: header sanity, a row sample for a person, and demo-cache readiness.

Run it as a module, so it needs no change to cli.py::

    uv run python -m sleeper.universe_check check data/universes/local-first.json   # header + 20-row sheet
    uv run python -m sleeper.universe_check cache data/universes/*.json            # README cache hits, offline
    uv run python -m sleeper.universe_check warm data/universes/*.json             # fetch missing READMEs

- ``check`` prints problems with the header counts (exit 1 if any) and a numbered sample of rows with
  a box to tick for each, for the T08 sanity check and T18's 17-of-20 topic check.
- ``cache`` asks the cache for every row's README, and for the arXiv papers those READMEs link (S2),
  with no network (as on a demo machine with SLEEPER_OFFLINE=1). A live demo run reads them through
  the readme and papers tools; a miss there is an UPSTREAM error in the run. Universes themselves are
  committed files and need no cache. Citation counts are best effort and not counted.
- ``warm`` fetches what the cache is missing (one GitHub core request per README; one arXiv request
  per repo's papers), so copying ``.cache/`` to the demo machine is enough for offline live runs. It
  stops when the core rate limit falls under a floor, because the team shares one token.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sleeper.contracts import Universe

SAMPLE_SIZE = 20
SAMPLE_SEED = 8  # not the build seed (7), so the sample isn't the first rows the build drew
RATE_FLOOR = 1_000  # leave this many core requests for the rest of the team


def load(path: str | Path) -> Universe:
    return Universe.model_validate_json(Path(path).read_text(encoding="utf-8"))


def header_issues(u: Universe) -> list[str]:
    """Problems a person would otherwise have to spot by eye. Empty = the header adds up."""
    h, c = u.header, u.header.counts
    out: list[str] = []
    excluded = sum(c.excluded.values())
    if c.eligible + excluded != c.search_total:
        out.append(f"eligible {c.eligible} + excluded {excluded} != search_total {c.search_total}")
    if c.rows > c.eligible:
        out.append(f"rows {c.rows} > eligible {c.eligible}")
    if c.rows > h.max_size:
        out.append(f"rows {c.rows} > max_size {h.max_size}")
    if c.sampled != (c.eligible > h.max_size):
        out.append(f"sampled={c.sampled} but eligible {c.eligible} vs max_size {h.max_size}")
    if not c.sampled and c.rows != c.eligible:
        out.append(f"not sampled but rows {c.rows} != eligible {c.eligible}")
    ids = [r.repo_id for r in u.rows]
    if len(ids) != len(set(ids)):
        out.append("duplicate repo_id")
    # The star window applies at search time, so a repo can drift past it before its metrics are read.
    # Small drift is normal; far outside the window means the search qualifiers were wrong.
    far = [r.repo for r in u.rows if not (h.stars_min * 0.8 <= r.metrics.stars <= h.stars_max * 1.2)]
    if far:
        out.append(f"{len(far)} rows far outside stars {h.stars_min}-{h.stars_max}: {', '.join(far[:5])}")
    early = [r.repo for r in u.rows if r.metrics.created_at.date() < h.created_after]
    if early:
        out.append(f"{len(early)} rows created before {h.created_after}: {', '.join(early[:5])}")
    if h.created_before:
        late = [r.repo for r in u.rows if r.metrics.created_at.date() > h.created_before]
        if late:
            out.append(f"{len(late)} rows created after {h.created_before}: {', '.join(late[:5])}")
    future = [r.repo for r in u.rows if r.metrics.created_at > h.built_at]
    if future:
        out.append(f"{len(future)} rows created after built_at: {', '.join(future[:5])}")
    if not h.scoring.strip():
        out.append("header.scoring is empty")
    return out


def sample(u: Universe, n: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED) -> list:
    rows = list(u.rows)
    return random.Random(seed).sample(rows, min(n, len(rows)))


def check_sheet(u: Universe, path: str, n: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED) -> str:
    """Markdown: header summary, automatic issues, and a sample to tick by hand."""
    h, c = u.header, u.header.counts
    excluded = ", ".join(f"{k} {v}" for k, v in sorted(c.excluded.items())) or "none"
    stars = sorted(r.metrics.stars for r in u.rows)
    created = sorted(r.metrics.created_at.date() for r in u.rows)
    lines = [
        f"## {h.topic_label} (`{path}`)",
        "",
        f"- Built {h.built_at:%Y-%m-%d %H:%M} UTC by `{h.builder}`, seed {h.seed}, max {h.max_size}",
        f"- Found {c.search_total}; excluded {excluded}; eligible {c.eligible}; rows {c.rows}"
        + (" (sampled)" if c.sampled else ""),
        f"- Stars {stars[0]}-{stars[-1]} (window {h.stars_min}-{h.stars_max}); created {created[0]} to {created[-1]}",
        f"- Queries: {'; '.join(f'`{q}`' for q in h.queries)}",
        "",
    ]
    issues = header_issues(u)
    lines += [f"**Header issues:** {len(issues)}"] + [f"- {i}" for i in issues] + [""]
    lines += [
        f"**Sample of {min(n, len(u.rows))} rows (seed {seed}).** Tick a row if it clearly belongs to the topic "
        "and its numbers look plausible on its GitHub page.",
        "",
    ]
    for i, r in enumerate(sample(u, n, seed), start=1):
        m = r.metrics
        desc = (m.description or "(no description)").replace("\n", " ")[:140]
        lines.append(f"{i}. [ ] [{r.repo}]({r.url}): {m.stars} stars, {m.language or '?'}, created "
                     f"{m.created_at:%Y-%m-%d}. {desc}")
    lines += ["", "On topic: __ / 20 (T18 needs at least 17). Checked by: ____"]
    return "\n".join(lines) + "\n"


@dataclass
class CacheReport:
    rows: int
    cached: int
    missing: list[str]
    paper_repos: int = 0  # rows whose cached README links arXiv papers
    papers_missing: list[str] = field(default_factory=list)  # of those, rows with an uncached paper


def cache_report(u: Universe, cache_dir: str | Path | None = None) -> CacheReport:
    """Which rows' READMEs the cache can serve with no network (demo-machine readiness)."""
    from sleeper.agent.data_tools import README_ACCEPT, README_PATH
    from sleeper.github import GitHubClient, NotFound, OfflineCacheMiss
    from sleeper.papers import MAX_PAPERS, PapersClient, PapersUnavailable, find_arxiv_ids

    missing: list[str] = []
    paper_repos, papers_missing = 0, []
    with GitHubClient(token="", offline=True, cache_dir=cache_dir) as gh:
        papers = PapersClient(offline=True, cache_dir=gh.cache.dir.parent / "papers")
        for r in u.rows:
            if r.metrics.readme_chars == 0:
                continue  # the readme tool answers NOT_FOUND without asking GitHub
            try:
                text = gh.get(README_PATH.format(repo=r.repo), accept=README_ACCEPT)
            except NotFound:
                continue  # a cached 404 is a cached answer
            except OfflineCacheMiss:
                missing.append(r.repo)
                continue
            ids = find_arxiv_ids(str(text or ""))[:MAX_PAPERS]
            if ids:
                paper_repos += 1
                try:
                    papers.lookup(ids)
                except PapersUnavailable:
                    papers_missing.append(r.repo)
    need = sum(1 for r in u.rows if r.metrics.readme_chars != 0)
    return CacheReport(need, need - len(missing), missing, paper_repos, papers_missing)


def warm(u: Universe, gh, *, floor: int = RATE_FLOOR, papers=None, progress=None) -> tuple[int, int, bool]:
    """Fetch uncached READMEs, then uncached papers. Returns (fetched, failed, stopped_at_floor)."""
    from sleeper.agent.data_tools import README_ACCEPT, README_PATH
    from sleeper.github import GitHubError, NotFound

    report = cache_report(u, gh.cache.dir)
    todo = report.missing
    fetched = failed = 0
    for repo in todo:
        remaining, _ = gh.rate_limits.get("core", (floor + 1, 0.0))
        if remaining <= floor:
            return fetched, failed, True
        try:
            gh.get(README_PATH.format(repo=repo), accept=README_ACCEPT)
            fetched += 1
        except NotFound:
            fetched += 1  # cached as a 404
        except GitHubError:
            failed += 1
        if progress:
            progress(f"{fetched + failed}/{len(todo)} {repo}")
    if todo:  # READMEs fetched just now may link papers too
        report = cache_report(u, gh.cache.dir)
    if report.papers_missing:
        from sleeper.papers import MAX_PAPERS, PapersClient, PapersUnavailable, find_arxiv_ids

        papers = papers or PapersClient(cache_dir=gh.cache.dir.parent / "papers", max_attempts=2, timeout=10)
        for repo in report.papers_missing:
            text = gh.get(README_PATH.format(repo=repo), accept=README_ACCEPT)
            try:
                papers.lookup(find_arxiv_ids(str(text or ""))[:MAX_PAPERS])
                fetched += 1
            except PapersUnavailable:
                failed += 1
            if progress:
                progress(f"papers {repo}")
    return fetched, failed, False


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m sleeper.universe_check", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="header sanity and a row sample to tick (markdown)")
    c.add_argument("universes", nargs="+")
    c.add_argument("--n", type=int, default=SAMPLE_SIZE)
    c.add_argument("--seed", type=int, default=SAMPLE_SEED)
    c.add_argument("--out", help="also write the sheet to this file")
    k = sub.add_parser("cache", help="README cache hits with no network")
    k.add_argument("universes", nargs="+")
    w = sub.add_parser("warm", help="fetch the READMEs the cache is missing")
    w.add_argument("universes", nargs="+")
    w.add_argument("--floor", type=int, default=RATE_FLOOR, help=f"stop at this many core requests left ({RATE_FLOOR})")
    args = p.parse_args(argv)

    if args.cmd == "check":
        sheets, bad = [], 0
        for path in args.universes:
            u = load(path)
            bad += len(header_issues(u))
            sheets.append(check_sheet(u, path, args.n, args.seed))
        text = "# T08 universe check\n\n" + "\n".join(sheets)
        print(text)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        return 1 if bad else 0

    if args.cmd == "cache":
        short = 0
        for path in args.universes:
            r = cache_report(load(path))
            short += len(r.missing)
            short += len(r.papers_missing)
            print(f"{path}: {r.cached}/{r.rows} READMEs cached"
                  + (f" (missing e.g. {', '.join(r.missing[:3])})" if r.missing else "")
                  + f"; papers for {r.paper_repos - len(r.papers_missing)}/{r.paper_repos} repos that link any")
        return 1 if short else 0

    from sleeper.github import GitHubClient

    with GitHubClient(max_wait=30, max_attempts=2, timeout=10) as gh:
        gh.get("/rate_limit", refresh=True)  # seeds gh.rate_limits["core"]
        stopped = False
        for path in args.universes:
            fetched, failed, stopped = warm(load(path), gh, floor=args.floor, progress=lambda m: print(m, file=sys.stderr))
            print(f"{path}: fetched {fetched}, failed {failed}" + ("; stopped at the rate-limit floor" if stopped else ""))
            if stopped:
                break
        print(f"GitHub requests: {gh.stats.network_requests}, cache hits: {gh.stats.cache_hits}")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
