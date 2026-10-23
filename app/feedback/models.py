"""A visitor's thumbs up or down on one reply."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Rating = Literal["up", "down"]
# Offered after a thumbs down; optional.
Reason = Literal["wrong", "not_answered", "other"]


class Feedback(BaseModel):
    tenant_id: str
    conversation_id: str
    reply_id: str      # Message.id / Question.reply_id of the rated reply
    rating: Rating
    reason: Reason | None = None
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
