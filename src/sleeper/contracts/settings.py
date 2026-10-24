"""Run settings: what the user chose in the launcher (F7.1), plus run limits (D9)."""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from sleeper.contracts.common import Contract


class RunSettings(Contract):
    topic: str = Field(min_length=1, description="Topic id from the topics file, e.g. 'rust-devtools'")
    universe_file: str | None = Field(None, description="Prebuilt universe path; None = default for the topic")
    stars_min: int = Field(50, ge=0)
    stars_max: int = Field(2000, ge=0)
    created_after: date = date(2025, 1, 1)
    max_picks: int = Field(5, ge=1, le=5)
    min_ranked: int = Field(10, ge=1, description="Ranking target (F2.3); below it the run is flagged, not failed")
    model: str = Field(min_length=1, description="OpenRouter model id, e.g. 'openai/gpt-5.6-luna'")
    token_budget: int = Field(300_000, gt=0)
    max_turns: int = Field(25, gt=0)
    max_wall_clock_s: int = Field(600, gt=0)
    seed: int = 7

    @model_validator(mode="after")
    def _check_ranges(self) -> RunSettings:
        if self.stars_min > self.stars_max:
            raise ValueError("stars_min must be <= stars_max")
        return self
