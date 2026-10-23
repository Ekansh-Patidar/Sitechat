"""Where leads are stored.

Keyed by tenant and conversation, so one visitor has one lead record that
fills up over the conversation rather than fragmenting into several.

Updates merge: a later message adding a phone number must not wipe the email
captured earlier.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from google.cloud import firestore

from app.retention import with_expiry
from app.leads.models import Lead
from app.leads.pending import PendingItem

logger = logging.getLogger(__name__)

COLLECTION = "leads"

# Lead emails sent per tenant per day (UTC), for the daily cap.
EMAIL_COUNTS = "lead_email_counts"


class LeadStore(ABC):
    @abstractmethod
    def get(self, tenant_id: str, conversation_id: str) -> Lead | None:
        raise NotImplementedError

    @abstractmethod
    def since(self, tenant_id: str, when: datetime) -> list[Lead]:
        """This tenant's leads updated at or after `when`, newest first.
        For reports (scripts/leads.py), not the reply path."""
        raise NotImplementedError

    @abstractmethod
    def upsert_fields(
        self, tenant_id: str, conversation_id: str, fields: dict
    ) -> Lead:
        """Merge non-empty fields into the lead, creating it if needed."""
        raise NotImplementedError

    @abstractmethod
    def add_item(
        self, tenant_id: str, conversation_id: str, item: PendingItem
    ) -> Lead:
        """Append an outstanding item, creating the lead if needed."""
        raise NotImplementedError

    @abstractmethod
    def mark_items_sent(
        self,
        tenant_id: str,
        conversation_id: str,
        items: list[PendingItem],
        emailed: bool = True,
    ) -> None:
        """Mark these items as sent, and record the notification time.

        Only the items passed in: the email goes out in the background, and
        an item added while it was being sent was not in it. emailed=False
        is for items folded into an earlier email without sending a new one;
        it leaves last_notified_at as the time of that email.
        """
        raise NotImplementedError

    @abstractmethod
    def mark_items_failed(
        self, tenant_id: str, conversation_id: str, items: list[PendingItem]
    ) -> None:
        """Mark these items as failed, so they are not sent again."""
        raise NotImplementedError

    @abstractmethod
    def emails_sent_on(self, tenant_id: str, day: str) -> int:
        """How many lead emails this tenant was sent on this UTC day
        ("2026-10-17")."""
        raise NotImplementedError

    @abstractmethod
    def count_email_sent(self, tenant_id: str, day: str) -> None:
        """Add one to this tenant's lead emails for this UTC day."""
        raise NotImplementedError


def _same_item(a: PendingItem, b: PendingItem) -> bool:
    return (a.kind, a.detail, a.created_at) == (b.kind, b.detail, b.created_at)


def _mark(
    lead: Lead, items: list[PendingItem], sent: bool, emailed: bool = True
) -> None:
    """Set sent_at (or failed_at) on the waiting items that match `items`."""
    now = datetime.now(timezone.utc)
    for item in lead.items:
        if item.is_waiting and any(_same_item(item, done) for done in items):
            if sent:
                item.sent_at = now
            else:
                item.failed_at = now
    if sent and emailed:
        lead.last_notified_at = now


def _merge(lead: Lead, fields: dict) -> Lead:
    """Apply only non-empty values, so nothing already captured is lost."""
    for key, value in fields.items():
        if value and hasattr(lead, key):
            setattr(lead, key, value)
    lead.updated_at = datetime.now(timezone.utc)
    return lead


class InMemoryLeadStore(LeadStore):
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], Lead] = {}
        self._email_counts: dict[tuple[str, str], int] = {}

    def emails_sent_on(self, tenant_id: str, day: str) -> int:
        return self._email_counts.get((tenant_id, day), 0)

    def count_email_sent(self, tenant_id: str, day: str) -> None:
        key = (tenant_id, day)
        self._email_counts[key] = self._email_counts.get(key, 0) + 1

    def get(self, tenant_id: str, conversation_id: str) -> Lead | None:
        return self._data.get((tenant_id, conversation_id))

    def since(self, tenant_id: str, when: datetime) -> list[Lead]:
        found = [lead for (tenant, _), lead in self._data.items()
                 if tenant == tenant_id and lead.updated_at >= when]
        return sorted(found, key=lambda lead: lead.updated_at, reverse=True)

    def upsert_fields(
        self, tenant_id: str, conversation_id: str, fields: dict
    ) -> Lead:
        key = (tenant_id, conversation_id)
        lead = self._data.get(key) or Lead(
            tenant_id=tenant_id, conversation_id=conversation_id
        )
        self._data[key] = _merge(lead, fields)
        return self._data[key]

    def add_item(
        self, tenant_id: str, conversation_id: str, item: PendingItem
    ) -> Lead:
        key = (tenant_id, conversation_id)
        lead = self._data.get(key) or Lead(
            tenant_id=tenant_id, conversation_id=conversation_id
        )
        lead.items.append(item)
        lead.updated_at = datetime.now(timezone.utc)
        self._data[key] = lead
        return lead

    def mark_items_sent(
        self,
        tenant_id: str,
        conversation_id: str,
        items: list[PendingItem],
        emailed: bool = True,
    ) -> None:
        self._mark_items(tenant_id, conversation_id, items, True, emailed)

    def mark_items_failed(
        self, tenant_id: str, conversation_id: str, items: list[PendingItem]
    ) -> None:
        self._mark_items(tenant_id, conversation_id, items, False)

    def _mark_items(
        self, tenant_id, conversation_id, items, sent: bool, emailed: bool = True
    ) -> None:
        lead = self._data.get((tenant_id, conversation_id))
        if lead:
            _mark(lead, items, sent, emailed)


