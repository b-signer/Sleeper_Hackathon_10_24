"""Run records (C3): runs/<run_id>/settings.json, events.jsonl (live) and result.json.

RunRecorder is the EventSink the loop writes to. Each event is one line, written and
flushed under a lock as it happens, so another process (the UI) can tail the file
mid-run. result.json is written atomically (temp file + os.replace).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TextIO

from sleeper.contracts import RunEvent, RunResult, RunSettings, dump_event, parse_event

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
EVENTS_FILE = "events.jsonl"
RESULT_FILE = "result.json"


def new_run_id(topic: str, now: datetime | None = None) -> str:
    """'YYYYMMDD-HHMMSS_<topic>' (UTC); the topic is made filesystem-safe."""
    now = now or datetime.now(UTC)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", topic).strip("-.") or "run"
    return f"{now.astimezone(UTC):%Y%m%d-%H%M%S}_{safe}"


class RunRecorder:
    """Owns one run folder. Implements EventSink (thread-safe emit)."""

    def __init__(self, run_id: str, dir: Path, echo: Callable[[RunEvent], None] | None = None) -> None:
        self.run_id = run_id
        self.dir = dir
        self._echo = echo
        self._lock = threading.Lock()
        self._seq = 0
        self._file: TextIO | None = open(dir / EVENTS_FILE, "a", encoding="utf-8")

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        settings: RunSettings,
        *,
        run_id: str | None = None,
        echo: Callable[[RunEvent], None] | None = None,
    ) -> RunRecorder:
        """Make runs_dir/<run_id>/ (suffix -2, -3, ... if taken) and write settings.json."""
        base = run_id or new_run_id(settings.topic)
        runs_dir = Path(runs_dir)
        runs_dir.mkdir(parents=True, exist_ok=True)
        rid, n = base, 1
        while True:
            try:
                (runs_dir / rid).mkdir()
                break
            except FileExistsError:
                n += 1
                rid = f"{base}-{n}"
        d = runs_dir / rid
        (d / SETTINGS_FILE).write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return cls(rid, d, echo)

    @property
    def events_path(self) -> Path:
        return self.dir / EVENTS_FILE

    def emit(self, event_type: type[Any], /, **payload: Any) -> RunEvent:
        """Build the event (seq, ts=now UTC, run_id), append it as one line and flush. Thread-safe."""
        with self._lock:
            if self._file is None:
                raise RuntimeError(f"run {self.run_id}: recorder is closed")
            event = event_type(seq=self._seq, ts=datetime.now(UTC), run_id=self.run_id, **payload)
            self._file.write(dump_event(event) + "\n")
            self._file.flush()
            self._seq += 1
            if self._echo is not None:
                try:
                    self._echo(event)
                except Exception:  # a display problem must never break the run
                    log.exception("echo failed for event %d", event.seq)
        return event

    def write_result(self, result: RunResult) -> Path:
        """Write result.json atomically and return its path."""
        path = self.dir / RESULT_FILE
        tmp = self.dir / f".{RESULT_FILE}.tmp"
        tmp.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_events(path: Path) -> list[RunEvent]:
    """Parse events.jsonl. A partial last line (run still writing) is ignored; other bad lines raise."""
    text = Path(path).read_text(encoding="utf-8")
    lines = text.split("\n")
    complete, tail = lines[:-1], lines[-1]  # tail is "" when the file ends with a newline
    events = [parse_event(line) for line in complete if line.strip()]
    if tail.strip():
        try:
            events.append(parse_event(tail))
        except ValueError:
            pass  # half-written line
    return events
