"""scripts/rename_tenant.py against an in-memory fake Firestore."""

from unittest.mock import patch

import pytest

from scripts import delete_tenant, rename_tenant


class Snapshot:
    def __init__(self, db, collection, doc_id):
        self.id = doc_id
        self._data = db.data.get(collection, {}).get(doc_id)
        self.exists = self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class Query:
    def __init__(self, docs):
        self.docs = docs

    def stream(self):
        return iter(self.docs)


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def _all(self):
        return self.db.data.get(self.name, {})

    def stream(self):
        return iter([Snapshot(self.db, self.name, i) for i in list(self._all())])

    def where(self, filter):
        return Query([Snapshot(self.db, self.name, i) for i, f in self._all().items()
                      if f.get(filter.field_path) == filter.value])

    def document(self, doc_id):
        db, name = self.db, self.name

        class Doc:
            def __init__(self):
                self.collection, self.id = name, doc_id

            def get(self):
                return Snapshot(db, name, doc_id)

        return Doc()


class Batch:
    def __init__(self, db):
        self.db, self.writes = db, []

    def set(self, ref, data):
        self.writes.append((ref, data))

    def commit(self):
        for ref, data in self.writes:
            self.db.data.setdefault(ref.collection, {})[ref.id] = data


class FakeDB:
    def __init__(self, data):
        self.data = data

    def collection(self, name):
        return Collection(self, name)

    def batch(self):
        return Batch(self)


def sample():
    return FakeDB({
        "tenants": {"demo": {"tenant_id": "demo", "site_key": "pk_demo", "display_name": "S"},
                    "other": {"tenant_id": "other", "site_key": "pk_other"}},
        "conversations": {
            "demo__c1": {"tenant_id": "demo", "summary": "s", "expire_at": "2027-10-18"},
            "demo__c2": {},  # written before the field existed
            "other__c1": {"tenant_id": "other"},
            "demox__c1": {"tenant_id": "demox"},  # prefix must not match demo
        },
        "leads": {"demo__c1": {"tenant_id": "demo", "email": "a@example.com"}},
        "feedback": {"demo__c1__r1": {"tenant_id": "demo", "conversation_id": "c1"}},
        "token_usage": {"demo__2026-10-18": {"tenant_id": "demo", "tokens": 5}},
        "lead_email_counts": {},
    })


def run(db, *args):
    with patch.object(rename_tenant.firestore, "Client", return_value=db):
        rename_tenant.main(list(args))


def test_dry_run_copies_nothing(capsys):
    db = sample()
    run(db, "demo", "new")

    out = capsys.readouterr().out
    assert "conversations      2 to copy" in out
    assert "Dry run" in out
    assert "new" not in db.data["tenants"]


def test_copy_moves_everything_under_the_new_id_and_keeps_the_old():
    db = sample()
    run(db, "demo", "new", "--copy", "--confirm", "new")

    assert db.data["tenants"]["new"]["tenant_id"] == "new"
    assert db.data["tenants"]["new"]["site_key"] == "pk_demo"
    assert set(db.data["conversations"]) == {
        "demo__c1", "demo__c2", "other__c1", "demox__c1", "new__c1", "new__c2"}
    # Contents kept, including the retention stamp; only the tenant changes.
    assert db.data["conversations"]["new__c1"] == {
        "tenant_id": "new", "summary": "s", "expire_at": "2027-10-18"}
    assert db.data["leads"]["new__c1"]["email"] == "a@example.com"
    assert "new__c1__r1" in db.data["feedback"]
    assert db.data["token_usage"]["new__2026-10-18"]["tokens"] == 5
    # The old tenant is left for delete_tenant (step 4).
    assert "demo" in db.data["tenants"]


def test_a_new_site_key_can_be_given():
    db = sample()
    run(db, "demo", "new", "--site-key", "pk_new_001", "--copy", "--confirm", "new")

    assert db.data["tenants"]["new"]["site_key"] == "pk_new_001"


def test_refuses_without_matching_confirm(capsys):
    with pytest.raises(SystemExit):
        run(sample(), "demo", "new", "--copy", "--confirm", "demo")
    assert "Refusing" in capsys.readouterr().out


def test_refuses_to_copy_onto_an_existing_tenant(capsys):
    db = sample()
    with pytest.raises(SystemExit):
        run(db, "demo", "other", "--copy", "--confirm", "other")

    assert "already has data" in capsys.readouterr().out
    assert db.data["tenants"]["other"]["site_key"] == "pk_other"


def test_copied_and_rebuilt_cover_everything_delete_tenant_removes():
    """Nothing of the old tenant's is deleted in step 4 without being
    copied (here) or rebuilt (ingest, sync_facts)."""
    moved = set(rename_tenant.COPIED) | set(rename_tenant.REBUILT) | {"tenants"}
    assert moved == set(delete_tenant.PER_TENANT) | set(delete_tenant.BY_TENANT_ID)
