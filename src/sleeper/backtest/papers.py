"""Linked papers (S2) as of the backtest date.

S2's citation counts are read from Semantic Scholar today, so they include citations made after
the as-of date: a future fact. Here they are always unknown (None). Papers first published after
the date are dropped too; the as-of README shouldn't link them, but a current README would.
Titles and dates are kept: they were fixed when the paper came out.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from sleeper.contracts.tools import LinkedPaper


class PapersLookup(Protocol):
    def lookup(self, arxiv_ids: list[str]) -> list[LinkedPaper]: ...


class AsOfPapers:
    """Wraps S2's ``PapersClient`` (built lazily, so an unused tool never makes one)."""

    def __init__(self, as_of: date, inner: PapersLookup | None = None) -> None:
        self.as_of = as_of
        self._inner = inner

    def lookup(self, arxiv_ids: list[str]) -> list[LinkedPaper]:
        if self._inner is None:
            from sleeper.papers import PapersClient

            self._inner = PapersClient(max_attempts=2, timeout=10)
        out = []
        for p in self._inner.lookup(arxiv_ids):
            if p.published and date.fromisoformat(p.published[:10]) > self.as_of:
                continue
            out.append(p.model_copy(update={"citation_count": None, "citations_as_of": None}))
        return out
