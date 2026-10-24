"""Fact check (T13, F6.1): flag any number on a card that doesn't match the tool outputs it cites.

Three checks per card:
- key figures: the value at `source_path` in the cited output (T09's tolerance, so the two agree);
- criterion scores: the cited output's score for that criterion (within 0.5, as in T09);
- numbers written in the pitch, claims and risks: each must appear in the outputs that line cites,
  allowing display rounding ("1.2k" for 1,234, "85%" for 0.85, "2.4" for 2.43).

Numbers that aren't facts are skipped: time windows ("in the last 90 days"), years, dates, versions,
call IDs and numbers inside names. Flags never fail a run; they are shown next to the number (T16).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from sleeper.contracts import CRITERIA, BrowseOutput, Card, ErrorOutput, FactCheckFlag, SubmitOutput, ToolOutput

SCORE_ABS_TOL = 0.5  # T09: models round 81.2 to 81


def figure_matches(shown: float, source: float) -> bool:
    """T09's key figure tolerance: |shown - source| <= max(1% of source, 0.051)."""
    return abs(shown - source) <= max(0.01 * abs(source), 0.051) + 1e-9


def resolve_pointer(doc: object, pointer: str) -> object:
    """RFC 6901 lookup. Raises KeyError if the pointer doesn't resolve."""
    for raw in pointer.split("/")[1:] if pointer else []:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(doc, list) and part.isdigit() and int(part) < len(doc):
            doc = doc[int(part)]
        elif isinstance(doc, dict) and part in doc:
            doc = doc[part]
        else:
            raise KeyError(pointer)
    return doc


# ---- Numbers written in text --------------------------------------------------------------

