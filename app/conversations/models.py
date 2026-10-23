"""The shape of a stored conversation.

Every conversation belongs to exactly one tenant. That field is the data
boundary this product depends on, so it is required, not optional.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str          # "user" or "assistant"
    content: str
    # An assistant reply's id, as /chat returned it; what visitor feedback
    # (app/feedback) refers to. Empty for visitor messages and older replies.
    id: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Question(BaseModel):
    """Something the visitor asked, and what the assistant said back.

    Recorded as each turn is stored, so the lead email can list every
    question without asking a model to remember or find them.
    """

    text: str          # the visitor's message, contact details removed
    reply: str         # the assistant's reply, cut to REPLY_LIMIT
    asked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    # The reply's id (Message.id), so feedback on it can be matched to the
    # question after older messages are summarised away.
    reply_id: str = ""
    # Set when the question is emailed: "answered", "partly",
    # "not_answered", "comment" or "unchecked". Kept so reports on what the
    # bot could not answer need no further model calls. Empty until then.
    status: str = ""


# Enough of the reply to tell whether it answered the question.
REPLY_LIMIT = 500


class Conversation(BaseModel):
    conversation_id: str
    tenant_id: str
    messages: list[Message] = Field(default_factory=list)
    summary: str = ""

    # Every question asked, in order. Unlike messages, never summarised or
    # trimmed: the lead email is built from this.
    questions: list[Question] = Field(default_factory=list)
    # How many of `questions` have gone out in lead emails, so a later
    # email lists only the new ones.
    questions_emailed: int = 0

    # The programme(s) the conversation is about, so a follow-up like "how
    # much does it cost?" stays on the course named before. See
    # app/programmes/focus.py.
    focus: list[str] = Field(default_factory=list)

    # Set when the visitor has given contact details and there are
    # questions not yet emailed; cleared once they are sent. The lead
    # digest sweep looks only at conversations with this set, so its cost
    # does not grow with every conversation ever stored.
    digest_due: bool = False

    # When a digest sweep took this conversation to email it. Another sweep
    # running at the same time (a manual run during a scheduled one) skips
    # it until the claim is released or lead_digest_claim_minutes pass, so
    # one email is not sent twice. Cleared with digest_due.
    digest_claimed_at: datetime | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )