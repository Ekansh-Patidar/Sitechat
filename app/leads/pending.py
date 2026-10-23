"""Things the business still needs to act on for one conversation.

Two purposes, and they are worth keeping distinct.

First, memory for the bot. The model loses track of an intention when the
visitor changes subject: it decides to contact the team, gets asked
something else, and never returns to it. Holding outstanding items on the
server means the orchestrator can remind it rather than hoping.

Second, deciding what goes in an email. A returning visitor should generate
a second email containing only what is new, and unsent items are exactly
that set.

What is not an item: the visitor's country, their intended month, the
course they asked about. Those are lead fields. They travel with whatever
email goes out, but they never cause one.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ItemKind(str, Enum):
    # The visitor asked to speak to someone.
    CONTACT_REQUEST = "contact_request"
    # The bot could not answer something that mattered to them.
    UNANSWERED = "unanswered"


class PendingItem(BaseModel):
    kind: ItemKind

    # What the visitor actually asked, in their words where possible.
    detail: str

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Set when the team has this item: included in an email, or folded into
    # one sent minutes earlier when it added nothing new (see
    # lead_email_cooldown_minutes in config.py).
    sent_at: datetime | None = None

    # Set when an email carrying this item failed and retries are off
    # (retry_failed_lead_emails in config.py). Such an item is not sent
    # again; clear this to send it with the next email.
    failed_at: datetime | None = None

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None

    @property
    def is_waiting(self) -> bool:
        """Still to go out: not sent, and not given up on."""
        return self.sent_at is None and self.failed_at is None