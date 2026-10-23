"""Tests for the tenant deletion script, against an in-memory fake Firestore.

What matters: everything of the tenant's is found (by field and by id
prefix), nothing of another tenant's is, and nothing is deleted without
--delete and a matching --confirm.
"""

from unittest.mock import patch

import pytest

from scripts import delete_tenant


class Ref:
    def __init__(self, db, collection, doc_id):
        self.db, self.collection, self.id = db, collection, doc_id


class Snapshot:
    def __init__(self, db, collection, doc_id):
        self.id = doc_id
        self.reference = Ref(db, collection, doc_id)
        self.exists = doc_id in db.data.get(collection, {})


class Query:
    def __init__(self, docs):
        self.docs = docs

    def stream(self):
        return self.docs


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def _all(self):
        return self.db.data.get(self.name, {})

    def stream(self):
        return [Snapshot(self.db, self.name, i) for i in self._all()]

    def where(self, filter):
        return Query(
            [
                Snapshot(self.db, self.name, i)
                for i, fields in self._all().items()
                if fields.get(filter.field_path) == filter.value
            ]
        )

    def document(self, doc_id):
        db, name = self.db, self.name

        class Doc:
            def get(self):
                return Snapshot(db, name, doc_id)

        return Doc()


class Batch:
    def __init__(self, db):
        self.db, self.refs = db, []

    def delete(self, ref):
        self.refs.append(ref)

    def commit(self):
        for ref in self.refs:
            self.db.data[ref.collection].pop(ref.id, None)


class FakeDB:
    def __init__(self, data):
        self.data = data

    def collection(self, name):
        return Collection(self, name)

    def batch(self):
        return Batch(self)


def sample():
    return FakeDB(
        {
            "tenants": {"t1": {}, "t2": {}},
            "facts": {"t1": {}},
            "conversations": {
                "t1__a": {"tenant_id": "t1"},
                "t1__b": {},                      # no tenant_id field
                "t2__a": {"tenant_id": "t2"},
                "t10__x": {"tenant_id": "t10"},   # prefix must not match t1
            },
            "leads": {"t1__a": {"tenant_id": "t1"}, "t2__a": {"tenant_id": "t2"}},
            "documents": {"t1__doc": {"tenant_id": "t1"}},
            "chunks": {"t1__doc__0": {"tenant_id": "t1"}, "t2__doc__0": {"tenant_id": "t2"}},
            "programmes": {"t1": {}, "t2": {}},
            "feedback": {
                "t1__a__r1": {"tenant_id": "t1"},
                "t1__b__r2": {"tenant_id": "t1"},
                "t2__a__r1": {"tenant_id": "t2"},
            },
            "token_usage": {"t1__2026-10-18": {"tenant_id": "t1"}},
            "lead_email_counts": {"t1__2026-10-18": {"tenant_id": "t1"},
                                  "t2__2026-10-18": {"tenant_id": "t2"}},
        }
    )


def ids(found):
    return {name: sorted(r.id for r in refs) for name, refs in found.items()}


def test_finds_everything_of_the_tenant_and_nothing_else():
    found = delete_tenant.find_tenant_documents(sample(), "t1")
    assert ids(found) == {
        "conversations": ["t1__a", "t1__b"],
        "leads": ["t1__a"],
        "documents": ["t1__doc"],
        "chunks": ["t1__doc__0"],
        "feedback": ["t1__a__r1", "t1__b__r2"],
        "token_usage": ["t1__2026-10-18"],
        "lead_email_counts": ["t1__2026-10-18"],
        "tenants": ["t1"],
        "facts": ["t1"],
        "programmes": ["t1"],
    }


def test_one_conversation_and_its_lead():
    found = delete_tenant.find_conversation_documents(sample(), "t1", "a")
    assert ids(found) == {"conversations": ["t1__a"], "leads": ["t1__a"],
                          "feedback": ["t1__a__r1"]}


def test_delete_removes_only_what_was_found():
    db = sample()
    found = delete_tenant.find_tenant_documents(db, "t1")
    for refs in found.values():
        delete_tenant.delete_references(db, refs)

    assert set(db.data["conversations"]) == {"t2__a", "t10__x"}
    assert set(db.data["tenants"]) == {"t2"}
    assert set(db.data["chunks"]) == {"t2__doc__0"}


def test_delete_without_matching_confirm_is_refused(capsys):
    with pytest.raises(SystemExit):
        delete_tenant.main(["t1", "--delete", "--confirm", "t2"])
    assert "Refusing" in capsys.readouterr().out


def test_dry_run_deletes_nothing(capsys):
    db = sample()
    with patch.object(delete_tenant.firestore, "Client", return_value=db):
        delete_tenant.main(["t1", "--conversation", "a"])

    assert "t1__a" in db.data["conversations"]
    assert "Dry run" in capsys.readouterr().out


def test_namespace_matches_the_vector_store():
    from app.retrieval.vector_store import PineconeVectorStore

    assert delete_tenant.pinecone_namespace("t1") == PineconeVectorStore._namespace(
        None, "t1"
    )


def test_every_collection_the_app_writes_is_covered():
    """A new store's collection must be added to delete_tenant, or deleting
    a tenant (or a visitor's data) silently leaves it behind."""
    import re
    from pathlib import Path

    written = set()
    for path in Path("app").rglob("*.py"):
        # Stores name their collections in constants: COLLECTION,
        # EMAIL_COUNTS, CHUNKS, DOCUMENTS.
        written |= set(re.findall(
            r'^(?:COLLECTION|[A-Z_]*COUNTS|CHUNKS|DOCUMENTS) = "([a-z_]+)"$',
            path.read_text(encoding="utf-8"), re.M,
        ))
    assert written >= {"conversations", "leads", "feedback", "token_usage",
                       "lead_email_counts", "programmes", "chunks"}
    covered = set(delete_tenant.PER_TENANT) | set(delete_tenant.BY_TENANT_ID)
    assert written <= covered, written - covered
