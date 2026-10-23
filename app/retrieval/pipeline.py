"""The full retrieval pipeline: hybrid search, fusion, reranking."""

import logging

from app.config import get_settings
from app.documents.store import ChunkStore
from app.evaluation_config import RetrievalConfig
from app.retrieval.embeddings import embed_query
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.keyword_search import keyword_search
from app.retrieval.reranker import rerank
from app.programmes.models import OVERVIEW
from app.retrieval.vector_store import SearchResult, VectorStore
import time

logger = logging.getLogger(__name__)

# How many of each focused programme's best sections are always kept: one
# for a single focus, two for each side of a comparison. Everything else is
# ranked by relevance, with the focused programmes' sections boosted by
# settings.focus_boost.
KEEP_ONE = 1
KEEP_EACH = 2
FOCUS_POOL = 3
GENERAL_POOL = 20


def blend(
    focused: list[SearchResult],
    general: list[SearchResult],
    top_k: int,
    programmes: list[str],
    boost: float,
) -> list[SearchResult]:
    """Rank focused and general results together; focus gets a boost.

    Fixed slots for the focus failed both ways in real chats: three slots
    spent on the 300-hour course for "how many halls?", and, when the rest
    could also come from the focus, every slot. Ranking everything by
    relevance, with a small boost for the programme the visitor is asking
    about, lets a school-wide section win when it is the better match. A
    minimum per focused programme keeps follow-ups and both sides of a
    comparison. Returns more than top_k only if the minimums need it.
    """
    best: dict[str, tuple[float, SearchResult]] = {}
    for result in focused + general:
        score = result.score + (boost if result.programme in programmes else 0.0)
        if result.chunk_id not in best or score > best[result.chunk_id][0]:
            best[result.chunk_id] = (score, result)
    ranked = sorted(best.values(), key=lambda pair: pair[0], reverse=True)

    keep = KEEP_ONE if len(programmes) == 1 else KEEP_EACH
    chosen: dict[str, tuple[float, SearchResult]] = {}
    for programme in programmes:
        mine = [pair for pair in ranked if pair[1].programme == programme]
        for pair in mine[:keep]:
            chosen[pair[1].chunk_id] = pair
    for pair in ranked:
        if len(chosen) >= top_k:
            break
        chosen.setdefault(pair[1].chunk_id, pair)

    return [r for _, r in sorted(chosen.values(), key=lambda pair: pair[0], reverse=True)]


def retrieve(
    tenant_id: str,
    query: str,
    vector_store: VectorStore,
    chunk_store: ChunkStore | None = None,
    config: RetrievalConfig | None = None,
    programmes: list[str] | None = None,
    overview: bool = False,
) -> list[SearchResult]:
    """Retrieve for a query. config defaults to what config.py sets.

    With `programmes` (the conversation's focus), the focused programmes'
    sections and everything else are ranked together, the focused ones
    with a small boost, so a school-wide section still wins a school-wide
    question. See blend(). With `overview`, the tenant's overview section
    (all programmes side by side) is added if not already there.
    """
    settings = get_settings()
    config = config or RetrievalConfig.from_settings(settings)
    candidates = config.candidates

    started = time.monotonic()
    query_vector = embed_query(query)
    embed_ms = (time.monotonic() - started) * 1000

    started = time.monotonic()
    vector_results = vector_store.search(tenant_id, query_vector, candidates)
    search_ms = (time.monotonic() - started) * 1000

    keyword_results = []
    if config.use_keyword and chunk_store is not None:
        keyword_results = keyword_search(
            tenant_id, query, chunk_store, candidates
        )

    if keyword_results:
        fused = reciprocal_rank_fusion(
            [vector_results, keyword_results],
            candidates,
            weights=[settings.vector_weight, config.keyword_weight],
        )
    else:
        fused = vector_results[:candidates]

    if config.use_rerank:
        final = rerank(query, fused, settings.retrieval_top_k)
    else:
        final = fused[: settings.retrieval_top_k]

    if programmes:
        focused = [
            result
            for programme in programmes
            for result in vector_store.search(
                tenant_id, query_vector, FOCUS_POOL, programmes=[programme]
            )
        ]
        general = vector_store.search(tenant_id, query_vector, GENERAL_POOL)
        final = blend(
            focused, general, settings.retrieval_top_k, programmes,
            settings.focus_boost,
        )

    if overview:
        # The side-by-side overview is added, not ranked: the question named
        # a whole group of programmes, so it must be in view, but putting it
        # first pushed better matches down ("what will I learn on the
        # 200-hour course?" names a group and wants the curriculum).
        table = vector_store.search(tenant_id, query_vector, 1, programmes=[OVERVIEW])
        present = {r.chunk_id for r in final}
        final = final + [t for t in table if t.chunk_id not in present]

    logger.info(
        "Retrieved for %s (focus %s): %d vector, %d keyword, %d fused, "
        "%d final (embed %.0fms, search %.0fms): %s",
        tenant_id,
        programmes or "none",
        len(vector_results),
        len(keyword_results),
        len(fused),
        len(final),
        embed_ms,
        search_ms,
        [r.chunk_id for r in final],
    )
    return final