"""Local web server (T14). API reference: the docstring of `sleeper.server.app`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from sleeper.config import Settings
from sleeper.contracts import RunSettings
from sleeper.server.app import FAKE_MODEL, create_app, refusal_for
from sleeper.server.runner import FAKE_RUN_DIR, FakeRunner, Runner, RunRefused
from sleeper.server.store import ModelInfo, Refusal, RunInfo, RunStore, ServerConfig, SpendInfo, UniverseInfo
from sleeper.server.topics import TopicService, topic_service_from_settings

FAKE_DIR = Path("fixtures/fake")
FAKE_TOPIC = "fake"

__all__ = [
    "FAKE_MODEL",
    "FakeRunner",
    "ModelInfo",
    "Refusal",
    "RunInfo",
    "RunRefused",
    "RunStore",
    "Runner",
    "ServerConfig",
    "SpendInfo",
    "TopicService",
    "UniverseInfo",
    "agent_runner",
    "app_from_settings",
    "create_app",
    "refusal_for",
    "server_config",
    "spend_info",
    "topic_service_from_settings",
]

OFFLINE_REASON = "Offline mode (SLEEPER_OFFLINE=1): live runs are off. Replay works."
NO_KEY_REASON = "OPENROUTER_API_KEY is not set, so live runs can't call a model. Replay works."
NO_GITHUB_REASON = "GITHUB_TOKEN is not set, so new topics can't be built."


def agent_runner(cfg: Settings) -> Runner:
    """Runs T10's execute_run. The scripted fake model is free and offline, but only knows the fake universe.

    Refusals (RunRefused here; SpendGuardError, FileNotFoundError, WebAccessRefused and
    ModelError("auth") from execute_run) are raised before `on_start`, so no run folder exists.
    """

    def run(settings: RunSettings, on_start: Callable[[str], None]) -> None:
        fake = settings.model == FAKE_MODEL
        if fake and settings.topic != FAKE_TOPIC:
            raise RunRefused(f"The scripted fake model only works on the '{FAKE_TOPIC}' universe.", "other")
        if cfg.offline and not fake:
            raise RunRefused(f"Offline mode (SLEEPER_OFFLINE=1): only {FAKE_MODEL} can run.", "offline")
        if not fake and not cfg.openrouter_api_key:
            raise RunRefused("OPENROUTER_API_KEY is not set, so the model can't be called.", "no_api_key")
        from sleeper.agent.runner import execute_run

        execute_run(settings, fake=fake, cfg=cfg, on_start=on_start)

    return run


def server_config(cfg: Settings) -> ServerConfig:
    from sleeper.agent.openrouter import PRICES
    from sleeper.agent.spend import estimate_max_cost

    default_budget = RunSettings.model_fields["token_budget"].default
    models: list[ModelInfo] = []
    for model_id, role in [(cfg.dev_model, "Development"), (cfg.demo_model, "Demo")]:
        if model_id in {m.id for m in models}:
            continue
        price = PRICES.get(model_id)
        worst = estimate_max_cost(RunSettings(topic=FAKE_TOPIC, model=model_id, token_budget=default_budget))
        models.append(
            ModelInfo(
                id=model_id,
                label=f"{role}: {model_id}",
                input_per_m=price[0] if price else None,
                output_per_m=price[1] if price else None,
                max_run_usd=round(worst, 4),
            )
        )
    # Last on purpose: an offline test, never the default for a live run.
    models.append(ModelInfo(id=FAKE_MODEL, label="Offline test: scripted fake (free)", offline_ok=True, max_run_usd=0.0))
    live_disabled = OFFLINE_REASON if cfg.offline else None if cfg.openrouter_api_key else NO_KEY_REASON
    return ServerConfig(
        offline=cfg.offline,
        models=models,
        default_model=cfg.dev_model,
        fake_model=FAKE_MODEL,
        default_token_budget=default_budget,
        live_disabled_reason=live_disabled,
        custom_topics_reason=live_disabled or (None if cfg.github_token else NO_GITHUB_REASON),
    )


def spend_info(cfg: Settings) -> SpendInfo | None:
    """Spent so far on this key (OpenRouter's figure, else the local ledger); None when offline."""
    from sleeper.agent import spend

    if cfg.offline:
        return None
    if cfg.openrouter_api_key:
        try:
            return SpendInfo(spent_usd=spend.fetch_key_usage(cfg.openrouter_api_key), source="openrouter")
        except Exception:  # network, HTTP, shape: same fallback as the guard itself
            pass
    return SpendInfo(spent_usd=spend.local_spend(cfg.runs_dir), source="local")


def app_from_settings(settings: Settings, runner: Runner | None = None, topics: TopicService | None = None) -> FastAPI:
    """The app `sleeper serve` runs: runs in SLEEPER_RUNS_DIR plus the recorded fake run, started with T10.
    Universes: the prebuilt ones, custom topics built from the launcher (under the cache), and the fake one."""
    store = RunStore(settings.runs_dir, extra_dirs=[FAKE_RUN_DIR.parent])
    topics = topics or topic_service_from_settings(settings)
    return create_app(
        store,
        universe_dirs=[settings.universes_dir, topics.out_dir, FAKE_DIR],
        runner=runner or agent_runner(settings),
        config=server_config(settings),
        spend=lambda: spend_info(settings),
        topics=topics,
    )
