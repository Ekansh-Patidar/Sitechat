"""The shapes of what goes in and out of the public API.

Pydantic validates incoming requests against these models. Anything
malformed is rejected with a clear error before our code runs.

ChatResponse carries more than just text. Keeping extra optional fields
here from the start means adding buttons later is an addition, not a
redesign. Sources are not sent: visitors are not shown them, and the
retrieved chunk ids are logged instead.
"""

from pydantic import BaseModel, Field, field_validator

from app.feedback.models import Rating, Reason

from app.agent.sanitize import strip_invisible


class ChatRequest(BaseModel):
    # The visitor's message. Capped so one request can't send a huge payload.
    message: str = Field(min_length=1, max_length=2000)

    # Invisible characters can hide instructions for the model; see
    # app/agent/sanitize.py. A message of nothing else is refused.
    @field_validator("message")
    @classmethod
    def _visible_only(cls, value: str) -> str:
        value = strip_invisible(value)
        if not value.strip():
            raise ValueError("Message is empty")
        return value

    # Identifies this visitor's conversation. Issued by the server (a uuid4)
    # on the first reply; the widget stores it and sends it back. It becomes
    # part of a Firestore document id, so the pattern keeps out "/" (which
    # breaks the path, a 500) and "__" (the tenant/conversation separator).
    conversation_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9-]{1,64}$"
    )


class Action(BaseModel):
    """A button shown under the reply."""
    label: str
    type: str          # e.g. "link", "whatsapp", "question"
    value: str         # URL, phone number, or question text


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    actions: list[Action] = Field(default_factory=list)
    # Set on the turn a contact request is received: shown as a card under
    # the reply ("Request received. <business> will reply to ...").
    confirmation: str | None = None
    # Names this reply, for thumbs up/down (POST /feedback).
    reply_id: str = ""


class FeedbackRequest(BaseModel):
    """A thumbs up or down on one reply. Sending again replaces it."""
    conversation_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    reply_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    rating: Rating
    reason: Reason | None = None


class WidgetConfig(BaseModel):
    """Public settings the widget needs to render itself."""
    display_name: str
    greeting: str
    suggested_questions: list[str]
    accent_colour: str
    privacy_url: str | None = None