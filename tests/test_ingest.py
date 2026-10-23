"""Tests for the ingestion pipeline."""

import pytest

from app.documents.ingest import ingest_file, make_document_id
from app.documents.loaders import LoaderError, load_text_file
from app.documents.store import InMemoryChunkStore


def write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_document_id_is_stable_and_safe():
    assert make_document_id("Course Guide v2.md") == "course-guide-v2"
    assert make_document_id("curriculum.md") == "curriculum"


def test_title_comes_from_the_h1(tmp_path):
    path = write(tmp_path, "notes.md", "# Real Title\n\nBody text.")
    title, _ = load_text_file(path)
    assert title == "Real Title"


def test_title_falls_back_to_the_filename(tmp_path):
    path = write(tmp_path, "course-guide.md", "Just body text.")
    title, _ = load_text_file(path)
    assert title == "Course Guide"


def test_unsupported_type_is_rejected(tmp_path):
    path = write(tmp_path, "brochure.pdf", "content")
    with pytest.raises(LoaderError):
        load_text_file(path)


def test_empty_file_is_rejected(tmp_path):
    path = write(tmp_path, "empty.md", "   \n  ")
    with pytest.raises(LoaderError):
        load_text_file(path)


def test_ingest_stores_chunks(tmp_path):
    path = write(
        tmp_path, "guide.md", "# Guide\n\n## Section A\n\nText A.\n\n## Section B\n\nText B."
    )
    store = InMemoryChunkStore()

    result = ingest_file(path, "t1", store)

    assert result.chunk_count >= 2
    chunks = store.get_chunks("t1", "guide")
    assert len(chunks) == result.chunk_count


def test_reingesting_replaces_rather_than_duplicates(tmp_path):
    """Editing a document must not leave the old version behind."""
    store = InMemoryChunkStore()

    path = write(tmp_path, "guide.md", "# Guide\n\n## A\n\nFirst version.")
    ingest_file(path, "t1", store)

    path.write_text("# Guide\n\n## A\n\nSecond version.", encoding="utf-8")
    ingest_file(path, "t1", store)

    chunks = store.get_chunks("t1", "guide")
    assert len(chunks) == 1
    assert "Second version" in chunks[0].text


def test_documents_are_isolated_between_tenants(tmp_path):
    store = InMemoryChunkStore()
    path = write(tmp_path, "guide.md", "# Guide\n\n## A\n\nTenant one content.")

    ingest_file(path, "t1", store)

    assert store.get_chunks("t2", "guide") == []
    assert store.list_documents("t2") == []