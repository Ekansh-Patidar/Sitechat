"""Tests for the chunker.

These check the behaviour retrieval depends on: sections stay whole,
heading paths are correct, and chunks are findable by their topic.
"""

from app.documents.chunker import chunk_markdown

SAMPLE = """# Course Guide

Intro text.

## Curriculum

### Yoga Anatomy

Students study the muscular system and practise adjustments.

### Philosophy

We read the Yoga Sutras.

## Daily Schedule

Mornings begin at 5:30.
"""


def chunk(text: str):
    return chunk_markdown(text, "t1", "doc1", "Course Guide")


def test_sections_become_separate_chunks():
    chunks = chunk(SAMPLE)
    headings = [c.heading for c in chunks]
    assert "Yoga Anatomy" in headings
    assert "Philosophy" in headings
    assert "Daily Schedule" in headings


def test_heading_path_is_nested_correctly():
    chunks = chunk(SAMPLE)
    anatomy = next(c for c in chunks if c.heading == "Yoga Anatomy")
    assert anatomy.heading_path == ["Course Guide", "Curriculum", "Yoga Anatomy"]


def test_leaving_a_subsection_pops_the_path():
    """Daily Schedule is under the H1, not under Curriculum."""
    chunks = chunk(SAMPLE)
    schedule = next(c for c in chunks if c.heading == "Daily Schedule")
    assert schedule.heading_path == ["Course Guide", "Daily Schedule"]


def test_searchable_text_includes_the_heading():
    """A chunk about anatomy must be findable by the word 'anatomy'."""
    chunks = chunk(SAMPLE)
    anatomy = next(c for c in chunks if c.heading == "Yoga Anatomy")
    assert "Yoga Anatomy" in anatomy.to_searchable_text()
    assert "muscular system" in anatomy.to_searchable_text()


def test_a_section_is_not_split_mid_sentence():
    chunks = chunk(SAMPLE)
    anatomy = next(c for c in chunks if c.heading == "Yoga Anatomy")
    assert anatomy.text.strip().endswith(".")


def test_long_sections_are_split_at_paragraph_breaks():
    paragraph = "This is a sentence about practice. " * 20
    long_text = "# Doc\n\n## Long Section\n\n" + "\n\n".join([paragraph] * 6)

    chunks = chunk(long_text)

    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) < 2200
        # Every piece keeps the heading, so none becomes orphaned.
        assert c.heading_path == ["Doc", "Long Section"]


def test_chunks_are_numbered_in_order():
    chunks = chunk(SAMPLE)
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_tiny_trailing_piece_is_merged():
    """A 100-character leftover chunk would be useless on its own."""
    body = "x" * 1750 + "\n\nshort tail."
    chunks = chunk(f"# Doc\n\n## Section\n\n{body}")
    assert all(len(c.text) > 200 for c in chunks)