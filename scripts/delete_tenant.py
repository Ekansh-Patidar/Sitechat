"""Delete a tenant's data, or one conversation's, from Firestore and Pinecone.

    python -m scripts.delete_tenant pyramid-yogshala                     # dry run
    python -m scripts.delete_tenant pyramid-yogshala --delete --confirm pyramid-yogshala
    python -m scripts.delete_tenant pyramid-yogshala --conversation <id>  # dry run
    python -m scripts.delete_tenant pyramid-yogshala --conversation <id> --delete --confirm pyramid-yogshala

A dry run (the default) only prints what would be deleted. Deleting needs
--delete and --confirm repeating the tenant id, because it cannot be undone.

Whole tenant: the tenant record, facts, programmes, documents, chunks,
conversations, leads, feedback, daily token and lead-email counts, and the
tenant's Pinecone namespace. Documents are found both by
their tenant_id field and by the "<tenant>__" id prefix, so records written
without the field are not missed.

One conversation (--conversation): that conversation, its lead and its
feedback, for a visitor asking for their data to be deleted, or for
clearing test chats.
"""

import argparse
import sys

from google.cloud import firestore

from app.config import get_settings
from app.logging_setup import setup_logging

# Collections keyed "<tenant>__<id>" that also carry a tenant_id field.
PER_TENANT = ("conversations", "leads", "documents", "chunks", "feedback",
              "token_usage", "lead_email_counts")
# Collections keyed by the tenant id itself.
BY_TENANT_ID = ("tenants", "facts", "programmes")
# Every collection the app writes must be in one of the two
# (tests/test_delete_tenant.py checks), or deleting a tenant leaves it behind.

BATCH_SIZE = 400  # Firestore allows 500 writes per batch


def find_tenant_documents(db, tenant_id: str) -> dict[str, list]:
    """Every document reference belonging to the tenant, per collection."""
    found: dict[str, list] = {}
    for name in PER_TENANT:
        collection = db.collection(name)
        refs = {}
        query = collection.where(
            filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
        )
        for doc in query.stream():
            refs[doc.id] = doc.reference
        prefix = f"{tenant_id}__"
        for doc in collection.stream():
            if doc.id.startswith(prefix):
                refs[doc.id] = doc.reference
        found[name] = list(refs.values())
    for name in BY_TENANT_ID:
        doc = db.collection(name).document(tenant_id).get()
        found[name] = [doc.reference] if doc.exists else []
    return found


def find_conversation_documents(
    db, tenant_id: str, conversation_id: str
) -> dict[str, list]:
    """The conversation, its lead and its feedback."""
    doc_id = f"{tenant_id}__{conversation_id}"
    found = {}
    for name in ("conversations", "leads"):
        doc = db.collection(name).document(doc_id).get()
        found[name] = [doc.reference] if doc.exists else []
    # Feedback is keyed "<tenant>__<conversation>__<reply>".
    query = db.collection("feedback").where(
        filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
    )
    found["feedback"] = [
        doc.reference for doc in query.stream()
        if doc.id.startswith(f"{doc_id}__")
    ]
    return found


def delete_references(db, refs: list) -> None:
    for start in range(0, len(refs), BATCH_SIZE):
        batch = db.batch()
        for ref in refs[start : start + BATCH_SIZE]:
            batch.delete(ref)
        batch.commit()


def pinecone_namespace(tenant_id: str) -> str:
    # Must match PineconeVectorStore._namespace.
    return f"tenant-{tenant_id}"


def _pinecone_index():
    from pinecone import Pinecone

    settings = get_settings()
    client = Pinecone(api_key=settings.pinecone_api_key.get_secret_value())
    return client.Index(settings.pinecone_index_name)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tenant_id")
    parser.add_argument("--conversation", help="delete only this conversation")
    parser.add_argument("--delete", action="store_true", help="actually delete")
    parser.add_argument("--confirm", help="repeat the tenant id to delete")
    args = parser.parse_args(argv)

    if args.delete and args.confirm != args.tenant_id:
        print("Refusing: --delete needs --confirm with the same tenant id.")
        sys.exit(1)

    setup_logging("WARNING")
    db = firestore.Client(project=get_settings().gcp_project_id)

    if args.conversation:
        found = find_conversation_documents(db, args.tenant_id, args.conversation)
        namespace = None
    else:
        found = find_tenant_documents(db, args.tenant_id)
        namespace = pinecone_namespace(args.tenant_id)

    for name, refs in found.items():
        print(f"{name:<18} {len(refs)}")

    vectors = 0
    if namespace:
        index = _pinecone_index()
        stats = index.describe_index_stats()
        entry = stats.namespaces.get(namespace) if stats.namespaces else None
        vectors = entry.vector_count if entry else 0
        print(f"{'pinecone':<18} {vectors} vectors in namespace {namespace}")

    if not args.delete:
        print("\nDry run. Nothing deleted. Add --delete --confirm "
              f"{args.tenant_id} to delete.")
        return

    for refs in found.values():
        delete_references(db, refs)
    if namespace and vectors:
        # Document-schema index: deletes go through the documents API.
        _pinecone_index().documents.delete(namespace=namespace, delete_all=True)
    print("\nDeleted.")


if __name__ == "__main__":
    main()
