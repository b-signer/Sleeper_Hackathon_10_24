"""Tests for linked papers (S2). No network except tests marked live."""

from __future__ import annotations

import json

import httpx
import pytest

from sleeper import fakes
from sleeper.agent.data_tools import DataTools, ReadmeUnavailable
from sleeper.agent.submit_check import SubmitContext, check_submission
from sleeper.contracts import ErrorOutput, PapersArgs, PapersOutput
from sleeper.papers import MAX_PAPERS, PapersClient, PapersUnavailable, find_arxiv_ids, linked_papers, parse_arxiv_feed

ARXIV = {
    "2106.09685": ("LoRA: Low-Rank Adaptation of Large\n      Language Models", "2021-06-17T17:37:18Z", 2),
    "1706.03762": ("Attention Is All You Need", "2017-06-12T17:57:34Z", 7),
    "2305.14314": ("QLoRA: Efficient Finetuning of Quantized LLMs", "2023-05-23T17:50:33Z", 1),
}
CITES = {"2106.09685": 9000, "1706.03762": 150000}  # 2305.14314: not on Semantic Scholar


def atom(ids: list[str]) -> str:
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/{i}v{ARXIV[i][2]}</id><title>{ARXIV[i][0]}</title>"
        f"<published>{ARXIV[i][1]}</published></entry>"
        for i in ids
        if i in ARXIV
    )
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>q</title>{entries}</feed>'


