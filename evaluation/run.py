"""Runs the evaluation set against a retrieval configuration.

Two configurations can be compared directly, which is the point: it turns
"hybrid retrieval should be better" into a number.
"""

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.documents.store import ChunkStore
from app.evaluation_config import RetrievalConfig
from app.programmes.focus import resolve_focus, wants_overview
from app.programmes.models import ProgrammeCatalogue
from app.retrieval.pipeline import retrieve
from app.retrieval.vector_store import VectorStore
from evaluation.metrics import (
    noise_at_k,
    precision_at_k,
    recall_at_k,
    matches,
    reciprocal_rank,
    section_key,
)
from evaluation.questions import QUESTIONS, EvalQuestion

logger = logging.getLogger(__name__)


@dataclass
class QuestionResult:
    question: EvalQuestion
    # Section keys of what was retrieved, in rank order (see metrics.py).
    retrieved: list[str]
    recall: float
    precision: float
    rr: float
    noise: int


@dataclass
class EvalReport:
    config_name: str
    results: list[QuestionResult]

    def _mean(self, values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def kinds(self) -> list[str]:
        """The question kinds present, in the order they first appear."""
        return list(dict.fromkeys(r.question.kind for r in self.results))

    def summary(self, kind: str | None = None) -> dict:
        results = [
            r for r in self.results
            if kind is None or r.question.kind == kind
        ]
        answerable = [r for r in results if r.question.answers_in]
        unanswerable = [r for r in results if not r.question.answers_in]

        return {
            "questions": len(results),
            "recall": self._mean([r.recall for r in answerable]),
            "precision": self._mean([r.precision for r in answerable]),
            "mrr": self._mean([r.rr for r in answerable]),
            "noise": self._mean([float(r.noise) for r in unanswerable]),
        }


def run_evaluation(
    tenant_id: str,
    config: RetrievalConfig,
    vector_store: VectorStore,
    chunk_store: ChunkStore,
    catalogue: ProgrammeCatalogue | None = None,
) -> EvalReport:
    settings = get_settings()
    top_k = settings.retrieval_top_k

    results = []
    for question in QUESTIONS:
        # The conversation's focus, as production works it out: carried
        # through the earlier messages, then this one. Without a catalogue
        # there is no focus and retrieval sees only this message.
        focus: list[str] = []
        if catalogue is not None:
            for earlier in question.history:
                focus = resolve_focus(earlier, focus, catalogue)
            focus = resolve_focus(question.question, focus, catalogue)
        retrieved = [
            section_key(r.chunk_id.rsplit("__", 1)[0], r.heading_path)
            for r in retrieve(
                tenant_id, question.question, vector_store, chunk_store, config,
                programmes=focus or None,
                overview=wants_overview(question.question, focus, catalogue),
            )
        ]
        groups = question.answers_in
        # Scored over everything the bot is given: usually top_k, more when
        # a comparison keeps two sections per side or the overview is added.
        k = max(top_k, len(retrieved))

        results.append(
            QuestionResult(
                question=question,
                retrieved=retrieved,
                recall=recall_at_k(retrieved, groups, k),
                precision=precision_at_k(retrieved, groups, k),
                rr=reciprocal_rank(retrieved, groups),
                noise=noise_at_k(retrieved, groups, k),
            )
        )

    return EvalReport(config_name=config.name, results=results)


def unmatched_labels(chunk_store: ChunkStore, tenant_id: str) -> list[str]:
    """Labels in the question set that match no section in the store.

    A group whose labels all have typos, or were left behind when a
    document's headings changed, would silently count as never retrieved
    and drag every score down. Checked before each run.
    """
    keys = {
        section_key(chunk.document_id, chunk.heading_path)
        for chunk in chunk_store.get_all_chunks(tenant_id)
    }
    # A group may name a section that does not exist yet (labels list a
    # section's old and new place across a restructure), so the check is
    # that every group can be found somewhere, not every label.
    return [
        f"{question.question!r}: {group}"
        for question in QUESTIONS
        for group in question.answers_in
        if not any(matches(key, label) for key in keys for label in group)
    ]
