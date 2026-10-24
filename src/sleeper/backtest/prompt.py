"""The backtest prompt: the live prompt (T11) plus an as-of block.

The live prompt is looked up at call time, so whatever T11 ships is what gets backtested. T11
removes backtest and "as-of" wording from the live prompt; the backtest adds its framing here
instead, after the live text, so neither prompt has to know about the other.
"""

from __future__ import annotations

from datetime import date

from sleeper.backtest.types import PromptFn
from sleeper.contracts import RunSettings, UniverseHeader

AS_OF_BLOCK = """
Backtest rules (this run is scored later against what happened after {as_of}):
- Today is {as_of} for this run. Every tool output shows the repos as they were on that date.
- Don't use anything you know about these repos from after {as_of}: later star counts, releases, \
news or adoption. Your memory still isn't a source, and anything it adds from after that date is \
contamination.
- Some metrics can't be rebuilt for a past date, so they show as unknown (null). Treat unknown as \
unknown, not as zero or as bad.
- Write "proven wrong if" signals for the months after {as_of}.
"""


def live_prompt(settings: RunSettings, header: UniverseHeader) -> str:
    from sleeper.agent import prompt  # looked up per call, so T11's changes apply

    return prompt.system_prompt(settings, header)


def backtest_prompt(
    settings: RunSettings, header: UniverseHeader, as_of: date, *, base: PromptFn | None = None
) -> str:
    """``base`` (default: the live prompt) followed by the as-of block."""
    text = (base or live_prompt)(settings, header)
    return text.rstrip() + "\n" + AS_OF_BLOCK.format(as_of=as_of.isoformat())