class FirestoreLeadStore(LeadStore):
    def __init__(self, project_id: str) -> None:
        self._db = firestore.Client(project=project_id)

    def emails_sent_on(self, tenant_id: str, day: str) -> int:
        doc = self._db.collection(EMAIL_COUNTS).document(f"{tenant_id}__{day}").get()
        return int((doc.to_dict() or {}).get("count", 0)) if doc.exists else 0

    def count_email_sent(self, tenant_id: str, day: str) -> None:
        # Increment is applied by the server, so two sends at once both count.
        self._db.collection(EMAIL_COUNTS).document(f"{tenant_id}__{day}").set(
            {"tenant_id": tenant_id, "day": day, "count": firestore.Increment(1)},
            merge=True,
        )

    def _doc_id(self, tenant_id: str, conversation_id: str) -> str:
        return f"{tenant_id}__{conversation_id}"

    def get(self, tenant_id: str, conversation_id: str) -> Lead | None:
        doc = (
            self._db.collection(COLLECTION)
            .document(self._doc_id(tenant_id, conversation_id))
            .get()
        )
        if not doc.exists:
            return None
        return Lead(**(doc.to_dict() or {}))

    def since(self, tenant_id: str, when: datetime) -> list[Lead]:
        # A single-field equality filter (automatic index); the date is
        # checked here, so no composite index is needed.
        query = self._db.collection(COLLECTION).where(
            filter=firestore.FieldFilter("tenant_id", "==", tenant_id)
        )
        found = [Lead(**(d.to_dict() or {})) for d in query.stream()]
        found = [lead for lead in found if lead.updated_at >= when]
        return sorted(found, key=lambda lead: lead.updated_at, reverse=True)

    def upsert_fields(
        self, tenant_id: str, conversation_id: str, fields: dict
    ) -> Lead:
        doc_ref = self._db.collection(COLLECTION).document(
            self._doc_id(tenant_id, conversation_id)
        )

        @firestore.transactional
        def update_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            lead = (
                Lead(**(snapshot.to_dict() or {}))
                if snapshot.exists
                else Lead(
                    tenant_id=tenant_id, conversation_id=conversation_id
                )
            )
            lead = _merge(lead, fields)
            transaction.set(doc_ref, with_expiry(lead.model_dump(mode="json")))
            return lead

        return update_in_transaction(self._db.transaction())

    def add_item(
        self, tenant_id: str, conversation_id: str, item: PendingItem
    ) -> Lead:
        doc_ref = self._db.collection(COLLECTION).document(
            self._doc_id(tenant_id, conversation_id)
        )

        @firestore.transactional
        def update_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            lead = (
                Lead(**(snapshot.to_dict() or {}))
                if snapshot.exists
                else Lead(
                    tenant_id=tenant_id, conversation_id=conversation_id
                )
            )
            lead.items.append(item)
            lead.updated_at = datetime.now(timezone.utc)
            transaction.set(doc_ref, with_expiry(lead.model_dump(mode="json")))
            return lead

        return update_in_transaction(self._db.transaction())

    def mark_items_sent(
        self,
        tenant_id: str,
        conversation_id: str,
        items: list[PendingItem],
        emailed: bool = True,
    ) -> None:
        self._mark_items(tenant_id, conversation_id, items, True, emailed)

    def mark_items_failed(
        self, tenant_id: str, conversation_id: str, items: list[PendingItem]
    ) -> None:
        self._mark_items(tenant_id, conversation_id, items, False)

    def _mark_items(
        self, tenant_id, conversation_id, items, sent: bool, emailed: bool = True
    ) -> None:
        doc_ref = self._db.collection(COLLECTION).document(
            self._doc_id(tenant_id, conversation_id)
        )

        @firestore.transactional
        def update_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                return
            lead = Lead(**(snapshot.to_dict() or {}))
            _mark(lead, items, sent, emailed)
            transaction.set(doc_ref, with_expiry(lead.model_dump(mode="json")))

        update_in_transaction(self._db.transaction())


class FailSoftLeadStore:
    """A lead store for the reply path, where answering comes first.

    Leads are secondary to the reply: if the lead store is down, the visitor
    should still get an answer (backlog 97). Every call that raises is
    logged as an error (Sentry reports it) and returns None, which callers
    treat as "no lead". The background step after the reply uses the real
    store, so its failures stay visible there.
    """

    def __init__(self, store: LeadStore) -> None:
        self._store = store

    def __getattr__(self, name):
        method = getattr(self._store, name)

        def call(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            except Exception:
                logger.exception("Lead store %s failed; answering without it", name)
                return None

        return call


def fail_soft(store: LeadStore | None):
    if store is None or isinstance(store, FailSoftLeadStore):
        return store
    return FailSoftLeadStore(store)
