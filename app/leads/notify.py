"""Sends the lead to the team.

Only to the team. The visitor's address is the Reply-To, so the team's
reply reaches them, but we never send to it ourselves: anyone can type
anyone's address into a chat, and copying it in would let a stranger make
us email someone, with text they wrote. The email carries a suggested reply
(reply_draft.py) for the team to finish and send from their own address.

The summary matters more than the transcript. A team member wants to know
who this is and what they want, not to read twenty messages. It is written
by the agent layer and arrives here as text, so this module only sends.
"""

import logging
from collections.abc import Sequence

import resend

from app.config import get_settings
from app.leads.email_html import (
    STATUS_LABELS,
    questions_for_email,
    render_lead_email,
    request_note,
)
from app.leads.extraction import looks_like_throwaway
from app.leads.links import LINK_CAUTION, defang, has_link
from app.leads.models import AskedQuestion, Lead
from app.leads.pending import ItemKind
from app.leads.reply_draft import draft_body
from app.tenants.models import Tenant

logger = logging.getLogger(__name__)


def _format_body(
    lead: Lead,
    tenant: Tenant,
    summary: str,
    questions: Sequence[AskedQuestion] = (),
    links: bool = False,
) -> str:
    rows = [
        (
            "Email",
            f"{lead.email} (throwaway address, may not be real)"
            if lead.email and looks_like_throwaway(lead.email)
            else lead.email,
        ),
        ("Phone", lead.phone),
        ("Name", lead.name),
        ("Country", lead.country),
        (tenant.wording.when_label, lead.intended_month),
        ("Interested in", lead.course_interest),
    ]
    details = "\n".join(f"{label}: {value}" for label, value in rows if value)

    # As in the HTML version: the request's reason is not listed, since
    # "What they asked" is the complete list.
    requested = any(
        item.kind == ItemKind.CONTACT_REQUEST for item in lead.unsent_items
    )
    request_line = f"{request_note(tenant)}\n\n" if requested else ""

    asked = questions_for_email(lead, questions)
    asked_lines = "\n".join(
        f"- [{STATUS_LABELS[q.status]}] {q.text}" for q in asked
    )
    asked_section = f"\nWhat they asked:\n{asked_lines}\n" if asked else ""

    returning = ""
    if lead.last_notified_at:
        returning = (
            "\nThis visitor has been in touch before. Only what is new since "
            "the last email is listed above.\n"
        )

    if lead.email:
        reply = (
            "Replying to this email will reach them directly.\n\n"
            "Suggested reply (fill in the answers, then send):\n\n"
            f"{draft_body(lead, tenant, questions)}"
        )
    else:
        reply = "They left a phone number but no email address."

    caution = f"{LINK_CAUTION}\n\n" if links else ""
    return (
        f"A visitor to {tenant.display_name}'s website needs a reply.\n\n"
        f"{caution}"
        f"{request_line}"
        f"{details}\n"
        f"{asked_section}"
        f"{returning}\n"
        f"What they were asking about:\n{summary}\n\n"
        f"{reply}"
    )


def can_send(tenant: Tenant) -> bool:
    """Whether a lead email for this tenant could go out at all."""
    if not get_settings().resend_api_key.get_secret_value():
        logger.warning("No Resend key configured, skipping lead email")
        return False

    if not tenant.team_email:
        logger.warning("Tenant %s has no team_email set", tenant.tenant_id)
        return False

    return True


def _without_live_links(lead: Lead, summary: str, questions):
    """Copies of what the email shows, with visitor-written links defanged
    (app/leads/links.py), and whether there were any. The contact email
    and phone are left alone: the server extracted those, not the visitor's
    free text."""
    texts = [summary, lead.name, lead.country, lead.intended_month,
             lead.course_interest, *(q.text for q in questions),
             *(i.detail for i in lead.items)]
    if not any(has_link(t) for t in texts if t):
        return lead, summary, list(questions), False
    lead = lead.model_copy(update={
        field: defang(getattr(lead, field))
        for field in ("name", "country", "intended_month", "course_interest")
    } | {"items": [i.model_copy(update={"detail": defang(i.detail)}) for i in lead.items]})
    questions = [q.model_copy(update={"text": defang(q.text)}) for q in questions]
    return lead, defang(summary), questions, True


def send_lead_email(
    lead: Lead,
    tenant: Tenant,
    summary: str,
    questions: Sequence[AskedQuestion] = (),
) -> bool:
    """Email the team. Returns True if it was sent."""
    if not can_send(tenant):
        return False

    lead, summary, questions, links = _without_live_links(lead, summary, questions)

    settings = get_settings()
    resend.api_key = settings.resend_api_key.get_secret_value()

    params: resend.Emails.SendParams = {
        "from": settings.notification_from_email,
        "to": [tenant.team_email],
        "subject": "New enquiry from your website",
        "text": _format_body(lead, tenant, summary, questions, links),
        "html": render_lead_email(lead, tenant, summary, questions, links),
    }

    # The team's reply goes to the visitor. Never "to" or "cc": see the
    # module docstring. Lowercased for leads stored before extraction did.
    if lead.email:
        params["reply_to"] = lead.email.lower()

    try:
        resend.Emails.send(params)
        logger.info("Lead email sent for %s", lead.conversation_id)
        return True
    except Exception:
        logger.exception("Could not send lead email")
        return False