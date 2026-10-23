"""Where programme catalogues are stored. One document per tenant.

Read on every message that might change the conversation's focus, so it is
cached like the facts sheet.
"""

import logging
import time
from abc import ABC, abstractmethod

from google.cloud import firestore

from app.config import get_settings
from app.programmes.models import ProgrammeCatalogue

logger = logging.getLogger(__name__)

COLLECTION = "programmes"


class ProgrammeStore(ABC):
    @abstractmethod
    def get(self, tenant_id: str) -> ProgrammeCatalogue | None:
        raise NotImplementedError

    @abstractmethod
    def save(self, catalogue: ProgrammeCatalogue) -> None:
        raise NotImplementedError


class InMemoryProgrammeStore(ProgrammeStore):
    def __init__(self) -> None:
        self._data: dict[str, ProgrammeCatalogue] = {}

    def get(self, tenant_id: str) -> ProgrammeCatalogue | None:
        return self._data.get(tenant_id)

    def save(self, catalogue: ProgrammeCatalogue) -> None:
        self._data[catalogue.tenant_id] = catalogue


class FirestoreProgrammeStore(ProgrammeStore):
    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def get(self, tenant_id: str) -> ProgrammeCatalogue | None:
        doc = self._db.collection(COLLECTION).document(tenant_id).get()
        if not doc.exists:
            return None
        return ProgrammeCatalogue(**(doc.to_dict() or {}))

    def save(self, catalogue: ProgrammeCatalogue) -> None:
        self._db.collection(COLLECTION).document(catalogue.tenant_id).set(
            catalogue.model_dump(mode="json")
        )
        logger.info("Saved programmes for tenant %s", catalogue.tenant_id)


# tenant_id -> (catalogue, time cached)
_cache: dict[str, tuple[ProgrammeCatalogue | None, float]] = {}


def get_cached_catalogue(
    store: ProgrammeStore, tenant_id: str
) -> ProgrammeCatalogue | None:
    ttl = get_settings().tenant_cache_seconds
    now = time.monotonic()
    entry = _cache.get(tenant_id)
    if entry and (now - entry[1]) < ttl:
        return entry[0]
    catalogue = store.get(tenant_id)
    _cache[tenant_id] = (catalogue, now)
    return catalogue
