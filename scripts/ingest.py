"""Ingest a folder of documents for one tenant.

    python -m scripts.ingest pyramid-yogshala docs/pyramid-yogshala

Also stores the tenant's programmes.yaml, if there is one, and tags each
chunk with the programme its section belongs to.
"""

import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.documents.ingest import ingest_file
from app.documents.loaders import SUPPORTED_SUFFIXES, LoaderError
from app.documents.store import FirestoreChunkStore
from app.logging_setup import setup_logging
from app.programmes.models import load_catalogue
from app.programmes.store import FirestoreProgrammeStore
from app.retrieval.vector_store import PineconeVectorStore


logger = logging.getLogger(__name__)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python -m scripts.ingest <tenant_id> <folder>")
        sys.exit(1)

    tenant_id = sys.argv[1]
    folder = Path(sys.argv[2])

    settings = get_settings()
    setup_logging(settings.log_level)

    if not folder.is_dir():
        logger.error("Not a folder: %s", folder)
        sys.exit(1)

    files = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in SUPPORTED_SUFFIXES
    )

    if not files:
        logger.error("No supported files in %s", folder)
        sys.exit(1)

    store = FirestoreChunkStore(settings.gcp_project_id)
    vector_store = PineconeVectorStore()

    # The programme catalogue tags each chunk with its programme. Looked for
    # in the folder, then in docs/<tenant>/, so ingesting one file from
    # elsewhere still gets tagged.
    catalogue = None
    for candidate in (folder / "programmes.yaml", Path("docs") / tenant_id / "programmes.yaml"):
        if candidate.is_file():
            catalogue = load_catalogue(candidate, tenant_id)
            FirestoreProgrammeStore(settings.gcp_project_id).save(catalogue)
            print(f"  Programmes: {len(catalogue.programmes)} from {candidate}")
            break
    else:
        print("  No programmes.yaml: chunks will not be tagged by programme")

    total = 0
    for path in files:
        try:
            result = ingest_file(path, tenant_id, store, vector_store, catalogue)
        except LoaderError as exc:
            logger.error("Skipped: %s", exc)
            continue
        print(f"  {result.title}: {result.chunk_count} chunks")
        for warning in result.warnings:
            print(f"    CHECK, reads like an instruction to the bot: {warning}")
        total += result.chunk_count

    print(f"\nIngested {total} chunks for {tenant_id}")


if __name__ == "__main__":
    main()