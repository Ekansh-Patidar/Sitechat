"""Which parts of the retrieval pipeline to use.

Kept in app/ rather than evaluation/ because it describes the pipeline, and
because production may later want to switch parts off per tenant.
retrieval.pipeline.retrieve takes one of these; with none, it builds one
from settings, so production and the evaluation run the same code.
"""

from dataclasses import dataclass

from app.config import Settings


@dataclass
class RetrievalConfig:
    name: str
    use_keyword: bool = True
    use_rerank: bool = True
    keyword_weight: float = 1.0
    # Candidates each retriever returns before fusion. The comparisons in
    # notes/architecture-discussion.md section 5 were measured with 20, so
    # the evaluation configs keep 20. Production uses settings (5).
    candidates: int = 20

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetrievalConfig":
        """What production runs, as set in config.py."""
        return cls(
            "production",
            use_keyword=settings.use_keyword_search,
            use_rerank=settings.use_rerank,
            keyword_weight=settings.keyword_weight,
            candidates=settings.retrieval_candidates,
        )


VECTOR_ONLY = RetrievalConfig(
    "vector only", use_keyword=False, use_rerank=False
)
VECTOR_RERANK = RetrievalConfig(
    "vector + rerank", use_keyword=False, use_rerank=True
)
HYBRID_EQUAL = RetrievalConfig(
    "hybrid, equal weights", use_keyword=True, use_rerank=False,
    keyword_weight=1.0,
)
HYBRID_WEIGHTED = RetrievalConfig(
    "hybrid, keyword at 0.3", use_keyword=True, use_rerank=False,
    keyword_weight=0.3,
)
FULL = RetrievalConfig(
    "hybrid 0.3 + rerank", use_keyword=True, use_rerank=True,
    keyword_weight=0.3,
)