"""Create the Pinecone index. Run once.

    python -m scripts.create_index

One index holds every tenant's vectors, separated by namespace. Namespaces
are the isolation boundary: a search scoped to one namespace cannot see
another's data, so cross-tenant leakage is prevented by the database rather
than by a filter in our query code.
"""

import logging

from pinecone import Pinecone

from app.config import get_settings
from app.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    pc = Pinecone(api_key=settings.pinecone_api_key.get_secret_value())

    existing = [idx.name for idx in pc.indexes.list()]
    if settings.pinecone_index_name in existing:
        logger.info("Index %s already exists", settings.pinecone_index_name)
        return

    pc.indexes.create(
        name=settings.pinecone_index_name,
        schema={
            "fields": {
                "embedding": {
                    "type": "dense_vector",
                    "dimension": settings.embedding_dimension,
                    "metric": "cosine",
                }
            }
        },
        deployment={
            "deployment_type": "managed",
            "cloud": "aws",
            "region": "us-east-1",
        },
    )
    logger.info("Created index %s", settings.pinecone_index_name)


if __name__ == "__main__":
    main()