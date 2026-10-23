"""Re-scores candidates against the query with a dedicated model.

Retrieval is a two-stage problem. The first stage is fast and approximate:
it compares a query vector against millions of chunk vectors, which means
each chunk was embedded without knowing the question. The second stage is
slow and accurate: a cross-encoder reads the query and one chunk together
and scores how well they actually match.

Running the accurate model over everything would be far too slow, so it runs
over the twenty candidates the fast stage produced.

If reranking fails the candidates are returned unchanged. A degraded answer
beats no answer, and a reranker outage should not take the chat down.
"""

import logging
import time

import cohere

from app.config import get_settings
from app.retrieval.vector_store import SearchResult

# Cohere trial keys allow 10 calls per minute. Spacing calls out keeps the
# evaluation honest: without this, failed calls fall back silently and the
# reranked scores are only partly real.
_MIN_SECONDS_BETWEEN_CALLS = 6.5
_last_call_at = 0.0

logger = logging.getLogger(__name__)

_client: cohere.ClientV2 | None = None


def _get_client() -> cohere.ClientV2:
    global _client
    if _client is None:
        _client = cohere.ClientV2(
            api_key=get_settings().cohere_api_key.get_secret_value()
        )
    return _client


def rerank(
    query: str, candidates: list[SearchResult], top_k: int
) -> list[SearchResult]:
    global _last_call_at

    settings = get_settings()

    if not candidates or not settings.cohere_api_key.get_secret_value():
        return candidates[:top_k]

    documents = [c.to_searchable_text_for_rerank() for c in candidates]

    if settings.throttle_rerank:
        wait = _MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()

    try:
        response = _get_client().rerank(
            model=settings.rerank_model,
            query=query,
            documents=documents,
            top_n=min(top_k, len(documents)),
        )
    except Exception:
        logger.exception("Reranking failed, falling back to fusion order")
        return candidates[:top_k]

    reranked = []
    for result in response.results:
        original = candidates[result.index]
        reranked.append(
            SearchResult(
                chunk_id=original.chunk_id,
                text=original.text,
                document_title=original.document_title,
                heading_path=original.heading_path,
                score=result.relevance_score,
            )
        )
    return reranked
