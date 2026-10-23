"""Tests for the evaluation metrics.

The metrics are what every later judgement rests on, so they need to be
right. A quietly wrong recall calculation would make a bad change look good.
"""

from evaluation.metrics import (
    matches,
    noise_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    section_key,
)
from evaluation.questions import QUESTIONS

CUR = "web | 300-Hour > Curriculum"
FEES = "web | 300-Hour > Fees"
OVERVIEW = "web | 300-Hour"
OTHER = "old | Food"


def test_section_key_drops_the_document_title():
    assert section_key("web", ["Title", "300-Hour", "Fees"]) == "web | 300-Hour > Fees"


def test_label_matches_the_section_and_below_it():
    assert matches(CUR, "web | 300-Hour")
    assert matches(CUR, "web | 300-Hour > Curriculum")
    assert not matches(CUR, "web | 300-Hour > Fees")


def test_label_does_not_match_a_longer_sibling_name():
    """'100-Hour' must not match '100-Hour Retreat'."""
    assert not matches("web | 100-Hour Retreat", "web | 100-Hour")


def test_exact_label_excludes_sub_sections():
    assert matches(OVERVIEW, "=web | 300-Hour")
    assert not matches(CUR, "=web | 300-Hour")


def test_document_label_matches_anything_in_it():
    assert matches("old | Food", "old |")
    assert not matches(CUR, "old |")


def test_recall_needs_every_group():
    """A comparison is only fully answered when both sides are found."""
    groups = [["web | 300-Hour"], ["old | Food"]]
    assert recall_at_k([CUR, OTHER], groups, k=5) == 1.0
    assert recall_at_k([CUR], groups, k=5) == 0.5


def test_recall_accepts_any_label_in_a_group():
    assert recall_at_k([OTHER], [["web | Certification", "old | Food"]], k=5) == 1.0


def test_recall_respects_k():
    assert recall_at_k([OTHER, CUR], [[CUR]], k=1) == 0.0


def test_recall_is_one_when_nothing_is_expected():
    assert recall_at_k([CUR], [], k=5) == 1.0


def test_precision_counts_relevant_results_in_the_top_k():
    assert precision_at_k([CUR, OTHER], [["web | 300-Hour"]], k=2) == 0.5
    assert precision_at_k([], [[CUR]], k=5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank([OTHER, FEES], [[FEES]]) == 0.5
    assert reciprocal_rank([OTHER], [[FEES]]) == 0.0


def test_noise_only_for_unanswerable():
    assert noise_at_k([CUR, OTHER], [], k=5) == 2
    assert noise_at_k([CUR, OTHER], [[CUR]], k=5) == 0


def test_every_question_is_well_formed():
    kinds = {"semantic", "keyword", "programme", "comparison", "follow_up", "trap",
             "general", "list", "language", "unanswerable"}
    for q in QUESTIONS:
        assert q.kind in kinds, q.question
        assert bool(q.answers_in) == (q.kind != "unanswerable"), q.question
        assert all(group for group in q.answers_in), q.question
        if q.kind == "follow_up":
            assert q.history, q.question
        elif q.kind not in ("trap", "general", "list"):
            assert not q.history, q.question


def test_unmatched_labels_are_reported():
    from app.documents.models import Chunk
    from app.documents.store import InMemoryChunkStore
    from app.documents.models import Document
    from evaluation.run import unmatched_labels

    store = InMemoryChunkStore()
    chunk = Chunk(
        chunk_id="course-200hr__0", tenant_id="t1", document_id="course-200hr",
        document_title="T", heading_path=["T", "Food"], text="x", position=0,
    )
    store.save_document(
        Document(document_id="course-200hr", tenant_id="t1", title="T", source="s", chunk_count=1),
        [chunk],
    )
    missing = unmatched_labels(store, "t1")
    assert missing  # the real question set refers to many other sections
    assert not any("course-200hr | Food" in line for line in missing)
