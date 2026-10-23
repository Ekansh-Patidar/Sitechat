"""The shape of a tenant.

A tenant is one customer: one business (the first is a yoga school) with
one website. This model is the single definition of what a tenant holds.
Pydantic validates it, so bad data is rejected when it is written, not discovered later at request time.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, HttpUrl


class Wording(BaseModel):
    """How the bot talks about this business, in the prompt, the tools and
    the lead email. The defaults are a yoga school's, the first customer's,
    and give exactly the wording used before this existed; another kind of
    business sets its own at onboarding (see scripts/create_tenant.py).
    Each is a phrase that slots into a sentence, so keep the same form."""

    # "Help only with questions about <name>: <topics>."
    topics: str = (
        "its courses and retreats, staying there, getting there, and booking"
    )
    # "The business offers several <offerings>."
    offerings: str = "courses and retreats"
    # "If the answer depends on which <offering> the visitor means ..."
    offering: str = "course or retreat"
    # "Sections about <the_business> as a whole (<general_sections>) apply
    # to every programme."
    the_business: str = "the school"
    general_sections: str = "About, Contact, Booking, Our teachers, Certification"
    # "If they are unsure which to choose, <choosing>, then suggest ..."
    choosing: str = (
        "ask about their experience, how long they can stay and where they "
        "would like to be"
    )
    # "You are speaking to <audience>."
    audience: str = "prospective students, often from other countries"
    # Asked once after the email arrives: "ask ... <plans>."
    plans: str = (
        "where they are joining from and roughly when they are hoping to come"
    )
    # "... or which <interest> interests them"; the lead field it fills.
    interest: str = "course"
    # Label in the lead email for when they plan to come.
    when_label: str = "Planning to join"


class Tenant(BaseModel):
    # Stable internal identifier, e.g. "himalaya-yoga". Never changes.
    tenant_id: str

    # Public key that appears in the customer's website HTML. Not a secret:
    # it only names the tenant. Security comes from the domain allowlist.
    site_key: str

    # Human-readable name, shown in the chat header.
    display_name: str

    # Domains allowed to use this site key, e.g. ["himalayayoga.com"].
    allowed_domains: list[str] = Field(default_factory=list)

    # WhatsApp number in international format, digits only, e.g. "919876543210".
    whatsapp_number: str = ""

    # First message the visitor sees.
    greeting: str = "Hi! Ask me anything about our courses."

    # Buttons shown before the visitor types, to make starting easy.
    suggested_questions: list[str] = Field(default_factory=list)

    # Their website, used for context and for checking the domain list.
    website_url: HttpUrl | None = None

    # Google Sheet holding this tenant's facts. Set by you during onboarding,
    # never by the customer. This is what stops an unrelated shared sheet from
    # being read: if its ID isn't here, the sync never looks at it.
    facts_sheet_id: str = ""

    # Turn a tenant off without deleting their data.
    is_active: bool = True

    # Where lead notifications go. Set during onboarding.
    team_email: str = ""

    # How soon the team usually replies, as it reads after "usually", e.g.
    # "within 24 hours" or "within a day, Monday to Saturday". Shown in the
    # chat when a visitor's contact request is received. Only what the
    # business has promised: empty leaves the timing out.
    reply_time: str = ""

    # Accent colour, so the widget can match the customer's site.
    accent_colour: str = "#1f6f5c"

    # The customer's privacy notice, linked from the widget next to the
    # "AI assistant" label. Visitors give their email in the chat, so they
    # need to be able to see how it is used.
    privacy_url: HttpUrl | None = None

    # This customer's chat limit, e.g. "300/hour", sized to their traffic.
    # Empty uses rate_limit_per_tenant from config.py. Per instance, like
    # every limit here (see config.py).
    rate_limit: str = Field(
        default="", pattern=r"^$|^\d+/(second|minute|hour|day)$"
    )

    # Model tokens this tenant's replies may use per day, sized to their
    # traffic like rate_limit. 0 uses daily_token_budget from config.py.
    daily_token_budget: int = Field(default=0, ge=0)

    # How the bot describes the business; defaults suit a yoga school.
    wording: Wording = Field(default_factory=Wording)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )