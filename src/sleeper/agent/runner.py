"""execute_run(): the one entry point that runs the agent (CLI now, UI later).

Order: web guard -> universe -> spend guard -> model -> run folder -> run_agent -> result.json.
Every refusal (WebAccessRefused, FileNotFoundError, SpendGuardError, ModelError("auth"))
happens before the run folder exists.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from sleeper.agent.openrouter import OpenRouterModel, assert_no_web
from sleeper.agent.records import RunRecorder
from sleeper.agent.spend import check_spend
from sleeper.agent.types import ChatModel, ToolExecutor
from sleeper.config import Settings, load_settings
from sleeper.contracts import RunEvent, RunFinished, RunResult, RunSettings, Universe, Usage

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
FAKE_UNIVERSE = Path("fixtures/fake/universe.json")
UNIVERSES_DIR = Path("universes")


def _find(path: Path) -> Path | None:
    """A relative path is tried from the working directory, then from the repo root."""
    for p in (path,) if path.is_absolute() else (path, REPO_ROOT / path):
        if p.is_file():
            return p
    return None


def load_universe(settings: RunSettings) -> Universe:
    """settings.universe_file if set; topic 'fake' -> the T03 fixture; else universes/<topic>.json."""
    if settings.universe_file:
        path = Path(settings.universe_file)
    elif settings.topic == "fake":
        found = _find(FAKE_UNIVERSE)
        if found is None:
            from sleeper.fakes import fake_universe

            return fake_universe()
        path = found
    else:
        path = UNIVERSES_DIR / f"{settings.topic}.json"
    found = _find(path)
    if found is None:
        raise FileNotFoundError(f"no universe file for topic {settings.topic!r}: {path} not found (build it first)")
    return Universe.model_validate_json(found.read_text(encoding="utf-8"))


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def execute_run(
    settings: RunSettings,
    *,
    fake: bool = False,
    cfg: Settings | None = None,
    model: ChatModel | None = None,
    tools: ToolExecutor | None = None,
    universe: Universe | None = None,
    on_start: Callable[[str], None] | None = None,
    echo: Callable[[RunEvent], None] | None = None,
) -> RunResult:
    """Run the agent once and record it under cfg.runs_dir/<run_id>/.

    fake=True: scripted model + stub tools on the fake universe; no network, no spend check.
    Raises WebAccessRefused / FileNotFoundError / SpendGuardError / ModelError("auth") before
    any run folder exists. Model and tool failures during the run become the result's status.
    """
    cfg = cfg or load_settings()
    assert_no_web(settings.model)
    if model is not None:
        assert_no_web(model.model_id)
    if universe is None:
        universe = load_universe(settings)
    headroom = None if fake else check_spend(settings, cfg)

    own_model = model is None
    if model is None:
        if fake:
            from sleeper.agent.scripted import ScriptedModel, fake_script

            model = ScriptedModel(fake_script(universe))
        else:
            model = OpenRouterModel(settings.model, cfg.openrouter_api_key)
    if tools is None:
        from sleeper.agent.stub_tools import StubTools  # until T09's executor lands

        tools = StubTools(universe)
    from sleeper.agent.loop import run_agent  # W1's engine; imported lazily

    rec = RunRecorder.create(cfg.runs_dir, settings, echo=echo)
    started = datetime.now(UTC)
    try:
        if on_start is not None:
            on_start(rec.run_id)
        try:
            result = run_agent(settings, universe, model, tools, rec, git_sha=git_sha(), spend_headroom_usd=headroom)
        except Exception as e:  # run_agent shouldn't raise; if it does, still leave a valid record
            log.exception("run %s crashed", rec.run_id)
            msg = f"{type(e).__name__}: {e}"
            try:
                rec.emit(RunFinished, status="error", error=msg)
            except Exception:
                pass
            rec.write_result(
                RunResult(
                    run_id=rec.run_id,
                    status="error",
                    error=msg,
                    settings=settings,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    turns=0,
                    usage=Usage(),
                )
            )
            raise
        rec.write_result(result)
        return result
    finally:
        rec.close()
        close = getattr(model, "close", None)
        if own_model and callable(close):
            close()
