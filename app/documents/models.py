"""The shape of an ingested document and its chunks.

A chunk is one searchable piece of a document. It carries enough context to
be understood alone, because at retrieval time it will be read without the
surrounding document.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    # Stable id, built from the document and position so re-ingesting the
    # same document overwrites rather than duplicates.
    chunk_id: str

    tenant_id: str
    document_id: str

    # Human-readable source, shown to the visitor as a citation.
    document_title: str

    # Headings above this chunk, outermost first.
    # e.g. ["200-hour Course", "Curriculum", "Yoga Anatomy"]
    heading_path: list[str] = Field(default_factory=list)

    # The chunk's own text, without the heading prefix.
    text: str

    # Position in the document, used to put chunks back in order.
    position: int

    # The programme (course or retreat) this chunk's section belongs to,
    # from the tenant's programmes.yaml; "" for general sections. Lets
    # retrieval fetch from the programme a visitor is asking about.
    programme: str = ""

    @property
    def heading(self) -> str:
        """The innermost heading, or the document title if there is none."""
        return self.heading_path[-1] if self.heading_path else self.document_title

    def to_searchable_text(self) -> str:
        """Text as it will be embedded and searched.

        The heading path is prepended so a chunk about anatomy is findable
        by the word "anatomy" even if its body never uses the word.
        """
        if not self.heading_path:
            return self.text
        return " > ".join(self.heading_path) + "\n\n" + self.text


class Document(BaseModel):
    document_id: str
    tenant_id: str
    title: str
    source: str  # Filename or URL it came from.
    chunk_count: int = 0
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )