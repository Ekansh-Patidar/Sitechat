"""Tests for the tenant store.

These run against InMemoryTenantStore, so they need no network, no
credentials and no Firestore. They are checking the behaviour the rest of
the app relies on, not the storage technology.
"""


from app.tenants.models import Tenant
from app.tenants.store import InMemoryTenantStore


def make_tenant(tenant_id: str = "t1", site_key: str = "pk_1") -> Tenant:
    return Tenant(
        tenant_id=tenant_id,
        site_key=site_key,
        display_name="Test School",
        allowed_domains=["example.com"],
    )


def test_save_and_get_by_id():
    store = InMemoryTenantStore()
    store.save(make_tenant())

    found = store.get_by_id("t1")
    assert found is not None
    assert found.display_name == "Test School"


def test_get_by_site_key():
    store = InMemoryTenantStore()
    store.save(make_tenant(tenant_id="t1", site_key="pk_abc"))

    found = store.get_by_site_key("pk_abc")
    assert found is not None
    assert found.tenant_id == "t1"


def test_unknown_lookups_return_none():
    store = InMemoryTenantStore()
    assert store.get_by_id("nope") is None
    assert store.get_by_site_key("nope") is None


def test_tenants_are_isolated_by_site_key():
    """A site key must only ever return its own tenant."""
    store = InMemoryTenantStore()
    store.save(make_tenant(tenant_id="t1", site_key="pk_1"))
    store.save(make_tenant(tenant_id="t2", site_key="pk_2"))

    assert store.get_by_site_key("pk_1").tenant_id == "t1"
    assert store.get_by_site_key("pk_2").tenant_id == "t2"


def test_defaults_are_not_shared_between_tenants():
    """Each tenant must get its own list, not a shared one."""
    a = Tenant(tenant_id="a", site_key="ka", display_name="A")
    b = Tenant(tenant_id="b", site_key="kb", display_name="B")

    a.suggested_questions.append("Only for A")

    assert b.suggested_questions == []