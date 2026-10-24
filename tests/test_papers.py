"""Tests for linked papers (S2). No network except tests marked live."""

from __future__ import annotations

import json

import httpx
import pytest

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
