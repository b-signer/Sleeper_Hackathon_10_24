"""S1 + S2: linked papers in a backtest hide today's citation counts and later papers. Offline."""

from __future__ import annotations

from datetime import date

from sleeper.backtest.papers import AsOfPapers
from sleeper.backtest.prompt import AS_OF_BLOCK
from sleeper.contracts.tools import LinkedPaper


class Inner:
    def __init__(self, papers):
        self.papers, self.calls = papers, []

    def lookup(self, ids):
        self.calls.append(ids)
        return self.papers


def _paper(i, published, cites=120):
    return LinkedPaper(arxiv_id=i, url=f"https://arxiv.org/abs/{i}", found=True, title=f"Paper {i}",
                       published=published, citation_count=cites, citations_as_of="2026-10-24T00:00:00Z")


def test_citation_counts_hidden_and_later_papers_dropped():
    inner = Inner([_paper("2301.00001", "2023-01-02"), _paper("2609.00001", "2026-09-01"), _paper("2607.00001", "2026-08-01")])
    out = AsOfPapers(date(2026, 8, 1), inner).lookup(["2301.00001", "2609.00001", "2607.00001"])
    assert [p.arxiv_id for p in out] == ["2301.00001", "2607.00001"]  # published on the date is kept
    assert all(p.citation_count is None and p.citations_as_of is None for p in out)
    assert out[0].title == "Paper 2301.00001" and out[0].published == "2023-01-02"


def test_not_found_papers_pass_through():
    missing = LinkedPaper(arxiv_id="9999.99999", url="https://arxiv.org/abs/9999.99999", found=False)
    assert AsOfPapers(date(2026, 8, 1), Inner([missing])).lookup(["9999.99999"]) == [missing]


def test_prompt_says_citation_counts_are_unknown():
    assert "never a citation count" in AS_OF_BLOCK
