"""Local web server (T14). API reference: the docstring of `sleeper.server.app`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from sleeper.config import Settings
from sleeper.contracts import RunSettings
from sleeper.server.app import FAKE_MODEL, create_app
from sleeper.server.runner import FAKE_RUN_DIR, FakeRunner, Runner
from sleeper.server.store import ModelInfo, RunInfo, RunStore, ServerConfig, UniverseInfo

FAKE_DIR = Path("fixtures/fake")
FAKE_TOPIC = "fake"

__all__ = [
    "FAKE_MODEL",
    "FakeRunner",
    "ModelInfo",
    "RunInfo",
    "RunStore",
    "Runner",
    "ServerConfig",
    "UniverseInfo",
    "agent_runner",
    "app_from_settings",
    "create_app",
    "server_config",
]


class RunRefused(ValueError):
    """Settings this server won't run; raised before any run folder exists, so the request gets a 400."""


def agent_runner(cfg: Settings) -> Runner:
    """Runs T10's execute_run. The scripted fake model is free and offline, but only knows the fake universe."""

    def run(settings: RunSettings, on_start: Callable[[str], None]) -> None:
        fake = settings.model == FAKE_MODEL
        if fake and settings.topic != FAKE_TOPIC:
            raise RunRefused(f"the scripted fake model only works on the '{FAKE_TOPIC}' universe")
        if cfg.offline and not fake:
            raise RunRefused(f"offline mode (SLEEPER_OFFLINE=1): only {FAKE_MODEL} can run")
        from sleeper.agent.runner import execute_run

        execute_run(settings, fake=fake, cfg=cfg, on_start=on_start)

    return run


def server_config(cfg: Settings) -> ServerConfig:
    from sleeper.agent.openrouter import PRICES

    models = [ModelInfo(id=FAKE_MODEL, label="Scripted fake (offline, free)", offline_ok=True)]
    for model_id, role in [(cfg.dev_model, "Development"), (cfg.demo_model, "Demo")]:
        if model_id not in {m.id for m in models}:
            price = PRICES.get(model_id)
            models.append(
                ModelInfo(
                    id=model_id,
                    label=f"{role}: {model_id}",
                    input_per_m=price[0] if price else None,
                    output_per_m=price[1] if price else None,
                )
            )
    default = FAKE_MODEL if cfg.offline else cfg.dev_model
    return ServerConfig(offline=cfg.offline, models=models, default_model=default, fake_model=FAKE_MODEL)


def app_from_settings(settings: Settings, runner: Runner | None = None) -> FastAPI:
    """The app `sleeper serve` runs: runs in SLEEPER_RUNS_DIR plus the recorded fake run, started with T10."""
    store = RunStore(settings.runs_dir, extra_dirs=[FAKE_RUN_DIR.parent])
    return create_app(
        store,
        universe_dirs=[settings.universes_dir, FAKE_DIR],
        runner=runner or agent_runner(settings),
        config=server_config(settings),
    )
