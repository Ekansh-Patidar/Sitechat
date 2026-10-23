"""Tests for fusion and keyword search.

Fusion is pure logic and fully testable. Keyword search runs against an
in-memory chunk store, so no network is needed.
"""

from app.documents.models import Chunk, Document
from app.documents.store import InMemoryChunkStore
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.keyword_search import keyword_search
import app.retrieval.keyword_search as ks
from app.retrieval.vector_store import SearchResult


def result(chunk_id: str, score: float = 1.0) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        text=f"text for {chunk_id}",
        document_title="Doc",
        heading_path=["Doc", "Section"],
        score=score,
    )


def test_fusion_prefers_chunks_ranked_by_both():
    """A chunk found by both retrievers should beat one found by only one."""
    vector = [result("a"), result("b"), result("c")]
    keyword = [result("c"), result("d"), result("e")]

    merged = reciprocal_rank_fusion([vector, keyword], top_k=5)

    assert merged[0].chunk_id == "c"


def test_fusion_deduplicates():
    merged = reciprocal_rank_fusion(
        [[result("a"), result("b")], [result("a")]], top_k=10
    )
    ids = [r.chunk_id for r in merged]
    assert len(ids) == len(set(ids))


def test_fusion_handles_an_empty_list():
    """Keyword search returning nothing must not break the merge."""
    merged = reciprocal_rank_fusion([[result("a")], []], top_k=5)
    assert len(merged) == 1


def test_fusion_respects_top_k():
    lists = [[result(str(i)) for i in range(10)]]
    assert len(reciprocal_rank_fusion(lists, top_k=3)) == 3


def make_store() -> InMemoryChunkStore:
    store = InMemoryChunkStore()
    chunks = [
        Chunk(
            chunk_id="c0",
            tenant_id="t1",
            document_id="d1",
            document_title="Guide",
            heading_path=["Guide", "Certification"],
            text="Our course is Yoga Alliance registered as RYT 200.",
            position=0,
        ),
        Chunk(
            chunk_id="c1",
            tenant_id="t1",
            document_id="d1",
            document_title="Guide",
            heading_path=["Guide", "Meals"],
            text="Three vegetarian meals are served each day.",
            position=1,
        ),
    ]
    store.save_document(
        Document(
            document_id="d1", tenant_id="t1", title="Guide", source="g.md"
        ),
        chunks,
    )
    return store


def setup_function():
    ks._cache.clear()


def test_keyword_search_finds_an_exact_term():
    """The case vector search is weakest at: a specific code."""
    store = make_store()
    results = keyword_search("t1", "RYT 200", store, top_k=5)

    assert results
    assert results[0].chunk_id == "c0"


def test_keyword_search_matches_the_heading():
    store = make_store()
    results = keyword_search("t1", "certification", store, top_k=5)

    assert results
    assert results[0].chunk_id == "c0"


def test_keyword_search_is_isolated_by_tenant():
    store = make_store()
    assert keyword_search("t2", "RYT 200", store, top_k=5) == []


def test_keyword_search_returns_nothing_for_unrelated_query():
    store = make_store()
    assert keyword_search("t1", "zzzz qqqq", store, top_k=5) == []