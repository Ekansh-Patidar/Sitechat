"""Keyword search over a tenant's chunks using BM25.

BM25 is a classic ranking function: it scores a document by how often the
query's words appear in it, weighted so rare words count for more and long
documents are not unfairly favoured. No embeddings, no API call.

It exists alongside vector search because the two fail differently. Vector
search misses exact strings like "RYT 200"; BM25 misses anything phrased
differently from the source text.

Scale note: the index is built in memory from the tenant's chunks and
cached. That is fine into the low thousands of chunks. Beyond that this
would move to a dedicated search service such as OpenSearch, or Pinecone's
own full-text search.
"""

import logging
import re
import time

from rank_bm25 import BM25Okapi

from app.documents.models import Chunk
from app.documents.store import ChunkStore
from app.retrieval.vector_store import SearchResult

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300

# tenant_id -> (bm25 index, chunks, tokenized corpus, time built)
_cache: dict[str, tuple[BM25Okapi, list[Chunk], list[list[str]], float]] = {}


def tokenize(text: str) -> list[str]:
    """Lowercase and split on anything that is not a letter or digit."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _get_index(
    tenant_id: str, chunk_store: ChunkStore
) -> tuple[BM25Okapi, list[Chunk], list[list[str]]] | None:
    now = time.monotonic()

    entry = _cache.get(tenant_id)
    if entry and (now - entry[3]) < CACHE_TTL_SECONDS:
        return entry[0], entry[1], entry[2]

    chunks = chunk_store.get_all_chunks(tenant_id)
    if not chunks:
        return None

    # Indexed on searchable text, so the heading path is matchable too.
    corpus = [tokenize(c.to_searchable_text()) for c in chunks]
    index = BM25Okapi(corpus)

    _cache[tenant_id] = (index, chunks, corpus, now)
    logger.info("Built BM25 index for %s (%d chunks)", tenant_id, len(chunks))
    return index, chunks, corpus


def keyword_search(
    tenant_id: str, query: str, chunk_store: ChunkStore, top_k: int
) -> list[SearchResult]:
    built = _get_index(tenant_id, chunk_store)
    if built is None:
        return []

    index, chunks, corpus = built
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    scores = index.get_scores(query_tokens)
    query_set = set(query_tokens)

    # Keep only chunks that actually share a word with the query, then rank
    # those by BM25.
    #
    # Filtering on token overlap rather than on score matters: BM25 weights a
    # word by how rare it is, and with a small corpus that weight collapses to
    # zero for a word appearing in roughly half the chunks. A score filter
    # would then discard correct matches.
    candidates = [
        (float(score), chunk)
        for score, chunk, tokens in zip(scores, chunks, corpus)
        if query_set & set(tokens)
    ]

    candidates.sort(key=lambda pair: pair[0], reverse=True)

    return [
        SearchResult(
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            document_title=chunk.document_title,
            heading_path=chunk.heading_path,
            score=score,
        )
        for score, chunk in candidates[:top_k]
    ]