"""Stores and searches chunk vectors in Pinecone.

Every method takes a tenant_id and maps it to a Pinecone namespace. A search
is scoped to one namespace, so one tenant's chunks are unreachable from
another tenant's query by construction.

Chunk text is stored alongside the vector as metadata. That costs a little
storage but means a search returns everything needed to build an answer in
one round trip, with no second lookup in Firestore.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pinecone import DenseVectorQuery, Pinecone

from app.config import get_settings
from app.documents.models import Chunk
from pinecone.errors.exceptions import NotFoundError
import time

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    chunk_id: str
    text: str
    document_title: str
    heading_path: list[str]
    score: float
    # The programme the chunk belongs to ("" for general), from ingest.
    programme: str = ""

    @property
    def citation_title(self) -> str:
        """Where this chunk sits, as the model sees it: its full heading path.

        Not just the last heading: a section called
        "Fees" exists once per programme, and its text does not name the
        programme, so "7-Day Yoga Retreat (Rishikesh) > Fees" is what stops
        the model reading one programme's prices as another's.
        """
        # The document title is kept too: in a single-course document the
        # title is the only place that names the programme ("200-Hour ...
        # Naturopathy ... > Food" is Rishikesh food, not Mysore's).
        path = [h for h in self.heading_path if h]
        return " > ".join(path) if path else self.document_title

    def to_searchable_text_for_rerank(self) -> str:
        """Text given to the reranker, with its heading for context."""
        if self.heading_path:
            return " > ".join(self.heading_path) + "\n\n" + self.text
        return self.text


class VectorStore(ABC):
    @abstractmethod
    def upsert_chunks(self, tenant_id: str, chunks: list[Chunk]) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        tenant_id: str,
        query_vector: list[float],
        top_k: int,
        programmes: list[str] | None = None,
    ) -> list[SearchResult]:
        """The top_k chunks, only from `programmes` if given."""
        raise NotImplementedError

    @abstractmethod
    def delete_document(self, tenant_id: str, document_id: str) -> None:
        raise NotImplementedError


class InMemoryVectorStore(VectorStore):
    """Used by tests. Scores by dot product, with no index."""

    def __init__(self) -> None:
        self._data: dict[str, list[tuple[Chunk, list[float]]]] = {}

    def upsert_chunks(self, tenant_id: str, chunks: list[Chunk]) -> None:
        from app.retrieval.embeddings import embed_texts

        vectors = embed_texts([c.to_searchable_text() for c in chunks])
        existing = self._data.setdefault(tenant_id, [])
        ids = {c.chunk_id for c in chunks}
        existing[:] = [(c, v) for c, v in existing if c.chunk_id not in ids]
        existing.extend(zip(chunks, vectors))

    def search(
        self,
        tenant_id: str,
        query_vector: list[float],
        top_k: int,
        programmes: list[str] | None = None,
    ) -> list[SearchResult]:
        entries = [
            (chunk, vector)
            for chunk, vector in self._data.get(tenant_id, [])
            if programmes is None or chunk.programme in programmes
        ]

        scored = []
        for chunk, vector in entries:
            score = sum(a * b for a, b in zip(query_vector, vector))
            scored.append((score, chunk))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        return [
            SearchResult(
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                document_title=chunk.document_title,
                heading_path=chunk.heading_path,
                score=score,
                programme=chunk.programme,
            )
            for score, chunk in scored[:top_k]
        ]

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        entries = self._data.get(tenant_id, [])
        entries[:] = [
            (c, v) for c, v in entries if c.document_id != document_id
        ]


class PineconeVectorStore(VectorStore):
    def __init__(self) -> None:
        settings = get_settings()
        self._pc = Pinecone(api_key=settings.pinecone_api_key.get_secret_value())
        self._index_name = settings.pinecone_index_name
        self._index_handle = None

    @property
    def _index(self):
        """Built on first use, not at startup.

        pc.index() makes a network call to describe the index. Doing that
        during startup adds seconds before the container is ready to serve,
        and a cold instance under Cloud Run pays it on every scale-up.
        """
        if self._index_handle is None:
            self._index_handle = self._pc.index(self._index_name)
        return self._index_handle

    def _namespace(self, tenant_id: str) -> str:
        return f"tenant-{tenant_id}"

    def upsert_chunks(self, tenant_id: str, chunks: list[Chunk]) -> None:
        from app.retrieval.embeddings import embed_texts

        if not chunks:
            return

        vectors = embed_texts([c.to_searchable_text() for c in chunks])

        documents = []
        for chunk, vector in zip(chunks, vectors):
            documents.append(
                {
                    "_id": chunk.chunk_id,
                    "embedding": vector,
                    "text": chunk.text,
                    "document_id": chunk.document_id,
                    "document_title": chunk.document_title,
                    "heading_path": " > ".join(chunk.heading_path),
                    "programme": chunk.programme,
                }
            )

        self._index.documents.upsert(
            namespace=self._namespace(tenant_id),
            documents=documents,
        )
        logger.info("Indexed %d chunks for %s", len(chunks), tenant_id)

    def search(
        self,
        tenant_id: str,
        query_vector: list[float],
        top_k: int,
        programmes: list[str] | None = None,
    ) -> list[SearchResult]:
        started = time.monotonic()
        extra = {"filter": {"programme": {"$in": programmes}}} if programmes else {}
        response = self._index.documents.search(
            namespace=self._namespace(tenant_id),
            top_k=top_k,
            score_by=[DenseVectorQuery(field="embedding", values=query_vector)],
            include_fields=["text", "document_title", "heading_path", "programme"],
            **extra,
        )
        logger.info(
            "Pinecone search took %.0fms", (time.monotonic() - started) * 1000
        )

        results = []
        for match in response.matches:
            fields = match.fields if hasattr(match, "fields") else match
            heading = fields.get("heading_path", "")
            results.append(
                SearchResult(
                    chunk_id=match.id,
                    text=fields.get("text", ""),
                    document_title=fields.get("document_title", ""),
                    heading_path=heading.split(" > ") if heading else [],
                    score=match.score,
                    programme=fields.get("programme", "") or "",
                )
            )
        return results

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        # The index has a document schema, so writes, deletes included, must
        # go through the documents API. The plain vectors endpoint returns
        # 400 once the namespace exists.
        try:
            self._index.documents.delete(
                namespace=self._namespace(tenant_id),
                filter={"document_id": {"$eq": document_id}},
            )
        except NotFoundError:
            # Namespace does not exist yet. Nothing to delete on a first
            # ingest, so this is expected rather than an error.
            logger.debug(
                "Namespace for %s not found, nothing to delete", tenant_id
            )