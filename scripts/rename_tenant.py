"""Rename a tenant: copy its record and visitor data to a new tenant id.

    python -m scripts.rename_tenant demo-yoga pyramid-yogshala                 # dry run
    python -m scripts.rename_tenant demo-yoga pyramid-yogshala --copy --confirm pyramid-yogshala
    python -m scripts.rename_tenant demo-yoga pyramid-yogshala --site-key pk_... --copy --confirm ...

A tenant id is part of every document id and the Pinecone namespace, so
there is no rename in place. The whole move, in order:

  1. this script, --copy: the tenant record (with the new id, and a new
     site key if given), conversations, leads, feedback, and the daily
     token and lead-email counts, each under the new id. Nothing is
     deleted, and it refuses if anything already exists under the new id.
  2. python -m scripts.ingest <new> docs/<new>   (documents, chunks, the
     Pinecone vectors and programmes.yaml; rename the docs folder first)
  3. python -m scripts.sync_facts <new>
  4. python -m scripts.delete_tenant <old> --delete --confirm <old>

Between 1 and 4 both tenants hold the same site key unless --site-key is
given, so the widget may get either; do the four steps together, when
nobody is chatting. A new site key means updating the snippet on the
customer's site (and widget/test-page.html).
"""

import argparse
import sys

from google.cloud import firestore

from app.config import get_settings
from app.logging_setup import setup_logging

# Copied here, keyed "<tenant>__<id>" with a tenant_id field.
COPIED = ("conversations", "leads", "feedback", "token_usage", "lead_email_counts")
# Rebuilt by steps 2 and 3 rather than copied: documents, chunks,
# programmes, facts and the Pinecone namespace.
REBUILT = ("documents", "chunks", "programmes", "facts")

BATCH_SIZE = 400


def find(db, tenant_id: str) -> dict[str, list]:
    """The tenant's documents to copy, per collection, as snapshots."""
    found: dict[str, list] = {}
    prefix = f"{tenant_id}__"
    for name in COPIED:
        # By field and by id prefix, as delete_tenant finds them, so older
        # records written without the field are not left behind.
        collection, docs = db.collection(name), {}
        query = collection.where(
            filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
        )
        for doc in query.stream():
            if doc.id.startswith(prefix):
                docs[doc.id] = doc
        for doc in collection.stream():
            if doc.id.startswith(prefix):
                docs[doc.id] = doc
        found[name] = list(docs.values())
    tenant = db.collection("tenants").document(tenant_id).get()
    found["tenants"] = [tenant] if tenant.exists else []
    return found


def already_there(db, new_id: str) -> list[str]:
    """What exists under the new id; copying over it would mix two tenants."""
    clash = []
    if db.collection("tenants").document(new_id).get().exists:
        clash.append("tenants")
    for name in COPIED:
        query = db.collection(name).where(
            filter=firestore.FieldFilter("tenant_id", "==", new_id)
        )
        if any(True for _ in query.stream()):
            clash.append(name)
    return clash


def renamed(name: str, doc_id: str, data: dict, old: str, new: str,
            site_key: str = "") -> tuple[str, dict]:
    """The document's new id and contents."""
    data = dict(data)
    data["tenant_id"] = new
    if name == "tenants":
        if site_key:
            data["site_key"] = site_key
        return new, data
    return new + doc_id[len(old):], data


def copy(db, found: dict[str, list], old: str, new: str, site_key: str = "") -> None:
    # The tenant record last, so the new tenant appears once its data is there.
    order = [n for n in found if n != "tenants"] + ["tenants"]
    writes = [
        (name, *renamed(name, doc.id, doc.to_dict() or {}, old, new, site_key))
        for name in order for doc in found.get(name, [])
    ]
    for start in range(0, len(writes), BATCH_SIZE):
        batch = db.batch()
        for name, doc_id, data in writes[start : start + BATCH_SIZE]:
            batch.set(db.collection(name).document(doc_id), data)
        batch.commit()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old_id")
    parser.add_argument("new_id")
    parser.add_argument("--site-key", default="", help="a new site key for the tenant")
    parser.add_argument("--copy", action="store_true", help="actually copy")
    parser.add_argument("--confirm", help="repeat the new tenant id to copy")
    args = parser.parse_args(argv)

    if args.copy and args.confirm != args.new_id:
        print("Refusing: --copy needs --confirm with the new tenant id.")
        sys.exit(1)

    setup_logging("WARNING")
    db = firestore.Client(project=get_settings().gcp_project_id)

    found = find(db, args.old_id)
    if not found["tenants"]:
        print(f"No tenant {args.old_id}.")
        sys.exit(1)
    for name, docs in found.items():
        print(f"{name:<18} {len(docs)} to copy")
    print(f"{'rebuilt later':<18} {', '.join(REBUILT)} and Pinecone (steps 2 and 3)")

    clash = already_there(db, args.new_id)
    if clash:
        print(f"\nRefusing: {args.new_id} already has data in {', '.join(clash)}.")
        sys.exit(1)

    if not args.copy:
        print(f"\nDry run. Nothing copied. Add --copy --confirm {args.new_id} to copy.")
        return

    copy(db, found, args.old_id, args.new_id, args.site_key)
    print(f"\nCopied to {args.new_id}. Next:\n"
          f"  python -m scripts.ingest {args.new_id} docs/{args.new_id}\n"
          f"  python -m scripts.sync_facts {args.new_id}\n"
          f"  python -m scripts.delete_tenant {args.old_id} --delete --confirm {args.old_id}")


if __name__ == "__main__":
    main()
