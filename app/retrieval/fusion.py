"""Merges several ranked lists into one, using Reciprocal Rank Fusion.

The problem: cosine similarity runs 0 to 1, BM25 is unbounded and varies by
corpus. Adding or averaging them is meaningless.

RRF ignores the scores and uses only the positions. A result at rank r in a
list contributes weight / (k + r). A chunk ranked highly by both retrievers
beats one ranked highly by just one, and no normalisation is needed.

k = 60 is the value from the original paper and the usual default. It damps
the difference between the top few positions, so a chunk at rank 1 does not
overwhelm one at rank 3.

On weights: plain RRF treats every list as equally trustworthy. Measured on
a 28-chunk corpus, that was wrong. BM25 ranks nearly at random when almost
every chunk shares a word with the query, and giving that ranking an equal
vote dropped semantic MRR from 0.93 to 0.52. Weighting lets a less reliable
retriever break ties without overriding a better one.

This should be revisited as corpora grow. The weight that is right at 28
chunks is unlikely to be right at 500, so re-run the evaluation when a
tenant's document set expands.
"""

from app.retrieval.vector_store import SearchResult

RRF_K = 60


def reciprocal_rank_fusion(
    result_lists: list[list[SearchResult]],
    top_k: int,
    weights: list[float] | None = None,
) -> list[SearchResult]:
    if weights is None:
        weights = [1.0] * len(result_lists)

    if len(weights) != len(result_lists):
        raise ValueError("weights must have one entry per result list")

    scores: dict[str, float] = {}
    by_id: dict[str, SearchResult] = {}

    for results, weight in zip(result_lists, weights):
        for rank, result in enumerate(results, start=1):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + (
                weight / (RRF_K + rank)
            )
            by_id.setdefault(result.chunk_id, result)

    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)

    merged = []
    for chunk_id, score in ordered[:top_k]:
        result = by_id[chunk_id]
        merged.append(
            SearchResult(
                chunk_id=result.chunk_id,
                text=result.text,
                document_title=result.document_title,
                heading_path=result.heading_path,
                score=score,
            )
        )
    return merged