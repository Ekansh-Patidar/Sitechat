"""Where tenants are stored.

TenantStore is an interface: it says what any storage backend must be able
to do, without saying how. FirestoreTenantStore is the real one.
InMemoryTenantStore is for tests, so they run with no network and no setup.

This is the same pattern as MemoryStore in simplechat. It means the rest of
the app depends only on the interface, so swapping the backend later touches
this file and nothing else.
"""

import logging
from abc import ABC, abstractmethod

from google.cloud import firestore

from app.tenants.models import Tenant

logger = logging.getLogger(__name__)

COLLECTION = "tenants"


class TenantStore(ABC):
    @abstractmethod
    def get_by_site_key(self, site_key: str) -> Tenant | None:
        """Return the tenant owning this site key, or None."""
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, tenant_id: str) -> Tenant | None:
        """Return a tenant by internal id, or None."""
        raise NotImplementedError

    @abstractmethod
    def save(self, tenant: Tenant) -> None:
        """Create or overwrite a tenant."""
        raise NotImplementedError

    @abstractmethod
    def with_facts_sheet(self) -> list[Tenant]:
        """Active tenants with a facts sheet, for the scheduled sync."""
        raise NotImplementedError


class InMemoryTenantStore(TenantStore):
    """Holds tenants in a dict. Used by tests."""

    def __init__(self) -> None:
        self._by_id: dict[str, Tenant] = {}

    def get_by_site_key(self, site_key: str) -> Tenant | None:
        for tenant in self._by_id.values():
            if tenant.site_key == site_key:
                return tenant
        return None

    def get_by_id(self, tenant_id: str) -> Tenant | None:
        return self._by_id.get(tenant_id)

    def save(self, tenant: Tenant) -> None:
        self._by_id[tenant.tenant_id] = tenant

    def with_facts_sheet(self) -> list[Tenant]:
        return [t for t in self._by_id.values() if t.is_active and t.facts_sheet_id]


class FirestoreTenantStore(TenantStore):
    """Stores tenants in Firestore, one document per tenant."""

    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def get_by_site_key(self, site_key: str) -> Tenant | None:
        query = (
            self._db.collection(COLLECTION)
            .where(filter=firestore.FieldFilter("site_key", "==", site_key))
            .limit(1)
        )
        for doc in query.stream():
            return Tenant(**(doc.to_dict() or {}))
        return None

    def get_by_id(self, tenant_id: str) -> Tenant | None:
        doc = self._db.collection(COLLECTION).document(tenant_id).get()
        if not doc.exists:
            return None
        return Tenant(**(doc.to_dict() or {}))

    def save(self, tenant: Tenant) -> None:
        data = tenant.model_dump(mode="json")
        self._db.collection(COLLECTION).document(tenant.tenant_id).set(data)
        logger.info("Saved tenant %s", tenant.tenant_id)

    def with_facts_sheet(self) -> list[Tenant]:
        # Every tenant, filtered here: one read per tenant, which is fine
        # for tens of tenants every few minutes (free tier: 50,000 a day).
        tenants = [Tenant(**(d.to_dict() or {}))
                   for d in self._db.collection(COLLECTION).stream()]
        return [t for t in tenants if t.is_active and t.facts_sheet_id]