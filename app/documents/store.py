"""Where chunks are stored.

Chunks are keyed by tenant, and every read takes a tenant_id. One tenant's
documents must never be reachable from another tenant's request, so the
isolation sits in the key rather than in a filter that could be forgotten.

This store holds the text. Phase 6 adds the vector index that makes it
searchable; the two are kept separate so the text survives re-indexing.
"""

import logging
from abc import ABC, abstractmethod

from google.cloud import firestore

from app.documents.models import Chunk, Document

logger = logging.getLogger(__name__)

CHUNKS = "chunks"
DOCUMENTS = "documents"


class ChunkStore(ABC):
    @abstractmethod
    def save_document(self, document: Document, chunks: list[Chunk]) -> None:
        """Store a document and replace all of its chunks."""
        raise NotImplementedError

    @abstractmethod
    def get_chunks(self, tenant_id: str, document_id: str) -> list[Chunk]:
        raise NotImplementedError

    @abstractmethod
    def list_documents(self, tenant_id: str) -> list[Document]:
        raise NotImplementedError

    @abstractmethod
    def delete_document(self, tenant_id: str, document_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_all_chunks(self, tenant_id: str) -> list[Chunk]:
        raise NotImplementedError


class InMemoryChunkStore(ChunkStore):
    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], Document] = {}
        self._chunks: dict[tuple[str, str], list[Chunk]] = {}

    def save_document(self, document: Document, chunks: list[Chunk]) -> None:
        key = (document.tenant_id, document.document_id)
        self._documents[key] = document
        self._chunks[key] = chunks

    def get_chunks(self, tenant_id: str, document_id: str) -> list[Chunk]:
        return self._chunks.get((tenant_id, document_id), [])

    def list_documents(self, tenant_id: str) -> list[Document]:
        return [
            doc
            for (t_id, _), doc in self._documents.items()
            if t_id == tenant_id
        ]

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        key = (tenant_id, document_id)
        self._documents.pop(key, None)
        self._chunks.pop(key, None)

    def get_all_chunks(self, tenant_id: str) -> list[Chunk]:
        result = []
        for (t_id, _), chunks in self._chunks.items():
            if t_id == tenant_id:
                result.extend(chunks)
        return result


class FirestoreChunkStore(ChunkStore):
    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def _doc_key(self, tenant_id: str, document_id: str) -> str:
        return f"{tenant_id}__{document_id}"

    def save_document(self, document: Document, chunks: list[Chunk]) -> None:
        # Remove old chunks first, so a re-ingest that produces fewer chunks
        # does not leave stale ones behind.
        self._delete_chunks(document.tenant_id, document.document_id)

        batch = self._db.batch()

        doc_ref = self._db.collection(DOCUMENTS).document(
            self._doc_key(document.tenant_id, document.document_id)
        )
        batch.set(doc_ref, document.model_dump(mode="json"))

        for chunk in chunks:
            chunk_ref = self._db.collection(CHUNKS).document(
                self._doc_key(chunk.tenant_id, chunk.chunk_id)
            )
            batch.set(chunk_ref, chunk.model_dump(mode="json"))

        batch.commit()
        logger.info(
            "Stored %d chunks for %s", len(chunks), document.document_id
        )

    def _delete_chunks(self, tenant_id: str, document_id: str) -> None:
        query = (
            self._db.collection(CHUNKS)
            .where(filter=firestore.FieldFilter("tenant_id", "==", tenant_id))
            .where(
                filter=firestore.FieldFilter("document_id", "==", document_id)
            )
        )
        batch = self._db.batch()
        count = 0
        for doc in query.stream():
            batch.delete(doc.reference)
            count += 1
        if count:
            batch.commit()

    def get_chunks(self, tenant_id: str, document_id: str) -> list[Chunk]:
        query = (
            self._db.collection(CHUNKS)
            .where(filter=firestore.FieldFilter("tenant_id", "==", tenant_id))
            .where(
                filter=firestore.FieldFilter("document_id", "==", document_id)
            )
        )
        chunks = [Chunk(**(doc.to_dict() or {})) for doc in query.stream()]
        return sorted(chunks, key=lambda c: c.position)

    def list_documents(self, tenant_id: str) -> list[Document]:
        query = self._db.collection(DOCUMENTS).where(
            filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
        )
        return [Document(**(doc.to_dict() or {})) for doc in query.stream()]

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        self._delete_chunks(tenant_id, document_id)
        self._db.collection(DOCUMENTS).document(
            self._doc_key(tenant_id, document_id)
        ).delete()

    def get_all_chunks(self, tenant_id: str) -> list[Chunk]:
        query = self._db.collection(CHUNKS).where(
            filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
        )
        return [Chunk(**(doc.to_dict() or {})) for doc in query.stream()]