# A number that stands alone: not glued to letters, dashes, slashes or dots (so not "v0.4", "gpt-5.6",
# "c_003", "2026-10-01" or "/30d"), with an optional k/M suffix and an optional percent sign.
_NUMBER = re.compile(
    r"(?<![\w.\-/#])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<mult>[kKmM])(?![A-Za-z]))?(?P<pct>%)?(?![\w\-/]|[.,]\d)"
)
_WINDOW_BEFORE = re.compile(r"\b(?:last|past|in|per|over|within|every|each|next|first)\s+(?:the\s+)?$", re.IGNORECASE)
_WINDOW_AFTER = re.compile(r"^\s*(?:days?|d|weeks?|months?|years?|hours?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class TextNumber:
    shown: str  # as written, e.g. "1.2k" or "85%"
    value: float
    half_unit: float  # display rounding: half of the last shown digit, e.g. 0.05 for "2.4", 50 for "1.2k"
    percent: bool


def numbers_in(text: str) -> list[TextNumber]:
    """The factual numbers in `text`, skipping time windows and years."""
    found = []
    for m in _NUMBER.finditer(text):
        digits = m["num"].replace(",", "")
        value = float(digits)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        mult = {"k": 1e3, "m": 1e6}.get((m["mult"] or "").lower(), 1.0)
        if not m["mult"] and not m["pct"] and decimals == 0:
            if 1990 <= value <= 2100:
                continue  # a year
            if _WINDOW_BEFORE.search(text[: m.start()]) and _WINDOW_AFTER.match(text[m.end() :]):
                continue  # "in the last 90 days" is the window, not a fact
        found.append(
            TextNumber(
                shown=m.group(0).replace(" ", ""),
                value=value * mult,
                half_unit=0.5 * 10.0**-decimals * mult,
                percent=bool(m["pct"]),
            )
        )
    return found


def _numbers(doc: object) -> Iterator[float]:
    if isinstance(doc, bool) or doc is None:
        return
    if isinstance(doc, int | float):
        if math.isfinite(doc):
            yield float(doc)
    elif isinstance(doc, dict):
        for v in doc.values():
            yield from _numbers(v)
    elif isinstance(doc, list):
        for v in doc:
            yield from _numbers(v)


def text_number_matches(n: TextNumber, source: float) -> bool:
    candidates = [source, source * 100] if n.percent else [source]
    return any(abs(n.value - c) <= max(n.half_unit, 0.01 * abs(c)) + 1e-9 for c in candidates)


# ---- Card ---------------------------------------------------------------------------------


def _citable(outputs: Mapping[str, ToolOutput], call_id: str) -> dict | None:
    out = outputs.get(call_id)
    if out is None or isinstance(out, ErrorOutput | SubmitOutput):
        return None
    return out.model_dump(mode="json")


def _score_in(output: ToolOutput, repo: str, criterion: str) -> float | None:
    if isinstance(output, BrowseOutput):
        row = next((r for r in output.rows if r.repo.lower() == repo.lower()), None)
        return getattr(row.scores, criterion) if row else None
    scores = getattr(output, "scores", None)
    return getattr(scores, criterion) if scores is not None else None


def check_card(card: Card, outputs: Mapping[str, ToolOutput]) -> list[FactCheckFlag]:
    """Every number on `card` that its cited outputs don't back up. `outputs` maps call_id -> output."""
    flags: list[FactCheckFlag] = []

    for name in CRITERIA:
        rating = getattr(card.criteria, name)
        path = f"criteria.{name}"
        score_out = outputs.get(rating.score_call_id)
        found = _score_in(score_out, card.repo, name) if score_out is not None else None
        if found is None:
            flags.append(FactCheckFlag(
                card_path=f"{path}.score", shown=f"{rating.score:g}", source_call_id=rating.score_call_id,
                message=f"no {name} score for {card.repo} in {rating.score_call_id}",
            ))
        elif abs(rating.score - found) > SCORE_ABS_TOL:
            flags.append(FactCheckFlag(
                card_path=f"{path}.score", shown=f"{rating.score:g}", source_call_id=rating.score_call_id,
                found=f"{found:g}", message=f"score {rating.score:g} does not match {found:g} in {rating.score_call_id}",
            ))

        kf = rating.key_figure
        doc = _citable(outputs, kf.source_call_id)
        try:
            value = resolve_pointer(doc, kf.source_path) if doc is not None else None
        except KeyError:
            value = None
        if not isinstance(value, int | float) or isinstance(value, bool):
            flags.append(FactCheckFlag(
                card_path=f"{path}.key_figure.value", shown=f"{kf.value:g}", source_call_id=kf.source_call_id,
                source_path=kf.source_path, message=f"no number at {kf.source_path} in {kf.source_call_id}",
            ))
        elif not figure_matches(kf.value, float(value)):
            flags.append(FactCheckFlag(
                card_path=f"{path}.key_figure.value", shown=f"{kf.value:g}", source_call_id=kf.source_call_id,
                source_path=kf.source_path, found=f"{value:g}",
                message=f"{kf.value:g} does not match {value:g} at {kf.source_path} in {kf.source_call_id}",
            ))

    all_cited = sorted(card.cited_call_ids())
    texts: list[tuple[str, str, list[str]]] = [("pitch", card.pitch, all_cited)]
    texts += [(f"case[{i}].text", c.text, c.source_call_ids) for i, c in enumerate(card.case)]
    texts += [(f"risks[{i}].text", r.text, r.source_call_ids or all_cited) for i, r in enumerate(card.risks)]
    for card_path, text, cited in texts:
        docs = [d for cid in cited if (d := _citable(outputs, cid)) is not None]
        pool = [x for d in docs for x in _numbers(d)]
        for n in numbers_in(text):
            if not any(text_number_matches(n, x) for x in pool):
                where = ", ".join(cited) if cited else "no cited output"
                flags.append(FactCheckFlag(
                    card_path=card_path, shown=n.shown, source_call_id=cited[0] if len(cited) == 1 else None,
                    message=f"{n.shown} is not in the cited output{'s' if len(cited) > 1 else ''} ({where})",
                ))
    return flags
