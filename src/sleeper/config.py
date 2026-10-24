"""Settings loaded from the environment (and a local .env file, if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DEV_MODEL = "openai/gpt-5.6-luna"
DEFAULT_DEMO_MODEL = "openai/gpt-5.6-terra"

_TRUE = {"1", "true", "yes", "on"}


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE


@dataclass(frozen=True)
class Settings:
    github_token: str | None
    openrouter_api_key: str | None
    dev_model: str
    demo_model: str
    offline: bool
    cache_dir: Path
    runs_dir: Path


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings. Values already in the environment win over the .env file."""
    load_dotenv(env_file, override=False)
    env = os.environ
    return Settings(
        github_token=env.get("GITHUB_TOKEN") or None,
        openrouter_api_key=env.get("OPENROUTER_API_KEY") or None,
        dev_model=env.get("SLEEPER_DEV_MODEL") or DEFAULT_DEV_MODEL,
        demo_model=env.get("SLEEPER_DEMO_MODEL") or DEFAULT_DEMO_MODEL,
        offline=_flag(env.get("SLEEPER_OFFLINE")),
        cache_dir=Path(env.get("SLEEPER_CACHE_DIR") or ".cache"),
        runs_dir=Path(env.get("SLEEPER_RUNS_DIR") or "runs"),
    )
