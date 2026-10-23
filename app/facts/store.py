"""Where structured facts are stored.

One document per tenant, keyed by tenant_id. Facts are read on every
request, so this store is cached in the same way as the tenant store.
"""

import logging
from abc import ABC, abstractmethod

from google.cloud import firestore

from app.facts.models import FactSheet

import time

from app.config import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "facts"


class FactStore(ABC):
    @abstractmethod
    def get(self, tenant_id: str) -> FactSheet | None:
        raise NotImplementedError

    @abstractmethod
    def save(self, sheet: FactSheet) -> None:
        raise NotImplementedError


class InMemoryFactStore(FactStore):
    def __init__(self) -> None:
        self._data: dict[str, FactSheet] = {}

    def get(self, tenant_id: str) -> FactSheet | None:
        return self._data.get(tenant_id)

    def save(self, sheet: FactSheet) -> None:
        self._data[sheet.tenant_id] = sheet


class FirestoreFactStore(FactStore):
    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def get(self, tenant_id: str) -> FactSheet | None:
        doc = self._db.collection(COLLECTION).document(tenant_id).get()
        if not doc.exists:
            return None
        return FactSheet(**(doc.to_dict() or {}))

    def save(self, sheet: FactSheet) -> None:
        self._db.collection(COLLECTION).document(sheet.tenant_id).set(
            sheet.model_dump(mode="json")
        )
        logger.info("Saved facts for tenant %s", sheet.tenant_id)

# tenant_id -> (sheet, time cached)
_cache: dict[str, tuple[FactSheet | None, float]] = {}


def get_cached_facts(store: FactStore, tenant_id: str) -> FactSheet | None:
    """Read a tenant's facts, reusing a recent result if there is one."""
    ttl = get_settings().tenant_cache_seconds
    now = time.monotonic()

    entry = _cache.get(tenant_id)
    if entry and (now - entry[1]) < ttl:
        return entry[0]

    sheet = store.get(tenant_id)
    _cache[tenant_id] = (sheet, now)
    return sheet