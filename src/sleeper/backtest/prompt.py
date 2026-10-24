"""The backtest prompt: the live prompt (T11) plus an as-of block.

The live prompt is looked up at call time, so whatever T11 ships is what gets backtested. When it
takes a ``today`` argument (T11 does), it gets the as-of date, so its dates ("today", the 3- and
6-month horizons for "proven wrong if") are relative to that date. The live prompt calls the run
live and the metrics current; the block after it overrides exactly that, rather than editing T11's
sentences, so it keeps working as the live text changes.
"""

from __future__ import annotations

import inspect
from datetime import date

from sleeper.backtest.types import PromptFn
from sleeper.contracts import RunSettings, UniverseHeader

AS_OF_BLOCK = """
BACKTEST OVERRIDE (this section wins over anything above that says the run is live or the metrics are current)
- Today is {as_of} for this run. Every tool output shows the repos as they were on that date, and the run is \
scored later against what happened after it.
- Don't use anything you know about these repos from after {as_of}: later star counts, releases, news or \
adoption. Your memory still isn't a source, and anything it adds from after that date is contamination.
- Some metrics can't be rebuilt for a past date, so they show as unknown (null). Treat unknown as unknown, not as \
zero or as bad.
- Write as if {as_of} is the present, with "proven wrong if" signals for the months after it. The language rules \
above still apply.
"""


def live_prompt(settings: RunSettings, header: UniverseHeader, today: date | None = None) -> str:
    from sleeper.agent import prompt  # looked up per call, so T11's changes apply

    fn = prompt.system_prompt
    if today is not None and "today" in inspect.signature(fn).parameters:
        return fn(settings, header, today=today)
    return fn(settings, header)


def backtest_prompt(
    settings: RunSettings, header: UniverseHeader, as_of: date, *, base: PromptFn | None = None
) -> str:
    """``base`` (default: the live prompt, with today = ``as_of``) followed by the as-of block."""
    text = base(settings, header) if base is not None else live_prompt(settings, header, today=as_of)
    return text.rstrip() + "\n" + AS_OF_BLOCK.format(as_of=as_of.isoformat())
