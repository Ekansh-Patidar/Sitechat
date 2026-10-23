"""Tests for the retrieval pipeline and the retrieval triage.

embed_query, keyword_search and rerank are patched, so these check only
what retrieve decides: which stages run, with how many candidates, and
how much comes back.
"""

from unittest.mock import patch

import pytest

from app.config import get_settings
from app.evaluation_config import RetrievalConfig
from app.retrieval.pipeline import retrieve
from app.retrieval.triage import needs_retrieval
from app.retrieval.vector_store import SearchResult


def result(chunk_id: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        text=f"text for {chunk_id}",
        document_title="Doc",
        heading_path=["Doc"],
        score=1.0,
    )


class FakeVectorStore:
    """Returns chunks v0, v1, ... and records how many were asked for."""

    def __init__(self):
        self.calls = []

    def search(self, tenant_id, query_vector, top_k):
        self.calls.append((tenant_id, top_k))
        return [result(f"v{i}") for i in range(top_k)]


@pytest.fixture(autouse=True)
def no_embedding():
    with patch("app.retrieval.pipeline.embed_query", return_value=[0.0]):
        yield


def ids(results):
    return [r.chunk_id for r in results]


def test_production_default_is_vector_only_from_settings():
    store = FakeVectorStore()
    settings = get_settings()

    with patch("app.retrieval.pipeline.keyword_search") as kw, patch(
        "app.retrieval.pipeline.rerank"
    ) as rr:
        final = retrieve("t1", "q", store, chunk_store=object())

    assert store.calls == [("t1", settings.retrieval_candidates)]
    kw.assert_not_called()
    rr.assert_not_called()
    assert len(final) == min(settings.retrieval_top_k, settings.retrieval_candidates)


def test_config_candidates_reach_the_vector_search():
    store = FakeVectorStore()
    retrieve(
        "t1", "q", store,
        config=RetrievalConfig("x", use_keyword=False, use_rerank=False, candidates=20),
    )
    assert store.calls == [("t1", 20)]


def test_vector_only_keeps_vector_order_and_cuts_to_top_k():
    final = retrieve(
        "t1", "q", FakeVectorStore(),
        config=RetrievalConfig("x", use_keyword=False, use_rerank=False),
    )
    k = get_settings().retrieval_top_k
    assert ids(final) == [f"v{i}" for i in range(k)]


def test_keyword_results_are_fused_with_the_config_weight():
    config = RetrievalConfig(
        "x", use_keyword=True, use_rerank=False, keyword_weight=0.3, candidates=4
    )
    with patch(
        "app.retrieval.pipeline.keyword_search", return_value=[result("k0")]
    ) as kw, patch(
        "app.retrieval.pipeline.reciprocal_rank_fusion", return_value=[result("f0")]
    ) as fuse:
        final = retrieve("t1", "q", FakeVectorStore(), chunk_store=object(), config=config)

    kw.assert_called_once()
    assert kw.call_args[0][3] == 4
    assert fuse.call_args.kwargs["weights"] == [get_settings().vector_weight, 0.3]
    assert ids(final) == ["f0"]


def test_keyword_search_needs_a_chunk_store():
    config = RetrievalConfig("x", use_keyword=True, use_rerank=False)
    with patch("app.retrieval.pipeline.keyword_search") as kw:
        retrieve("t1", "q", FakeVectorStore(), chunk_store=None, config=config)
    kw.assert_not_called()


def test_rerank_gets_the_candidates_and_decides_the_final_list():
    config = RetrievalConfig("x", use_keyword=False, use_rerank=True, candidates=6)
    with patch(
        "app.retrieval.pipeline.rerank", return_value=[result("v5")]
    ) as rr:
        final = retrieve("t1", "q", FakeVectorStore(), config=config)

    query, candidates, top_k = rr.call_args[0]
    assert query == "q"
    assert len(candidates) == 6
    assert top_k == get_settings().retrieval_top_k
    assert ids(final) == ["v5"]


# --- triage ----------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["hi", "Hello!", "thanks", "Thank you.", "ok", "  bye  ", "good morning", ""],
)
def test_conversational_messages_skip_retrieval(message):
    assert not needs_retrieval(message)


@pytest.mark.parametrize(
    "message",
    [
        "fees?",
        "what are the dates",
        "thanks, but what about the fees?",
        "hi, do you have accommodation?",
        "ok so how long is the course",
    ],
)
def test_questions_go_to_retrieval(message):
    assert needs_retrieval(message)


# --- context message -------------------------------------------------------


