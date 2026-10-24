"""Topic relevance check for universe candidates (T18).

GitHub searches are broad (every Rust repo tagged ``cli``, every repo that says
"local-first"), so the builder asks a cheap model whether each candidate belongs
to the topic before fetching its metrics. Use::

    checker = RelevanceChecker.for_topic(topic.id, topic.label, topic.scope)
    v = checker.check(gh, search_item)   # Verdict("on" | "off" | "unsure", reason, ...)

- Input: repo name, description, GitHub topics (from the search item) and the
  first README_CHARS characters of the README, fetched with the same request as
  ``metrics._readme_chars`` (so it shares the GitHub cache).
- Output: JSON ``{"verdict": "on"|"off"|"unsure", "reason": "<one line>"}``.
  Malformed output counts as ``unsure``. The builder keeps only ``on``.
- Verdicts are saved in ``data/relevance/<topic>.json`` (committed) and reused
  when ``repo_id``, ``model`` and ``prompt_sha256`` match, so a rebuild makes no
  model calls and works offline.
- Cost controls: a cap per build (CostCapExceeded; default $0.50, lower it with
  SLEEPER_RELEVANCE_CAP_USD), and before the first model
  call the D10 spend guard: refuse if spent + cap > SPEND_GUARD_USD.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from sleeper.agent.spend import SPEND_GUARD_USD, SpendGuardError, current_spend
from sleeper.agent.types import ChatModel, ModelError
from sleeper.config import Settings, load_settings
from sleeper.github import RAW_ACCEPT, GitHubClient, GitHubError, NotFound

RELEVANCE_DIR = Path("data") / "relevance"
README_CHARS = 1500
DESCRIPTION_CHARS = 500
DEFAULT_CAP_USD = 0.50
VERDICTS = ("on", "off", "unsure")

SYSTEM_PROMPT = """You decide whether a GitHub repository belongs to a topic, for a curated list of projects in that topic.

Topic: {label}
{scope}

Judge from the repository's name, description, GitHub topics and the start of its README. Decide by \
the project's primary purpose and users; a matching tag, language or keyword alone is not enough. \
Answer "on" if it clearly belongs, "off" if it clearly does not, and "unsure" if the information is \
too thin to tell.

Reply with only a JSON object, no other text:
{{"verdict": "on" | "off" | "unsure", "reason": "<one short sentence: what the project is and why>"}}"""

USER_PROMPT = """Repository: {name}
Description: {description}
GitHub topics: {topics}
README (first {n} characters):
{readme}"""


class RelevanceError(RuntimeError):
    """The relevance check could not run (no model, cost cap)."""


class CostCapExceeded(RelevanceError):
    """This build's relevance checks reached the cost cap."""


def prompt_sha256(label: str, scope: str) -> str:
    text = SYSTEM_PROMPT.format(label=label, scope=scope.strip()) + "\n---\n" + USER_PROMPT
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Verdict:
    repo_id: int
    repo: str
    verdict: str  # on | off | unsure
    reason: str
    model: str
    prompt_sha256: str
    cost_usd: float


def parse_verdict(text: str | None) -> tuple[str, str]:
    """(verdict, reason) from the model's reply. Anything malformed is ("unsure", why)."""
    if not text:
        return "unsure", "unparseable reply: empty"
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except (ValueError, TypeError):
        return "unsure", f"unparseable reply: {text.strip()[:80]!r}"
    if not isinstance(data, dict):
        return "unsure", f"unparseable reply: {text.strip()[:80]!r}"
    verdict = str(data.get("verdict", "")).strip().lower()
    reason = " ".join(str(data.get("reason") or "").split())[:300]
    if verdict not in VERDICTS:
        return "unsure", f"unparseable verdict {verdict!r}" + (f": {reason}" if reason else "")
    return verdict, reason or "(no reason given)"


def _readme_excerpt(gh: GitHubClient, repo: str) -> str:
    """First README_CHARS of the README: the same request as metrics._readme_chars (shared cache)."""
    try:
        text = gh.get(f"/repos/{repo}/readme", accept=RAW_ACCEPT)
    except NotFound:
        return "(no README)"
    except GitHubError:
        return "(README could not be fetched)"
    return text[:README_CHARS] if isinstance(text, str) and text.strip() else "(empty README)"


@dataclass
class RelevanceStats:
    checked: int = 0  # verdicts used in this build (cached + new)
    new: int = 0  # model calls in this build
    cost_usd: float = 0.0  # recorded cost of the verdicts used in this build
    new_cost_usd: float = 0.0  # cost of this build's model calls (what the cap limits)
    counts: dict[str, int] = field(default_factory=lambda: {v: 0 for v in VERDICTS})


