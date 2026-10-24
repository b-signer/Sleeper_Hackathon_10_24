"""T14: the local web server. Serves the page and run data, live and recorded (F7.5, C3, C4, D11).

| Method | Path                            | Returns                                                       |
|--------|---------------------------------|---------------------------------------------------------------|
| GET    | /                               | The page (static/index.html; no external assets)              |
| GET    | /api/config                     | ServerConfig: offline flag, models with prices, spend guard   |
| GET    | /api/spend                      | SpendInfo {spent_usd, guard_usd, source}; null when offline   |
| GET    | /api/universes                  | [UniverseInfo] for the launcher                               |
| GET    | /api/runs                       | [RunInfo], newest first                                       |
| POST   | /api/runs                       | Body: RunSettings. 202 {"run_id"}; 409 if a run is in progress; |
|        |                                 | 400 {"detail": Refusal} if it was refused before starting     |
| GET    | /api/runs/{run_id}              | RunInfo                                                       |
| GET    | /api/runs/{run_id}/result       | RunResult; 409 while the run is in progress                   |
| GET    | /api/runs/{run_id}/events       | SSE: every event so far, then new ones live, then `end`       |
| GET    | /api/runs/{run_id}/replay       | SSE: a finished run at ?speed= (default 4, 0 = instant)       |

SSE messages: `id` is the event's seq, `event` its type, `data` the event as JSON (the RunEvent
contract). Every stream ends with an `end` message: close the EventSource on it, or the browser
reconnects. When `end` arrives the result is ready to fetch. A reconnect sends Last-Event-ID, and
the live stream resumes after that seq.

A refused run (400) never creates a run folder. Its `detail` is a Refusal object,
`{"reason": "spend_guard" | "offline" | "no_universe" | "no_api_key" | "web_model" | "other",
"message": "<human sentence>"}`, mapped from the exception type by `refusal_for`. Other errors
(404, 409, 422, 504) keep FastAPI's usual detail (a string, or a list for 422).
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import get_args

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from sleeper.contracts import RunEvent, RunResult, RunSettings, dump_event
from sleeper.agent.openrouter import WebAccessRefused
from sleeper.agent.spend import SpendGuardError
from sleeper.agent.types import ModelError
from sleeper.server.runner import Launch, Runner, RunRefused, launch
from sleeper.server.store import (
    ModelInfo,
    Refusal,
    RefusalReason,
    RunInfo,
    RunStore,
    ServerConfig,
    SpendInfo,
    UniverseInfo,
    follow_events,
    list_universes,
    read_result,
    replay_events,
)

STATIC_DIR = Path(__file__).parent / "static"
FAKE_MODEL = "scripted/fake"  # T10's ScriptedModel: offline, free, fake universe only


def refusal_for(e: Exception) -> Refusal:
    """The 400 detail for an exception raised before a run started."""
    if isinstance(e, RunRefused):
        reason = e.reason if e.reason in get_args(RefusalReason) else "other"
    elif isinstance(e, SpendGuardError):
        reason = "spend_guard"
    elif isinstance(e, WebAccessRefused):
        reason = "web_model"
    elif isinstance(e, FileNotFoundError):
        reason = "no_universe"
    elif isinstance(e, ModelError) and e.kind == "auth":
        reason = "no_api_key"
    else:
        reason = "other"
    message = str(e) or type(e).__name__
    if reason == "other" and not isinstance(e, RunRefused):
        message = f"{type(e).__name__}: {message}"
    return Refusal(reason=reason, message=message)


def _sse(events: AsyncIterator[RunEvent]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for e in events:
            yield f"id: {e.seq}\nevent: {e.type}\ndata: {dump_event(e)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def create_app(
    store: RunStore,
    universe_dirs: Sequence[Path],
    runner: Runner,
    start_timeout_s: float = 30,
    config: ServerConfig | None = None,
    spend: Callable[[], SpendInfo | None] | None = None,
) -> FastAPI:
    config = config or ServerConfig(
        offline=True,
        models=[ModelInfo(id=FAKE_MODEL, label="Scripted fake (offline, free)", offline_ok=True)],
        default_model=FAKE_MODEL,
        fake_model=FAKE_MODEL,
    )
    app = FastAPI(title="Sleeper", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    current: list[Launch] = []  # the latest run started from this server
    lock = threading.Lock()

    def run_dir_or_404(run_id: str) -> Path:
        run_dir = store.find(run_id)
        if run_dir is None:
            raise HTTPException(404, f"no run {run_id!r}")
        return run_dir

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/config")
    def server_config() -> ServerConfig:
        return config

    @app.get("/api/spend")
    def spent() -> SpendInfo | None:
        return spend() if spend is not None else None

    @app.get("/api/universes")
    def universes() -> list[UniverseInfo]:
        return list_universes(universe_dirs)

    @app.get("/api/runs")
    def runs() -> list[RunInfo]:
        return store.list()

    @app.post("/api/runs", status_code=202)
    def start_run(settings: RunSettings) -> dict[str, str]:
        # One run at a time: runs spend real money (D10), and the demo only needs one.
        with lock:
            if current and current[0].thread and current[0].thread.is_alive():
                raise HTTPException(409, f"run {current[0].run_id or '(starting)'} is still in progress")
            started = launch(runner, settings, store.find)
            current[:] = [started]
            if not started.wait(start_timeout_s):
                raise HTTPException(504, "the run did not start in time")
        if started.refused is not None:
            raise HTTPException(400, refusal_for(started.refused).model_dump())
        if started.run_id is None:
            raise HTTPException(500, "the runner finished without starting a run")
        return {"run_id": started.run_id}

    @app.get("/api/runs/{run_id}")
    def run_info(run_id: str) -> RunInfo:
        run_dir = run_dir_or_404(run_id)
        info = store.info(run_dir, writable=run_dir.parent == store.runs_dir)
        if info is None:
            raise HTTPException(500, f"run {run_id!r} has unreadable records")
        return info

    @app.get("/api/runs/{run_id}/result")
    def result(run_id: str) -> RunResult:
        found = read_result(run_dir_or_404(run_id))
        if found is None:
            raise HTTPException(409, f"run {run_id!r} is still in progress")
        return found

    @app.get("/api/runs/{run_id}/events")
    def events(
        run_id: str,
        from_seq: int = Query(0, ge=0),
        last_event_id: str | None = Header(None),
    ) -> StreamingResponse:
        if last_event_id and last_event_id.isdigit():
            from_seq = max(from_seq, int(last_event_id) + 1)
        return _sse(follow_events(run_dir_or_404(run_id), from_seq=from_seq))

    @app.get("/api/runs/{run_id}/replay")
    def replay(run_id: str, speed: float = Query(4.0, ge=0, le=100)) -> StreamingResponse:
        run_dir = run_dir_or_404(run_id)
        if not (run_dir / "result.json").exists():
            raise HTTPException(409, f"run {run_id!r} is still in progress; stream /events instead")
        return _sse(replay_events(run_dir, speed=speed))

    return app
