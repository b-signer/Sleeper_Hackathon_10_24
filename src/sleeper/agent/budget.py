"""Run budget (D9): cumulative usage, turn count, phase and the budget line shown to the model each turn.

Phases, for the next turn (thresholds are W1's call, plan §4):
- researching: normal.
- wrapping_up: <= 25% of tokens left, or <= 3 turns left. The model is told to wrap up.
- submit_only: <= 10% of tokens left, the last turn, or spend headroom reached. Only submit is offered.
`exhausted`: tokens used >= budget or turns >= max_turns: the loop stops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sleeper.contracts import Usage

BudgetPhase = Literal["researching", "wrapping_up", "submit_only"]
PHASE_ORDER: tuple[BudgetPhase, ...] = ("researching", "wrapping_up", "submit_only")

WRAP_UP_TOKEN_FRACTION = 0.25
WRAP_UP_TURNS_LEFT = 3
SUBMIT_ONLY_TOKEN_FRACTION = 0.10
SUBMIT_ONLY_TURNS_LEFT = 1

_ADVICE: dict[BudgetPhase, str] = {
    "researching": "Research, then submit.",
    "wrapping_up": "Budget is running low: stop exploring, finish the cards and call submit soon.",
    "submit_only": "Budget exhausted: only submit is available now. Call submit with your best cards.",
}


def add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        cached_tokens=a.cached_tokens + b.cached_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
        cost_usd=round(a.cost_usd + b.cost_usd, 8),
    )


@dataclass
class Budget:
    token_budget: int
    max_turns: int
    spend_headroom_usd: float | None = None
    usage: Usage = field(default_factory=Usage)
    turns: int = 0  # completed model turns
    _phase: BudgetPhase = "researching"

    def add(self, usage: Usage) -> None:
        """Count one model turn and its usage."""
        self.turns += 1
        self.usage = add_usage(self.usage, usage)

    @property
    def tokens_remaining(self) -> int:
        return self.token_budget - self.usage.total_tokens

    @property
    def turns_left(self) -> int:
        return self.max_turns - self.turns

    @property
    def exhausted(self) -> bool:
        return self.usage.total_tokens >= self.token_budget or self.turns >= self.max_turns

    @property
    def spend_reached(self) -> bool:
        return self.spend_headroom_usd is not None and self.usage.cost_usd >= self.spend_headroom_usd

    @property
    def phase(self) -> BudgetPhase:
        """Phase for the next turn. Never moves backwards."""
        left = self.tokens_remaining / self.token_budget
        if left <= SUBMIT_ONLY_TOKEN_FRACTION or self.turns_left <= SUBMIT_ONLY_TURNS_LEFT or self.spend_reached:
            now: BudgetPhase = "submit_only"
        elif left <= WRAP_UP_TOKEN_FRACTION or self.turns_left <= WRAP_UP_TURNS_LEFT:
            now = "wrapping_up"
        else:
            now = "researching"
        if PHASE_ORDER.index(now) > PHASE_ORDER.index(self._phase):
            self._phase = now
        return self._phase

    def line(self) -> str:
        """The budget line the model sees before each turn."""
        u = self.usage
        phase = self.phase
        return (
            f"[budget] turn {self.turns + 1}/{self.max_turns} | "
            f"tokens used {u.total_tokens:,} of {self.token_budget:,} ({max(0, self.tokens_remaining):,} left) | "
            f"cost so far ${u.cost_usd:.4f} | phase: {phase}. {_ADVICE[phase]}"
        )