class FakeApis:
    """MockTransport handler for arXiv and Semantic Scholar. ``fail`` holds statuses served first, per host."""

    def __init__(self, fail: dict[str, list[int]] | None = None) -> None:
        self.fail = {host: list(statuses) for host, statuses in (fail or {}).items()}
        self.requests: list[tuple[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "export.arxiv.org":
            ids = request.url.params["id_list"].split(",")
        else:
            ids = [i.removeprefix("ARXIV:") for i in json.loads(request.content)["ids"]]
        self.requests.append((host, ids))
        if self.fail.get(host):
            return httpx.Response(self.fail[host].pop(0), text="slow down")
        if host == "export.arxiv.org":
            return httpx.Response(200, text=atom(ids))
        return httpx.Response(200, json=[{"citationCount": CITES[i]} if i in CITES else None for i in ids])

    def hosts(self) -> list[str]:
        return [host for host, _ in self.requests]


def make_client(tmp_path, handler, **kw) -> PapersClient:
    return PapersClient(
        s2_api_key="", offline=False, cache_dir=tmp_path, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
    )


# ---- find_arxiv_ids ---------------------------------------------------------


def test_find_ids_link_forms():
    text = """
    Paper: https://arxiv.org/abs/2106.09685v2
    PDF: [pdf](https://arxiv.org/pdf/2305.14314.pdf) and https://arxiv.org/html/2401.00001v1
    Cite as arXiv:1706.03762 or https://doi.org/10.48550/arXiv.2010.11929
    """
    assert find_arxiv_ids(text) == ["2106.09685", "2305.14314", "2401.00001", "1706.03762", "2010.11929"]


def test_find_ids_old_style_and_dedupe():
    text = "See arxiv.org/abs/hep-th/9901001, arXiv:math.GT/0309136, and again arxiv.org/pdf/hep-th/9901001v3."
    assert find_arxiv_ids(text) == ["hep-th/9901001", "math.GT/0309136"]


def test_find_ids_ignores_lookalikes():
    text = "version 2106.09685, phone 1234.5678, https://example.com/abs/2106.09685, ARXIV.md"
    assert find_arxiv_ids(text) == []


def test_find_ids_keeps_first_mention_order():
    text = "arXiv:2305.14314 then https://arxiv.org/abs/2106.09685 then arXiv:2305.14314v2"
    assert find_arxiv_ids(text) == ["2305.14314", "2106.09685"]


def test_parse_arxiv_feed_strips_versions_and_wrapping():
    feed = parse_arxiv_feed(atom(["2106.09685", "9999.99999"]))
    assert feed == {"2106.09685": {"title": "LoRA: Low-Rank Adaptation of Large Language Models", "published": "2021-06-17"}}


# ---- PapersClient -----------------------------------------------------------


def test_lookup_combines_sources(tmp_path):
    apis = FakeApis()
    lora, qlora, ghost = make_client(tmp_path, apis).lookup(["2106.09685", "2305.14314", "9999.99999"])
    assert lora.found and lora.title.startswith("LoRA: Low-Rank") and lora.published == "2021-06-17"
    assert lora.citation_count == 9000 and lora.citations_as_of and lora.url == "https://arxiv.org/abs/2106.09685"
    assert qlora.found and qlora.citation_count is None  # on arXiv, not on Semantic Scholar
    assert not ghost.found and ghost.title is None
    # One batch call each; papers arXiv doesn't know aren't sent to Semantic Scholar.
    assert apis.requests == [
        ("export.arxiv.org", ["2106.09685", "2305.14314", "9999.99999"]),
        ("api.semanticscholar.org", ["2106.09685", "2305.14314"]),
    ]


def test_cache_per_paper_and_offline(tmp_path):
    apis = FakeApis()
    client = make_client(tmp_path, apis)
    client.lookup(["2106.09685", "9999.99999"])
    # A second README shares a cached paper and adds a new one: only the new one is fetched.
    client.lookup(["9999.99999", "1706.03762", "2106.09685"])
    assert apis.requests[2:] == [("export.arxiv.org", ["1706.03762"]), ("api.semanticscholar.org", ["1706.03762"])]

    offline = PapersClient(s2_api_key="", offline=True, cache_dir=tmp_path)
    papers = offline.lookup(["2106.09685", "9999.99999", "1706.03762"])
    assert [p.found for p in papers] == [True, False, True]  # unknown IDs are cached too
    assert [p.citation_count for p in papers] == [9000, None, 150000]
    assert offline.network_requests == 0
    with pytest.raises(PapersUnavailable, match="offline cache"):
        offline.lookup(["2305.14314"])


def test_citations_best_effort_then_filled_in(tmp_path):
    apis = FakeApis(fail={"api.semanticscholar.org": [429] * 3})
    client = make_client(tmp_path, apis)
    (lora,) = client.lookup(["2106.09685"])
    assert lora.found and lora.citation_count is None and lora.citations_as_of is None
    # The failure was not cached: the next lookup asks Semantic Scholar again, and arXiv not at all.
    (lora,) = client.lookup(["2106.09685"])
    assert lora.citation_count == 9000
    assert apis.hosts() == ["export.arxiv.org"] + ["api.semanticscholar.org"] * 4


def test_arxiv_retries_then_succeeds(tmp_path):
    apis = FakeApis(fail={"export.arxiv.org": [503]})
    assert make_client(tmp_path, apis).lookup(["2106.09685"])[0].found
    assert apis.hosts() == ["export.arxiv.org", "export.arxiv.org", "api.semanticscholar.org"]


def test_arxiv_failure_raises_and_caches_nothing(tmp_path):
    apis = FakeApis(fail={"export.arxiv.org": [503] * 3})
    with pytest.raises(PapersUnavailable, match="503 after 3 attempts"):
        make_client(tmp_path, apis).lookup(["2106.09685"])
    assert list(tmp_path.glob("*.json")) == []


def test_bad_request_is_not_retried(tmp_path):
    apis = FakeApis(fail={"export.arxiv.org": [400]})
    client = make_client(tmp_path, apis)
    with pytest.raises(PapersUnavailable, match="400"):
        client.lookup(["2106.09685"])
    assert client.network_requests == 1


def test_linked_papers_limit_and_empty(tmp_path):
    apis = FakeApis()
    client = make_client(tmp_path, apis)
    assert linked_papers("no papers here", client) == []
    assert apis.requests == []
    readme = " ".join(f"arXiv:2401.{n:05d}" for n in range(MAX_PAPERS + 5))
    assert len(linked_papers(readme, client)) == MAX_PAPERS


@pytest.mark.live
def test_live_lookup(tmp_path):
    lora, ghost = PapersClient(offline=False, cache_dir=tmp_path).lookup(["2106.09685", "9999.99999"])
    assert lora.found and lora.title.startswith("LoRA") and lora.published == "2021-06-17"
    assert not ghost.found
    # Citation count is best effort (Semantic Scholar's free pool often answers 429).
    assert lora.citation_count is None or lora.citation_count > 1000


# ---- papers tool (DataTools) -----------------------------------------------

PAPER_README = "Based on [LoRA](https://arxiv.org/abs/2106.09685) and arXiv:9999.99999 (a typo)."


def papers_tools(tmp_path, readme=PAPER_README, apis=None) -> tuple[DataTools, FakeApis]:
    apis = apis or FakeApis()

    def source(row):
        if isinstance(readme, Exception):
            raise readme
        return readme

    tools = DataTools(fakes.fake_universe(), readme_source=source, papers_client=make_client(tmp_path, apis))
    return tools, apis


def test_papers_tool_output(tmp_path):
    tools, _ = papers_tools(tmp_path)
    out = tools.papers(PapersArgs(repo="ACME/Quiver"), "c_004")
    assert isinstance(out, PapersOutput) and out.repo == "acme/quiver" and out.total_linked == 2
    assert [(p.arxiv_id, p.found, p.citation_count) for p in out.papers] == [("2106.09685", True, 9000), ("9999.99999", False, None)]


def test_papers_tool_no_links_makes_no_lookup(tmp_path):
    tools, apis = papers_tools(tmp_path, readme="Just a CLI. Version 2106.09685 of nothing.")
    out = tools.papers(PapersArgs(repo="acme/quiver"), "c_004")
    assert isinstance(out, PapersOutput) and out.papers == [] and out.total_linked == 0
    assert apis.requests == []


def test_papers_tool_caps_lookups(tmp_path):
    readme = " ".join(f"arXiv:2401.{n:05d}" for n in range(MAX_PAPERS + 3))
    tools, _ = papers_tools(tmp_path, readme=readme)
    out = tools.papers(PapersArgs(repo="acme/quiver"), "c_004")
    assert len(out.papers) == MAX_PAPERS and out.total_linked == MAX_PAPERS + 3


@pytest.mark.parametrize(
    ("repo", "readme", "apis", "code"),
    [
        ("rust-lang/cargo", PAPER_README, None, "NOT_IN_UNIVERSE"),
        ("acme/quiver", None, None, "NOT_FOUND"),
        ("acme/quiver", ReadmeUnavailable("README not in the offline cache"), None, "UPSTREAM"),
        ("acme/quiver", PAPER_README, FakeApis(fail={"export.arxiv.org": [503] * 3}), "UPSTREAM"),
    ],
)
def test_papers_tool_errors(tmp_path, repo, readme, apis, code):
    tools, _ = papers_tools(tmp_path, readme=readme, apis=apis)
    out = tools.papers(PapersArgs(repo=repo), "c_009")
    assert isinstance(out, ErrorOutput) and out.tool == "papers" and out.error.code == code, out


def test_submit_accepts_key_figure_from_papers_and_checks_repo(tmp_path):
    u = fakes.fake_universe()
    tools, _ = papers_tools(tmp_path)
    details = {r: fakes.repo_details_output(u, r, cid) for r, cid in [("acme/quiver", "c_001"), ("lumen-dev/tidepool", "c_002")]}
    outputs = {"c_001": details["acme/quiver"], "c_002": details["lumen-dev/tidepool"]}
    outputs["c_003"] = tools.papers(PapersArgs(repo="acme/quiver"), "c_003")

    def card(d, paper_cid):
        crit = {
            c: {"score": getattr(d.scores, c), "score_call_id": d.call_id,
                "key_figure": {"label": "stars", "value": d.metrics.stars, "source_call_id": d.call_id, "source_path": "/metrics/stars"}}
            for c in ("popularity", "momentum", "quality", "newness")
        }
        crit["quality"]["key_figure"] = {
            "label": "Citations of the linked paper", "value": 9000, "source_call_id": paper_cid,
            "source_path": "/papers/0/citation_count",
        }
        return {"repo": d.repo, "pitch": "A pick", "criteria": crit, "case": [{"text": "Cited 9,000 times", "source_call_ids": [paper_cid]}],
                "proven_wrong_if": ["No commits"], "confidence": 0.5}

    ranking = [{"rank": 1, "repo": "acme/quiver"}, {"rank": 2, "repo": "lumen-dev/tidepool"}]
    ctx = SubmitContext(universe=u, max_picks=5, outputs=outputs, researched={})
    _, errors = check_submission({"cards": [card(details["acme/quiver"], "c_003")], "ranking": ranking, "summary": "s"}, ctx)
    assert errors == []
    _, errors = check_submission({"cards": [card(details["lumen-dev/tidepool"], "c_003")], "ranking": ranking, "summary": "s"}, ctx)
    assert errors == [
        "cards[0] (lumen-dev/tidepool) criteria.quality.key_figure: c_003 is papers for acme/quiver, not lumen-dev/tidepool; "
        "cite this repo's own number"
    ]
