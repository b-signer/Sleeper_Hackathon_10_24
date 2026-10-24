"""Local web server (T14). API reference: the docstring of `sleeper.server.app`."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from sleeper.config import Settings
from sleeper.server.app import create_app
from sleeper.server.runner import FAKE_RUN_DIR, FakeRunner, Runner
from sleeper.server.store import RunInfo, RunStore, UniverseInfo

FAKE_DIR = Path("fixtures/fake")

__all__ = ["FakeRunner", "RunInfo", "RunStore", "Runner", "UniverseInfo", "app_from_settings", "create_app"]


def app_from_settings(settings: Settings, runner: Runner | None = None) -> FastAPI:
    """The app `sleeper serve` runs: real runs in SLEEPER_RUNS_DIR, plus the fake universe and fake run.

    Until T10 lands, new runs use FakeRunner (no network, no spend). With T10:
    `runner=lambda s, on_start: execute_run(s, on_start=on_start)`.
    """
    store = RunStore(settings.runs_dir, extra_dirs=[FAKE_RUN_DIR.parent])
    runner = runner or FakeRunner(settings.runs_dir)
    return create_app(store, universe_dirs=[settings.universes_dir, FAKE_DIR], runner=runner)
