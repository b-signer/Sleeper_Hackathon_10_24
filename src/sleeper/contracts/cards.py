"""Pick cards (F4) and the submission the agent sends to the submit tool (F2.3, F3.4)."""

from __future__ import annotations

from pydantic import Field, model_validator

from sleeper.contracts.common import CallId, Contract, JsonPointer, Repo, Score


class KeyFigure(Contract):
    """One number on a card and exactly where it came from (C1)."""

    label: str = Field(min_length=1, description="e.g. 'Stars gained, last 30 days'")
    value: float
    unit: str | None = Field(None, description="e.g. 'stars', 'commits', 'days'")
    source_call_id: CallId
    source_path: JsonPointer = Field(description="Pointer into the cited output, e.g. '/metrics/stars_gained_30d'")


class CriterionRating(Contract):
    score: Score = Field(description="Copied from the cited output's scores, never invented")
    score_call_id: CallId = Field(description="Tool output that shows this score")
    key_figure: KeyFigure = Field(description="The number behind the score, e.g. stars for popularity")


class CardCriteria(Contract):
    popularity: CriterionRating
    momentum: CriterionRating
    quality: CriterionRating
    newness: CriterionRating


class Claim(Contract):
    text: str = Field(min_length=1, max_length=400)
    source_call_ids: list[CallId] = Field(min_length=1)


class Risk(Contract):
    text: str = Field(min_length=1, max_length=400)
    source_call_ids: list[CallId] = Field(default_factory=list, description="Required if the risk states a fact")


class Card(Contract):
    repo: Repo
    pitch: str = Field(min_length=1, max_length=200, description="One line")
    criteria: CardCriteria
    case: list[Claim] = Field(min_length=1, max_length=4)
    risks: list[Risk] = Field(default_factory=list, max_length=3)
    proven_wrong_if: list[str] = Field(min_length=1, max_length=2, description="Observable signals to watch")
    confidence: float = Field(ge=0, le=1)

    def cited_call_ids(self) -> set[str]:
        """Every call ID this card cites (for submit checks, skeptic and fact check)."""
        ids: set[str] = set()
        for name in ("popularity", "momentum", "quality", "newness"):
            r: CriterionRating = getattr(self.criteria, name)
            ids |= {r.score_call_id, r.key_figure.source_call_id}
        for item in [*self.case, *self.risks]:
            ids |= set(item.source_call_ids)
        return ids


class RankingEntry(Contract):
    rank: int = Field(ge=1)
    repo: Repo
    note: str | None = Field(None, max_length=200)


class Submission(Contract):
    """Arguments of the submit tool. Context checks (universe, citations, max_picks) are done by T09."""

    cards: list[Card] = Field(min_length=1, max_length=5)
    ranking: list[RankingEntry] = Field(min_length=1, description="Every repo looked at closely, best first")
    summary: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def _check(self) -> Submission:
        ranks = sorted(e.rank for e in self.ranking)
        if ranks != list(range(1, len(self.ranking) + 1)):
            raise ValueError("ranking ranks must be exactly 1..n")
        ranked = [e.repo.lower() for e in self.ranking]
        if len(ranked) != len(set(ranked)):
            raise ValueError("duplicate repo in ranking")
        carded = [c.repo.lower() for c in self.cards]
        if len(carded) != len(set(carded)):
            raise ValueError("duplicate repo in cards")
        missing = [c.repo for c in self.cards if c.repo.lower() not in set(ranked)]
        if missing:
            raise ValueError(f"card repos missing from ranking: {missing}")
        return self