class RelevanceChecker:
    """Classifies candidates against a topic scope, with a committed verdict file as the cache."""

    def __init__(
        self,
        topic_id: str,
        label: str,
        scope: str,
        *,
        model: ChatModel | Callable[[], ChatModel],
        model_id: str | None = None,
        store_path: str | Path | None = None,
        cap_usd: float = DEFAULT_CAP_USD,
        cfg: Settings | None = None,
        spent_usd: Callable[[], float] | None = None,
        guard_usd: float = SPEND_GUARD_USD,
    ) -> None:
        self.topic_id, self.label, self.scope = topic_id, label, scope.strip()
        self._model = model if hasattr(model, "complete") else None
        self._model_factory = None if self._model is not None else model
        self.model_id = model_id or getattr(model, "model_id", None)
        if not self.model_id:
            raise ValueError("model_id is required when the model is created lazily")
        self.store_path = Path(store_path) if store_path else RELEVANCE_DIR / f"{topic_id}.json"
        self.cap_usd, self.guard_usd = cap_usd, guard_usd
        self._cfg = cfg
        self._spent_usd = spent_usd
        self._guard_checked = False
        self.sha = prompt_sha256(label, self.scope)
        self.system = SYSTEM_PROMPT.format(label=label, scope=self.scope)
        self.stats = RelevanceStats()
        self._verdicts: dict[int, Verdict] = self._load()

    @classmethod
    def for_topic(cls, topic_id: str, label: str, scope: str, *, cfg: Settings | None = None, **kw: Any) -> "RelevanceChecker":
        """The default checker: the dev model (luna) over OpenRouter, created only when a model call is needed."""
        cfg = cfg or load_settings()
        if "cap_usd" not in kw and os.environ.get("SLEEPER_RELEVANCE_CAP_USD"):
            kw["cap_usd"] = float(os.environ["SLEEPER_RELEVANCE_CAP_USD"])  # lower the per-build cap

        def make() -> ChatModel:
            from sleeper.agent.openrouter import OpenRouterModel

            try:
                return OpenRouterModel(cfg.dev_model, cfg.openrouter_api_key)
            except ModelError as e:
                raise RelevanceError(f"relevance check for {topic_id!r} needs a model: {e}") from e

        return cls(topic_id, label, scope, model=make, model_id=cfg.dev_model, cfg=cfg, **kw)

    # ---- verdict file ---------------------------------------------------------

    def _load(self) -> dict[int, Verdict]:
        if not self.store_path.exists():
            return {}
        data = json.loads(self.store_path.read_text(encoding="utf-8"))
        out = {}
        for v in data.get("verdicts", []):
            try:
                out[int(v["repo_id"])] = Verdict(**{k: v[k] for k in Verdict.__dataclass_fields__})
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def save(self) -> Path:
        """Write the verdict file: only verdicts for the current model and prompt, sorted by repo."""
        current = sorted(
            (v for v in self._verdicts.values() if v.model == self.model_id and v.prompt_sha256 == self.sha),
            key=lambda v: (v.repo.lower(), v.repo_id),
        )
        counts = {k: sum(v.verdict == k for v in current) for k in VERDICTS}
        data = {
            "topic": self.topic_id,
            "label": self.label,
            "scope": self.scope,
            "model": self.model_id,
            "prompt_sha256": self.sha,
            "readme_chars": README_CHARS,
            "totals": {"checked": len(current), **counts, "cost_usd": round(sum(v.cost_usd for v in current), 6)},
            "verdicts": [asdict(v) for v in current],
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.store_path)
        return self.store_path

    # ---- checking -------------------------------------------------------------

    def cached(self, repo_id: int) -> Verdict | None:
        v = self._verdicts.get(repo_id)
        return v if v and v.model == self.model_id and v.prompt_sha256 == self.sha else None

    def _check_guard(self) -> None:
        if self._guard_checked:
            return
        if self._spent_usd is not None:
            spent = self._spent_usd()
        else:
            spent = current_spend(self._cfg or load_settings())
        if spent + self.cap_usd > self.guard_usd:
            raise SpendGuardError(
                f"spend guard: ${spent:.2f} spent + up to ${self.cap_usd:.2f} for relevance checks "
                f"would exceed ${self.guard_usd:.0f} (D10)"
            )
        self._guard_checked = True

    def _get_model(self) -> ChatModel:
        if self._model is None:
            self._model = self._model_factory()  # type: ignore[misc]
        return self._model

    def check(self, gh: GitHubClient, item: dict) -> Verdict:
        """Verdict for one search item (needs id, full_name; uses description and topics)."""
        repo_id, repo = int(item["id"]), item["full_name"]
        v = self.cached(repo_id)
        if v is None:
            if self.stats.new_cost_usd >= self.cap_usd:
                raise CostCapExceeded(
                    f"relevance checks for {self.topic_id!r} reached the ${self.cap_usd:.2f} cap after "
                    f"{self.stats.new} model calls; verdicts so far are saved in {self.store_path}"
                )
            self._check_guard()
            readme = _readme_excerpt(gh, repo)
            desc = " ".join((item.get("description") or "").split())[:DESCRIPTION_CHARS] or "(none)"
            user = USER_PROMPT.format(
                name=repo, description=desc, topics=", ".join(item.get("topics") or []) or "(none)",
                n=README_CHARS, readme=readme,
            )
            messages = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
            try:
                reply = self._get_model().complete(messages, tools=[], timeout_s=60.0)
            except ModelError as e:
                # Not saved: a failed call is retried on the next build. Dropped from this one.
                self.stats.checked += 1
                self.stats.counts["unsure"] += 1
                return Verdict(repo_id, repo, "unsure", f"model error: {e}", self.model_id, self.sha, 0.0)
            verdict, reason = parse_verdict(reply.content)
            cost = round(reply.usage.cost_usd, 8)
            v = Verdict(repo_id, repo, verdict, reason, self.model_id, self.sha, cost)
            self._verdicts[repo_id] = v
            self.stats.new += 1
            self.stats.new_cost_usd += cost
            self.save()
        self.stats.checked += 1
        self.stats.cost_usd += v.cost_usd
        self.stats.counts[v.verdict] += 1
        return v

    def summary(self) -> str:
        """One line for header.builder, e.g. 'relevance openai/gpt-5.6-luna 412 checked, 37 off, $0.08'."""
        s = self.stats
        dropped = s.counts["off"] + s.counts["unsure"]
        return (
            f"relevance {self.model_id} {s.checked} checked, {dropped} off-topic, ${s.cost_usd:.2f}"
            f" (prompt {self.sha[:8]})"
        )
