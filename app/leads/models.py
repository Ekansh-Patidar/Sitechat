"""A lead: the contact details and context for one visitor.

Stored separately from the conversation, deliberately. Conversations are
operational data; leads are personal data, and keeping them apart means they
can have their own access rules and retention period without untangling them
later.

Every field is optional except the ids. A lead with only an email is still a
lead, and holding out for a complete record would mean losing most of them.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from app.leads.pending import PendingItem


class Lead(BaseModel):
    tenant_id: str
    conversation_id: str

    # Contact details. Either one is enough to be useful.
    email: str = ""
    phone: str = ""

    # Enrichment, gathered from conversation or asked once.
    name: str = ""
    country: str = ""
    intended_month: str = ""
    course_interest: str = ""
    # Outstanding things the business needs to act on. See pending.py.
    items: list[PendingItem] = Field(default_factory=list)

    # When the team was last emailed about this conversation. Unlike the
    # earlier notified_at, this does not block further emails: whether to
    # send is decided by unsent items, not by whether we have ever sent.
    last_notified_at: datetime | None = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def has_contact(self) -> bool:
        return bool(self.email or self.phone)

    @property
    def unsent_items(self) -> list[PendingItem]:
        """Items still to go out. Excludes items whose send failed."""
        return [item for item in self.items if item.is_waiting]

    @property
    def has_unsent_items(self) -> bool:
        return bool(self.unsent_items)


class AskedQuestion(BaseModel):
    """One visitor question as it appears in the lead email."""

    text: str
    # "not_answered", "partly", "answered", "comment" (not a question), or
    # "unchecked" when the model gave no valid verdict for it.
    status: str = "unchecked"
