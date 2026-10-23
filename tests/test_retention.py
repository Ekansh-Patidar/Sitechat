"""Conversations and leads carry an expiry date for Firestore's TTL policy
(backlog 107)."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.retention import FIELD, with_expiry

ROOT = Path(__file__).resolve().parent.parent


def test_expiry_is_a_real_timestamp_retention_days_ahead():
    data = with_expiry({"tenant_id": "t1"})
    expected = datetime.now(timezone.utc) + timedelta(days=get_settings().retention_days)
    assert isinstance(data[FIELD], datetime)  # a string would never expire
    assert abs(data[FIELD] - expected) < timedelta(seconds=5)


def test_zero_days_keeps_data(monkeypatch):
    monkeypatch.setattr(get_settings(), "retention_days", 0)
    assert FIELD not in with_expiry({"tenant_id": "t1"})


def test_every_full_write_of_a_conversation_or_lead_sets_the_expiry():
    """A full set() replaces the document, expire_at included, so each one
    must add it back, or that document would never be deleted."""
    for path in ("app/conversations/store.py", "app/leads/store.py"):
        source = (ROOT / path).read_text(encoding="utf-8")
        writes = re.findall(r"transaction\.set\(doc_ref, (.*)\)", source)
        assert writes, path
        assert all(w.startswith("with_expiry(") for w in writes), (path, writes)
