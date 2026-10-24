"""How the server starts runs. T10 provides the real runner; FakeRunner stands in until then.

A runner is any callable `runner(settings, on_start)` that does one run to completion in the
calling thread (the server calls it in a background thread). It must:

1. create `<runs_dir>/<run_id>/` with `settings.json`, then call `on_start(run_id)`;
2. append each event to `events.jsonl` as it happens (one line per event, flushed);
3. write `result.json` at the end.

Raising before `on_start` refuses the run (spend guard, missing universe, web-enabled model):
the server answers the request with the error and no run exists. T10's
`execute_run(settings, on_start=...)` has exactly this shape.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sleeper.contracts import RunEvent, RunFinished, RunResult, RunSettings, Usage, dump_event, parse_event
from sleeper.server.store import new_run_id, read_events, read_result

log = logging.getLogger(__name__)

FAKE_RUN_DIR = Path("fixtures/fake/runs/20261024-140000_fake")


class Runner(Protocol):
    def __call__(self, settings: RunSettings, on_start: Callable[[str], None]) -> None: ...


def write_json_atomic(path: Path, text: str) -> None:
    """Write via a temp file, so readers never see half a file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


@dataclass
class FakeRunner:
    """Re-emits a recorded run as if it were happening now, `delay_s` apart. No network, no spend."""

    runs_dir: Path
    source: Path = FAKE_RUN_DIR
    delay_s: float = 0.4

    def __call__(self, settings: RunSettings, on_start: Callable[[str], None]) -> None:
        events = read_events(self.source / "events.jsonl")
        result = read_result(self.source)
        run_id = new_run_id([self.runs_dir], settings.topic)
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True)
        write_json_atomic(run_dir / "settings.json", settings.model_dump_json(indent=2))
        on_start(run_id)
        started = datetime.now(UTC)
        with (run_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            for i, event in enumerate(events):
                if i:
                    time.sleep(self.delay_s)
                update: dict = {"run_id": run_id, "ts": datetime.now(UTC)}
                if event.type == "run_started":
                    update["settings"] = settings
                f.write(dump_event(event.model_copy(update=update)) + "\n")
                f.flush()
        if result:
            done = result.model_copy(
                update={"run_id": run_id, "settings": settings, "started_at": started, "finished_at": datetime.now(UTC)}
            )
            write_json_atomic(run_dir / "result.json", done.model_dump_json(indent=2))


@dataclass
class Launch:
    """One run being started in a background thread. `wait()` returns once it has started or failed."""

    thread: threading.Thread | None = None
    run_id: str | None = None
    refused: Exception | None = None  # raised before on_start: the run never existed
    _started: threading.Event = field(default_factory=threading.Event)

    def wait(self, timeout_s: float) -> bool:
        return self._started.wait(timeout_s)


def launch(runner: Runner, settings: RunSettings, find_run_dir: Callable[[str], Path | None]) -> Launch:
    """Start `runner` in a daemon thread. A crash after on_start closes the run with status `error`."""
    state = Launch()

    def on_start(run_id: str) -> None:
        state.run_id = run_id
        state._started.set()

    def target() -> None:
        try:
            runner(settings, on_start)
        except Exception as e:  # the run must end with a record, whatever went wrong
            if state.run_id is None:
                state.refused = e
            else:
                log.exception("run %s crashed", state.run_id)
                run_dir = find_run_dir(state.run_id)
                if run_dir is not None:
                    close_with_error(settings, state.run_id, run_dir, f"{type(e).__name__}: {e}")
        finally:
            state._started.set()

    state.thread = threading.Thread(target=target, name="sleeper-run", daemon=True)
    state.thread.start()
    return state


def _parse_or_none(line: str) -> RunEvent | None:
    try:
        return parse_event(line)
    except ValueError:  # e.g. a line cut off by the crash
        return None


def close_with_error(settings: RunSettings, run_id: str, run_dir: Path, error: str) -> None:
    """Append `run_finished(error)` and write an `error` result, unless the run already has them."""
    path = run_dir / "events.jsonl"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [line for line in text.splitlines() if line.strip()]
    first = _parse_or_none(lines[0]) if lines else None
    last = _parse_or_none(lines[-1]) if lines else None
    now = datetime.now(UTC)
    if last is None or last.type != "run_finished":
        finished = RunFinished(seq=len(lines), ts=now, run_id=run_id, status="error", error=error)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + dump_event(finished) + "\n")
    if not (run_dir / "result.json").exists():
        result = RunResult(
            run_id=run_id,
            status="error",
            error=error,
            settings=settings,
            started_at=first.ts if first else now,
            finished_at=now,
            turns=0,
            usage=Usage(),
        )
        write_json_atomic(run_dir / "result.json", result.model_dump_json(indent=2))
