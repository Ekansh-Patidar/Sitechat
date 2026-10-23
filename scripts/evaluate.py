"""Compare retrieval configurations on the evaluation set.

    python -m scripts.evaluate pyramid-yogshala
"""

import sys

from app.config import get_settings
from app.documents.store import FirestoreChunkStore
from app.evaluation_config import (
    FULL,
    HYBRID_EQUAL,
    HYBRID_WEIGHTED,
    VECTOR_ONLY,
    VECTOR_RERANK,
    RetrievalConfig,
)
from app.logging_setup import setup_logging
from app.programmes.store import FirestoreProgrammeStore
from app.retrieval.vector_store import PineconeVectorStore
from evaluation.run import EvalReport, run_evaluation, unmatched_labels
from evaluation.history import load_latest, save_run
from evaluation.questions import question_set_id


def print_report(report: EvalReport) -> None:
    overall = report.summary()
    print(f"\n{report.config_name}")
    print("-" * 60)
    print(
        f"  recall    {overall['recall']:.2f}   "
        f"precision {overall['precision']:.2f}   "
        f"mrr {overall['mrr']:.2f}   "
        f"noise {overall['noise']:.1f}"
    )
    for kind in report.kinds():
        if kind == "unanswerable":
            continue
        s = report.summary(kind)
        print(
            f"    {kind:<11} recall {s['recall']:.2f}   mrr {s['mrr']:.2f}   "
            f"({s['questions']} questions)"
        )


def print_misses(report: EvalReport) -> None:
    misses = [r for r in report.results if r.recall < 1.0]
    if not misses:
        return
    print(f"\n  Missed ({len(misses)}):")
    for result in misses:
        print(f"    {result.question.question}")
        print(f"      wanted:  {result.question.answers_in}")
        print("      got:")
        for key in result.retrieved:
            print(f"        {key}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.evaluate <tenant_id>")
        sys.exit(1)

    tenant_id = sys.argv[1]

    settings = get_settings()
    setup_logging("WARNING")

    vector_store = PineconeVectorStore()
    chunk_store = FirestoreChunkStore(settings.gcp_project_id)
    unmatched = unmatched_labels(chunk_store, tenant_id)
    if unmatched:
        print("These labels match no section; fix evaluation/questions.py:")
        for line in unmatched:
            print(f"  {line}")
        sys.exit(1)

    question_set = question_set_id()
    previous = load_latest(tenant_id, question_set)
    # Programme focus, as production uses it. None if the tenant has no
    # programmes.yaml ingested, which is the behaviour before focus existed.
    catalogue = FirestoreProgrammeStore(settings.gcp_project_id).get(tenant_id)
    print(f"Programme focus: {'on' if catalogue else 'off (no catalogue)'}")

    summaries = {}
    reports = []
    # "production" is built from config.py, so it measures exactly what
    # ships, including its candidate count; the others are fixed presets.
    production = RetrievalConfig.from_settings(settings)
    for config in (
        production, VECTOR_ONLY, VECTOR_RERANK, HYBRID_EQUAL, HYBRID_WEIGHTED, FULL
    ):
        report = run_evaluation(
            tenant_id, config, vector_store, chunk_store, catalogue
        )
        print_report(report)

        if previous and config.name in previous["configs"]:
            old = previous["configs"][config.name]
            delta = report.summary()["mrr"] - old["mrr"]
            print(f"    mrr change since last run: {delta:+.2f}")

        summaries[config.name] = report.summary()
        reports.append(report)

    path = save_run(tenant_id, summaries, question_set)
    print(f"\nSaved to {path}")
    print("\n" + "=" * 60)
    print("Misses with production settings")
    print("=" * 60)
    print_misses(reports[0])


if __name__ == "__main__":
    main()