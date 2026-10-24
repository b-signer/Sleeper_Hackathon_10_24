"""T10: Budget phases, exhaustion and the budget line."""

from __future__ import annotations

from sleeper.agent.budget import Budget
from sleeper.contracts import Usage


def u(tokens: int, cost: float = 0.0) -> Usage:
    return Usage(prompt_tokens=tokens, total_tokens=tokens, cost_usd=cost)


def test_budget_phases():
    b = Budget(token_budget=100_000, max_turns=25)
    assert b.phase == "researching" and not b.exhausted
    for _ in range(7):
        b.add(u(10_000))
    assert b.phase == "researching"  # 30k left, last turn 10k
    b.add(u(5_000))  # 25% left
    assert b.phase == "wrapping_up"
    b.add(u(15_000))  # 10% left
    assert b.phase == "submit_only" and not b.exhausted
    b.add(u(10_000))
    assert b.exhausted and b.tokens_remaining == 0
    assert b.usage.total_tokens == 100_000 and b.turns == 10


def test_budget_phases_by_turns():
    b = Budget(token_budget=1_000_000, max_turns=6)
    b.add(u(10))
    b.add(u(10))
    assert b.phase == "researching"  # 4 left
    b.add(u(10))
    assert b.phase == "wrapping_up"  # 3 left
    b.add(u(10))
    b.add(u(10))
    assert b.phase == "submit_only"  # last turn
    b.add(u(10))
    assert b.exhausted


def test_budget_phase_never_goes_back_and_spend_headroom():
    b = Budget(token_budget=1_000_000, max_turns=25, spend_headroom_usd=0.01)
    b.add(u(100, cost=0.02))
    assert b.phase == "submit_only"
    b.spend_headroom_usd = None
    assert b.phase == "submit_only"


def test_budget_line_has_the_numbers():
    b = Budget(token_budget=300_000, max_turns=25)
    b.add(Usage(prompt_tokens=3000, completion_tokens=200, total_tokens=3200, cost_usd=0.00084))
    line = b.line()
    assert "turn 2/25" in line
    assert "3,200" in line and "296,800 left" in line and "300,000" in line
    assert "$0.0008" in line and "researching" in line


def test_budget_projects_from_last_turn_size():
    """A small budget with big turns: submit_only comes before the fraction thresholds would trigger it."""
    b = Budget(token_budget=60_000, max_turns=25)
    b.add(u(12_000))
    assert b.phase == "researching"  # 48k left > 3 x 12k
    b.add(u(14_000))
    assert b.phase == "wrapping_up"  # 34k left < 3 x 14k (fraction alone: 57% left)
    b.add(u(16_000))
    assert b.phase == "submit_only"  # 18k left < 1.5 x 16k (fraction alone: 30% left)
    assert not b.exhausted
