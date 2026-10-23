"""Tests for the sheet reader and the sync diff.

The Google API is mocked, so these need no network and no credentials.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.facts.models import Fact, FactSheet
from app.facts.sheets import SheetError, read_facts
from app.facts.store import InMemoryFactStore
from app.facts.sync import sync_tenant_facts


def mock_sheet(rows):
    """Build a fake Sheets API service returning the given rows."""
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": rows
    }
    return service


HEADER = ["Category", "Label", "Value"]


@patch("app.facts.sheets._get_service")
def test_reads_valid_rows(mock_service):
    mock_service.return_value = mock_sheet([
        HEADER,
        ["Fees", "Course fee", "USD 1,450"],
        ["Dates", "Next course", "2 March 2027"],
    ])

    facts, problems = read_facts("sheet-1")

    assert len(facts) == 2
    assert problems == []
    assert facts[0].value == "USD 1,450"


@patch("app.facts.sheets._get_service")
def test_rejects_wrong_headers(mock_service):
    mock_service.return_value = mock_sheet([
        ["Thing", "Other", "Stuff"],
        ["Fees", "Course fee", "USD 1,450"],
    ])

    with pytest.raises(SheetError):
        read_facts("sheet-1")


@patch("app.facts.sheets._get_service")
def test_row_with_no_value_is_skipped_and_reported(mock_service):
    """A fact with an empty value must never reach the model."""
    mock_service.return_value = mock_sheet([
        HEADER,
        ["Fees", "Course fee", ""],
        ["Dates", "Next course", "2 March 2027"],
    ])

    facts, problems = read_facts("sheet-1")

    assert len(facts) == 1
    assert len(problems) == 1
    assert "Course fee" in problems[0]


@patch("app.facts.sheets._get_service")
def test_blank_rows_are_ignored_silently(mock_service):
    mock_service.return_value = mock_sheet([
        HEADER,
        ["Fees", "Course fee", "USD 1,450"],
        [],
        ["", "", ""],
    ])

    facts, problems = read_facts("sheet-1")

    assert len(facts) == 1
    assert problems == []


@patch("app.facts.sheets._get_service")
def test_sync_detects_a_price_change(mock_service):
    """The change log is what makes a wrong price traceable."""
    store = InMemoryFactStore()
    store.save(
        FactSheet(
            tenant_id="t1",
            facts=[Fact(category="Fees", label="Course fee", value="USD 1,450")],
        )
    )

    mock_service.return_value = mock_sheet([
        HEADER,
        ["Fees", "Course fee", "USD 1,600"],
    ])

    result = sync_tenant_facts("t1", "sheet-1", store)

    assert len(result.changed) == 1
    assert "USD 1,450" in result.changed[0]
    assert "USD 1,600" in result.changed[0]


@patch("app.facts.sheets._get_service")
def test_sync_detects_added_and_removed(mock_service):
    store = InMemoryFactStore()
    store.save(
        FactSheet(
            tenant_id="t1",
            facts=[Fact(category="Fees", label="Old fact", value="gone soon")],
        )
    )

    mock_service.return_value = mock_sheet([
        HEADER,
        ["Fees", "New fact", "brand new"],
    ])

    result = sync_tenant_facts("t1", "sheet-1", store)

    assert result.added == ["Fees / New fact: brand new"]
    assert result.removed == ["Fees / Old fact"]


@patch("app.facts.sheets._get_service")
def test_same_label_under_different_categories_is_not_a_change(mock_service):
    """"Private room" exists once per course; they must not be paired up."""
    store = InMemoryFactStore()
    store.save(
        FactSheet(
            tenant_id="t1",
            facts=[Fact(category="Fees: 200-hour", label="Private room", value="US $899")],
        )
    )

    mock_service.return_value = mock_sheet([
        HEADER,
        ["Fees: 200-hour", "Private room", "US $899"],
        ["Fees: Mysore", "Private room", "€1232"],
    ])

    result = sync_tenant_facts("t1", "sheet-1", store)

    assert result.changed == []
    assert result.added == ["Fees: Mysore / Private room: €1232"]

# --- the scheduled sync (backlog 65) -----------------------------------------

from datetime import datetime, timedelta, timezone

from app.facts.sync import _attempts, sync_stale_facts
from app.tenants.models import Tenant
from app.tenants.store import InMemoryTenantStore

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
ROWS = [HEADER, ["Fees", "Course fee", "USD 1,450"], ["Dates", "Next course", "2 March"],
        ["Fees", "Deposit", "USD 200"]]


def tenant_store(*tenants):
    store = InMemoryTenantStore()
    for tenant in tenants:
        store.save(tenant)
    return store


def school(tenant_id="t1", sheet="sheet-1", active=True):
    return Tenant(tenant_id=tenant_id, site_key=f"pk_{tenant_id}", display_name="S",
                  facts_sheet_id=sheet, is_active=active)


def facts_from(age_hours, count=3):
    sheet = FactSheet(tenant_id="t1", facts=[
        Fact(category="Fees", label=f"Item {i}", value="x") for i in range(count)
    ])
    sheet.updated_at = NOW - timedelta(hours=age_hours)
    return sheet


@pytest.fixture(autouse=True)
def fresh_attempts():
    _attempts.clear()
    yield
    _attempts.clear()


@patch("app.facts.sheets._get_service")
def test_day_old_facts_are_synced_and_fresh_ones_left(mock_service):
    mock_service.return_value = mock_sheet(ROWS)
    facts = InMemoryFactStore()
    old = facts_from(age_hours=25)
    old.facts = [Fact(category="Fees", label="Course fee", value="USD 1,300")]
    facts.save(old)

    counts = sync_stale_facts(tenant_store(school()), facts, NOW)

    assert counts == {"synced": 1, "held": 0, "failed": 0}
    assert {f.label for f in facts.get("t1").facts} == {"Course fee", "Next course", "Deposit"}
    assert facts.get("t1").facts[0].value == "USD 1,450"

    # Now fresh: the next sweep leaves it alone.
    mock_service.reset_mock()
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    _attempts.clear()
    assert sync_stale_facts(tenant_store(school()), facts, later)["synced"] == 0
    mock_service.assert_not_called()


@patch("app.facts.sheets._get_service")
def test_tenants_without_a_sheet_or_inactive_are_skipped(mock_service):
    mock_service.return_value = mock_sheet(ROWS)

    counts = sync_stale_facts(
        tenant_store(school("a", sheet=""), school("b", active=False)),
        InMemoryFactStore(), NOW,
    )

    assert counts == {"synced": 0, "held": 0, "failed": 0}
    mock_service.assert_not_called()


@patch("app.facts.sheets._get_service")
def test_an_emptied_sheet_is_held_and_the_old_facts_kept(mock_service):
    mock_service.return_value = mock_sheet([HEADER])  # every row deleted
    facts = InMemoryFactStore()
    facts.save(facts_from(age_hours=30, count=3))

    counts = sync_stale_facts(tenant_store(school()), facts, NOW)

    assert counts["held"] == 1
    assert len(facts.get("t1").facts) == 3


@patch("app.facts.sheets._get_service")
def test_losing_more_than_half_is_held_but_a_by_hand_sync_accepts_it(mock_service):
    mock_service.return_value = mock_sheet([HEADER, ["Fees", "Item 0", "x"]])
    facts = InMemoryFactStore()
    facts.save(facts_from(age_hours=30, count=3))

    assert sync_tenant_facts("t1", "sheet-1", facts, careful=True).held
    assert len(facts.get("t1").facts) == 3

    sync_tenant_facts("t1", "sheet-1", facts)  # scripts.sync_facts
    assert len(facts.get("t1").facts) == 1


@patch("app.facts.sheets._get_service", side_effect=PermissionError("not shared"))
def test_an_unreadable_sheet_is_retried_hourly_not_every_sweep(mock_service):
    tenants, facts = tenant_store(school()), InMemoryFactStore()

    assert sync_stale_facts(tenants, facts, NOW)["failed"] == 1
    assert sync_stale_facts(tenants, facts, NOW + timedelta(minutes=5))["failed"] == 0
    assert sync_stale_facts(tenants, facts, NOW + timedelta(minutes=61))["failed"] == 1
    assert mock_service.call_count == 2


@patch("app.facts.sheets._get_service")
def test_a_few_tenants_per_sweep(mock_service, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "facts_sync_batch", 2)
    mock_service.return_value = mock_sheet(ROWS)
    tenants = tenant_store(*(school(f"t{i}") for i in range(5)))
    facts = InMemoryFactStore()

    assert sync_stale_facts(tenants, facts, NOW)["synced"] == 2
    assert sync_stale_facts(tenants, facts, NOW)["synced"] == 2
    assert sync_stale_facts(tenants, facts, NOW)["synced"] == 1


def test_listing_tenants_failing_does_not_raise():
    broken = MagicMock()
    broken.with_facts_sheet.side_effect = RuntimeError("firestore down")

    assert sync_stale_facts(broken, InMemoryFactStore(), NOW) == {
        "synced": 0, "held": 0, "failed": 0}


def test_without_the_key_file_the_services_own_account_reads_the_sheet(monkeypatch):
    """Cloud Run has no service-account.json: it uses sitechat-runtime."""
    from app.config import get_settings
    from app.facts import sheets

    monkeypatch.setattr(get_settings(), "google_service_account_file", "no-such-file.json")
    with patch("app.facts.sheets.google.auth.default",
               return_value=("runtime-creds", "project")) as default, \
         patch("app.facts.sheets.build") as build:
        sheets._get_service()

    assert default.call_args.kwargs["scopes"] == sheets.SCOPES
    assert build.call_args.kwargs["credentials"] == "runtime-creds"
