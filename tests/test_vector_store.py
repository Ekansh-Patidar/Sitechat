"""PineconeVectorStore against a fake index: no network.

The index has a document schema, so every write, deletes included, must
go through index.documents. The plain index.delete returns 400 there,
which broke re-ingesting once a tenant's namespace existed.
"""

from unittest.mock import MagicMock

from app.retrieval.vector_store import PineconeVectorStore


def store_with(index):
    store = object.__new__(PineconeVectorStore)
    store._index_handle = index
    return store


def test_delete_document_uses_the_documents_api():
    index = MagicMock()
    store_with(index).delete_document("demo-yoga", "course-200hr")

    index.documents.delete.assert_called_once_with(
        namespace="tenant-demo-yoga",
        filter={"document_id": {"$eq": "course-200hr"}},
    )
    index.delete.assert_not_called()
