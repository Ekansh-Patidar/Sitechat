"""A suggested reply to the visitor, for the team to finish and send.

The lead email has a "Reply to <visitor>" button that opens this draft in
the team's own email program, and shows it as text to copy. The team reads
it, writes the answers and sends it from their own address: nothing reaches
the visitor without a person checking it, and we never email an address
that a visitor typed (anyone could type anyone's).

Built from a template, not by the model: it only thanks the visitor and
lists what they asked that still needs an answer, leaving a gap under each
for the team. A model-written draft would be tempted to fill those gaps,
and an invented answer sent under the school's name is the worst outcome.
"""

from collections.abc import Sequence
from urllib.parse import quote

from app.leads.models import AskedQuestion, Lead
from app.tenants.models import Tenant

ANSWER_GAP = "[your answer]"

# Statuses that still need something from the team.
_TO_ANSWER = {"not_answered", "partly", "unchecked"}

# Email programs and browsers cut mailto: links somewhere past 2,000
# characters, and some refuse them entirely. Beyond this the button opens
# a blank email with the subject, and the team copies the draft instead.
MAILTO_LIMIT = 1900


def draft_subject(tenant: Tenant) -> str:
    return f"Your enquiry to {tenant.display_name}"


def draft_body(lead: Lead, tenant: Tenant, questions: Sequence[AskedQuestion]) -> str:
    # Imported here: email_html imports this module.
    from app.leads.email_html import questions_for_email

    first_name = lead.name.split()[0] if lead.name.strip() else ""
    to_answer = [
        q for q in questions_for_email(lead, questions) if q.status in _TO_ANSWER
    ]

    lines = [
        f"Hi {first_name}," if first_name else "Hi,",
        "",
        f"Thank you for getting in touch with {tenant.display_name} through "
        "our website.",
    ]
    if to_answer:
        lines += ["", "You asked:"]
        for question in to_answer:
            lines += ["", f"- {question.text}", f"  {ANSWER_GAP}"]
    lines += [
        "",
        "If you have any other questions, just reply to this email.",
        "",
        "Warm regards,",
        tenant.display_name,
    ]
    return "\n".join(lines)


def mailto_link(lead: Lead, tenant: Tenant, body: str) -> str:
    """mailto: for the visitor with the subject and, if it fits, the draft."""
    address = quote(lead.email, safe="@")
    subject = quote(draft_subject(tenant))
    with_body = (
        f"mailto:{address}?subject={subject}"
        f"&body={quote(body.replace(chr(10), chr(13) + chr(10)))}"
    )
    if len(with_body) <= MAILTO_LIMIT:
        return with_body
    return f"mailto:{address}?subject={subject}"
