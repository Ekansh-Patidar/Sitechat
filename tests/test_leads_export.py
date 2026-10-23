"""scripts/leads.py: the lead list as CSV rows."""

import csv
from datetime import datetime, timedelta, timezone

from app.conversations.store import InMemoryConversationStore
from app.leads.pending import ItemKind, PendingItem
from app.leads.store import InMemoryLeadStore
from scripts.leads import COLUMNS, rows

LONG_AGO = datetime(2000, 1, 1, tzinfo=timezone.utc)


def stores():
    leads, convos = InMemoryLeadStore(), InMemoryConversationStore()
    leads.upsert_fields("t1", "c1", {"email": "sam@example.com", "name": "Sam",
                                     "country": "Spain", "course_interest": "300-hour"})
    leads.add_item("t1", "c1", PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="x"))
    convos.append_turn("t1", "c1", "How much is the 300-hour course?", "It is ...",
                       is_question=True)
    leads.upsert_fields("t1", "c2", {"name": "No contact details"})
    leads.upsert_fields("t2", "c3", {"email": "other@example.com"})
    return leads, convos


def test_a_contactable_lead_becomes_one_row():
    leads, convos = stores()

    found = list(rows(leads, convos, "t1", LONG_AGO))

    assert len(found) == 1
    row = dict(zip(COLUMNS, found[0]))
    assert row["email"] == "sam@example.com"
    assert row["name"] == "Sam"
    assert row["interested in"] == "300-hour"
    assert row["asked to be contacted"] == "yes"
    assert row["emailed to team (UTC)"] == ""
    assert row["questions"] == "How much is the 300-hour course?"
    assert row["conversation id"] == "c1"


def test_leads_without_contact_details_only_with_all():
    leads, convos = stores()

    names = [r[2] for r in rows(leads, convos, "t1", LONG_AGO, everyone=True)]

    assert sorted(names) == ["No contact details", "Sam"]


def test_only_this_tenant_and_only_recent():
    leads, convos = stores()

    assert all(r[3] != "other@example.com" for r in rows(leads, convos, "t1", LONG_AGO, True))
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert list(rows(leads, convos, "t1", future)) == []


def test_written_file_opens_in_excel_with_accents(tmp_path):
    """UTF-8 with a byte-order mark, as main() writes it."""
    leads, convos = InMemoryLeadStore(), InMemoryConversationStore()
    leads.upsert_fields("t1", "c1", {"email": "jose@example.com", "name": "José"})
    path = tmp_path / "leads.csv"

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows(leads, convos, "t1", LONG_AGO))

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "José" in path.read_text(encoding="utf-8-sig")