def test_context_message_has_no_citation_markers():
    from app.agent.prompts import build_context_message

    message = build_context_message([result("a"), result("b")])
    assert "text for a" in message and "text for b" in message
    assert "[1]" not in message
    assert "cite" not in message.lower()


def test_no_results_means_no_context_message():
    from app.agent.prompts import build_context_message

    assert build_context_message([]) is None


def test_context_names_the_programme_of_each_section():
    """Two "Fees" sections must be told apart by their programme."""
    from app.agent.prompts import build_context_message

    seven = SearchResult(
        chunk_id="w__40", text="Double shared US $299.", document_title="Site",
        heading_path=["Site", "7-Day Yoga Retreat (Rishikesh)", "Fees"], score=1.0,
    )
    fourteen = SearchResult(
        chunk_id="w__52", text="Double shared US $599.", document_title="Site",
        heading_path=["Site", "14-Day Yoga Retreat (Rishikesh)", "Fees"], score=1.0,
    )
    message = build_context_message([seven, fourteen])

    assert "7-Day Yoga Retreat (Rishikesh) > Fees\nDouble shared US $299." in message
    assert "14-Day Yoga Retreat (Rishikesh) > Fees\nDouble shared US $599." in message


# --- programme focus ---------------------------------------------------------


def _r(chunk_id, score, programme=""):
    return SearchResult(chunk_id=chunk_id, text="", document_title="",
                        heading_path=[], score=score, programme=programme)


class ScoredVectorStore:
    """Fixed results with scores; programmes filters like Pinecone does."""

    def __init__(self, results):
        self.results = sorted(results, key=lambda r: r.score, reverse=True)

    def search(self, tenant_id, query_vector, top_k, programmes=None):
        hits = [r for r in self.results if programmes is None or r.programme in programmes]
        return hits[:top_k]


def blended(results, programmes, boost=0.05, top_k=5):
    from app.retrieval.pipeline import blend

    store = ScoredVectorStore(results)
    focused = [r for p in programmes for r in store.search("t", [], 3, [p])]
    return ids(blend(focused, store.search("t", [], 20), top_k, programmes, boost))


def test_a_vague_follow_up_prefers_the_focused_programme():
    """"How much does it cost?" scores about the same everywhere."""
    results = [_r("300-fees", 0.40, "300"), _r("300-dates", 0.39, "300"),
               _r("100-fees", 0.42, "100"), _r("r7-fees", 0.41, "r7"),
               _r("booking", 0.40), _r("200-fees", 0.41, "200")]
    assert blended(results, ["300"])[:2] == ["300-fees", "300-dates"]


def test_a_school_wide_question_during_a_focus_gets_the_school_section():
    """From a real chat: "how many halls?" with the 300-hour in focus."""
    results = [_r("halls", 0.55), _r("rooms", 0.47),
               _r("300-schedule", 0.38, "300"), _r("300-curriculum", 0.37, "300"),
               _r("300-fees", 0.36, "300"), _r("havelock-rooms", 0.45, "havelock")]
    final = blended(results, ["300"])
    # The school sections outrank the focused programme's; with fixed slots
    # three 300-hour sections came first and "halls" did not fit.
    assert final[:3] == ["halls", "rooms", "havelock-rooms"]


def test_a_comparison_keeps_two_sections_of_each_side():
    results = [_r("general", 0.60), _r("other", 0.58, "x"),
               _r("a1", 0.40, "a"), _r("a2", 0.39, "a"),
               _r("b1", 0.30, "b"), _r("b2", 0.29, "b")]
    final = blended(results, ["a", "b"])
    assert {"a1", "a2", "b1", "b2"} <= set(final)


def test_blend_returns_each_section_once():
    results = [_r("a1", 0.5, "a"), _r("g1", 0.4)]
    assert blended(results, ["a"]) == ["a1", "g1"]


def test_no_focus_is_the_plain_search():
    store = ScoredVectorStore([_r("g1", 0.5), _r("a1", 0.4, "a")])
    calls = []
    original = store.search

    def search(tenant_id, query_vector, top_k, programmes=None):
        calls.append(programmes)
        return original(tenant_id, query_vector, top_k, programmes)

    store.search = search
    retrieve("t1", "q", store)
    assert calls == [None]


def test_overview_is_added_without_pushing_better_matches_down():
    from app.programmes.models import OVERVIEW

    store = ScoredVectorStore([_r("g1", 0.6), _r("g2", 0.5), _r("table", 0.2, OVERVIEW)])
    final = ids(retrieve("t1", "q", store, overview=True))
    assert final[:2] == ["g1", "g2"]
    assert final.count("table") == 1
