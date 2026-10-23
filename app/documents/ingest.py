"""Ties the loader, chunker and store together."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.sanitize import instruction_like, strip_invisible
from app.documents.chunker import chunk_markdown
from app.documents.loaders import LoaderError, load_text_file
from app.documents.models import Document
from app.documents.sections import section_key
from app.documents.store import ChunkStore
from app.programmes.models import ProgrammeCatalogue

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    document_id: str
    title: str
    chunk_count: int
    # Sections with text that reads like an instruction to a model, for the
    # person ingesting to check; ingested anyway (we prepare these files).
    warnings: list[str] = field(default_factory=list)


def make_document_id(filename: str) -> str:
    """Build a stable id from a filename.

    Stable matters: ingesting the same file twice must replace the old
    version rather than create a second copy.
    """
    stem = Path(filename).stem.lower()
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-")


def ingest_file(
    path: Path,
    tenant_id: str,
    store: ChunkStore,
    vector_store=None,
    catalogue: ProgrammeCatalogue | None = None,
) -> IngestResult:
    title, text = load_text_file(path)
    text = strip_invisible(text)
    document_id = make_document_id(path.name)

    chunks = chunk_markdown(
        text=text,
        tenant_id=tenant_id,
        document_id=document_id,
        document_title=title,
    )

    if not chunks:
        raise LoaderError(f"{path.name}: produced no usable text")

    if catalogue is not None:
        for chunk in chunks:
            chunk.programme = catalogue.programme_for(
                section_key(chunk.document_id, chunk.heading_path)
            )

    document = Document(
        document_id=document_id,
        tenant_id=tenant_id,
        title=title,
        source=path.name,
        chunk_count=len(chunks),
    )

    store.save_document(document, chunks)

    if vector_store is not None:
        vector_store.delete_document(tenant_id, document_id)
        vector_store.upsert_chunks(tenant_id, chunks)

    warnings = []
    for chunk in chunks:
        found = instruction_like(chunk.text)
        if found:
            warnings.append(f"{' > '.join(chunk.heading_path)}: {found!r}")

    return IngestResult(
        document_id=document_id, title=title, chunk_count=len(chunks),
        warnings=warnings,
    )