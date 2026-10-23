"""Turns text into vectors.

An embedding is a list of numbers representing meaning. Two texts about the
same topic produce vectors that point in a similar direction, which is what
makes similarity search work without any shared keywords.

The same model must be used for indexing and for queries. Vectors from
different models are not comparable, so changing the model means
re-embedding everything.
"""

import logging

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        # Short timeout: an embedding takes well under a second, and the
        # SDK default (600 s, two retries) would outlast the request.
        _client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.embedding_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed several texts in one call.

    Batching matters during ingestion: embedding 50 chunks one at a time is
    50 round trips, while one call handles them together.
    """
    if not texts:
        return []

    settings = get_settings()
    response = _get_client().embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]


def embed_query(text: str) -> list[float]:
    """Embed a single search query."""
    return embed_texts([text])[0]