"""Run records on disk (C3): list runs and universes, read results, follow events.jsonl as it grows."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ValidationError

from sleeper.contracts import RunEvent, RunResult, RunSettings, RunStatus, Universe, parse_event

log = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}_[A-Za-z0-9_.-]+$")


class RunInfo(BaseModel):
    """One row of the run list."""

    run_id: str
    topic: str
    model: str
    status: RunStatus | Literal["running"]
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    cards: int = 0
    cost_usd: float | None = None
    writable: bool = True  # False for recorded runs in read-only dirs, e.g. the fake run


class ModelInfo(BaseModel):
    """One model the launcher can offer."""

    id: str
    label: str
    input_per_m: float | None = None  # USD per million tokens; None if unknown
    output_per_m: float | None = None
    offline_ok: bool = False  # True only for the scripted fake model


class ServerConfig(BaseModel):
    """What the launcher needs to know about this server."""

    offline: bool
    models: list[ModelInfo]
    default_model: str
    fake_model: str


class UniverseInfo(BaseModel):
    """One prebuilt universe the launcher can offer."""

    file: str
    topic: str
    topic_label: str
    rows: int
    built_at: AwareDatetime


def list_universes(dirs: Sequence[Path]) -> list[UniverseInfo]:
    """Every valid universe file in `dirs`. Other JSON files are skipped."""
    out = []
    for d in dirs:
        for path in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                header = Universe.model_validate_json(path.read_text(encoding="utf-8")).header
            except (OSError, ValueError):
                continue
            out.append(
                UniverseInfo(
                    file=path.as_posix(),
                    topic=header.topic,
                    topic_label=header.topic_label,
                    rows=header.counts.rows,
                    built_at=header.built_at,
                )
            )
    return out


def new_run_id(dirs: Sequence[Path], topic: str, now: datetime | None = None) -> str:
    """`YYYYMMDD-HHMMSS_<topic>`, with a suffix if that second is already taken in any of `dirs`."""
    base = f"{(now or datetime.now(UTC)):%Y%m%d-%H%M%S}_{re.sub(r'[^A-Za-z0-9_.-]', '-', topic)}"
    run_id, n = base, 1
    while any((d / run_id).exists() for d in dirs):
        n += 1
        run_id = f"{base}-{n}"
    return run_id


class RunStore:
    """Run directories. New runs go in `runs_dir`; `extra_dirs` are listed but never written to."""

    def __init__(self, runs_dir: Path, extra_dirs: Sequence[Path] = ()):
        self.runs_dir = runs_dir
        self.extra_dirs = list(extra_dirs)

    def find(self, run_id: str) -> Path | None:
        """The run's directory, or None. Rejects anything that isn't a run ID (no path tricks)."""
        if not RUN_ID_RE.match(run_id):
            return None
        for d in [self.runs_dir, *self.extra_dirs]:
            if (d / run_id).is_dir():
                return d / run_id
        return None

    def list(self) -> list[RunInfo]:
        """Every readable run, newest first. The first directory wins if a run ID appears twice."""
        seen: dict[str, RunInfo] = {}
        for d in [self.runs_dir, *self.extra_dirs]:
            for run_dir in d.iterdir() if d.is_dir() else []:
                if run_dir.name in seen or not RUN_ID_RE.match(run_dir.name):
                    continue
                info = self.info(run_dir, writable=d == self.runs_dir)
                if info:
                    seen[run_dir.name] = info
        return sorted(seen.values(), key=lambda r: r.run_id, reverse=True)

    def info(self, run_dir: Path, writable: bool = True) -> RunInfo | None:
        try:
            result = read_result(run_dir)
            if result:
                return RunInfo(
                    run_id=run_dir.name,
                    topic=result.settings.topic,
                    model=result.settings.model,
                    status=result.status,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    cards=len(result.cards),
                    cost_usd=result.usage.cost_usd,
                    writable=writable,
                )
            settings = RunSettings.model_validate_json((run_dir / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("skipping unreadable run %s: %s", run_dir, e)
            return None
        first = _first_event(run_dir / "events.jsonl")
        return RunInfo(
            run_id=run_dir.name,
            topic=settings.topic,
            model=settings.model,
            status="running",
            started_at=first.ts if first else None,
            writable=writable,
        )


def read_result(run_dir: Path) -> RunResult | None:
    path = run_dir / "result.json"
    return RunResult.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def read_events(path: Path) -> list[RunEvent]:
    return [parse_event(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _first_event(path: Path) -> RunEvent | None:
    try:
        with path.open(encoding="utf-8") as f:
            line = f.readline()
        return parse_event(line) if line.strip() else None
    except (OSError, ValidationError, json.JSONDecodeError):
        return None


async def follow_events(
    run_dir: Path, from_seq: int = 0, poll_s: float = 0.2, idle_timeout_s: float = 900, result_wait_s: float = 10
) -> AsyncIterator[RunEvent]:
    """Yield events as they are appended to events.jsonl, until `run_finished`.

    After `run_finished` it waits up to `result_wait_s` for result.json, so a client can fetch the
    result as soon as the stream ends. Also stops at the end of the file once result.json exists
    (a run that died without `run_finished`), or after `idle_timeout_s` with no new lines (a run
    orphaned by a restart).
    """
    path = run_dir / "events.jsonl"
    pos, buf, idle = 0, b"", 0.0
    while True:
        lines: list[bytes] = []
        if path.exists():
            with path.open("rb") as f:
                f.seek(pos)
                chunk = f.read()
            pos += len(chunk)
            *lines, buf = (buf + chunk).split(b"\n")  # keep a partly written last line for later
        for line in lines:
            if not line.strip():
                continue
            event = parse_event(line)
            if event.seq >= from_seq:
                yield event
            if event.type == "run_finished":
                waited = 0.0
                while not (run_dir / "result.json").exists() and waited < result_wait_s:
                    await asyncio.sleep(poll_s)
                    waited += poll_s
                return
        if lines:
            idle = 0.0
        elif (run_dir / "result.json").exists() or idle >= idle_timeout_s:
            return
        await asyncio.sleep(poll_s)
        idle += poll_s


async def replay_events(run_dir: Path, speed: float, max_gap_s: float = 2.0) -> AsyncIterator[RunEvent]:
    """Yield a finished run's events with their original spacing divided by `speed` (0 = no delay).

    Gaps longer than `max_gap_s` are shortened first, so a slow model call doesn't stall the replay.
    """
    prev = None
    for event in read_events(run_dir / "events.jsonl"):
        if prev is not None and speed > 0:
            gap = min(max((event.ts - prev.ts).total_seconds(), 0.0), max_gap_s)
            await asyncio.sleep(gap / speed)
        yield event
        prev = event
