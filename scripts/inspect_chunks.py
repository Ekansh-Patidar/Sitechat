"""Print a document's chunks, so you can see what the bot will retrieve.

    python -m scripts.inspect_chunks pyramid-yogshala
    python -m scripts.inspect_chunks pyramid-yogshala curriculum
"""

import sys

from app.config import get_settings
from app.documents.store import FirestoreChunkStore
from app.logging_setup import setup_logging


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.inspect_chunks <tenant_id> [document_id]")
        sys.exit(1)

    tenant_id = sys.argv[1]
    document_id = sys.argv[2] if len(sys.argv) > 2 else None

    settings = get_settings()
    setup_logging("WARNING")

    store = FirestoreChunkStore(settings.gcp_project_id)

    documents = store.list_documents(tenant_id)
    if not documents:
        print(f"No documents for {tenant_id}")
        return

    for document in documents:
        if document_id and document.document_id != document_id:
            continue

        print(f"\n{'=' * 70}")
        print(f"{document.title}  ({document.chunk_count} chunks)")
        print(f"{'=' * 70}")

        for chunk in store.get_chunks(tenant_id, document.document_id):
            path = " > ".join(chunk.heading_path) or "(no heading)"
            preview = chunk.text[:200].replace("\n", " ")
            print(f"\n[{chunk.position}] {path}  ({len(chunk.text)} chars)")
            print(f"    {preview}{'...' if len(chunk.text) > 200 else ''}")


if __name__ == "__main__":
    main()