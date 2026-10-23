"""The HTML version of the lead email.

Sent alongside the plain-text body, which stays as the fallback. Email
clients are not browsers: Gmail and Outlook drop <style> blocks, external
CSS and most layout features, so this uses nested tables and inline styles
only, at the usual 600px width.

Every value that came from a visitor or the model is escaped. A visitor can
type anything into the chat, and it must arrive as text, not markup.
"""

import re
from collections.abc import Sequence
from html import escape
from app.leads.extraction import looks_like_throwaway
from app.leads.links import LINK_CAUTION
from app.leads.models import AskedQuestion, Lead
from app.leads.pending import ItemKind
from app.leads.reply_draft import draft_body, mailto_link
from app.tenants.models import Tenant

# Label and (text, background) colours for each question status, in the
# order they are listed: what the team needs to answer comes first.
STATUS_LABELS = {
    "not_answered": "Not answered",
    "partly": "Partly answered",
    "unchecked": "Not checked",
    "answered": "Answered",
    "comment": "Comment",
}
STATUS_COLOURS = {
    "not_answered": ("#92400e", "#fef3c7"),
    "partly": ("#9a3412", "#ffedd5"),
    "unchecked": ("#374151", "#f3f4f6"),
    "answered": ("#166534", "#dcfce7"),
    "comment": ("#4b5563", "#f3f4f6"),
}
_ORDER = list(STATUS_LABELS)


def questions_for_email(
    lead: Lead, questions: Sequence[AskedQuestion]
) -> list[AskedQuestion]:
    """The questions to list, most in need of a reply first.

    Includes UNANSWERED items still waiting on the lead. Those were created
    before questions were recorded per turn, and are shown as not answered.
    """
    legacy = [
        AskedQuestion(text=item.detail, status="not_answered")
        for item in lead.unsent_items
        if item.kind == ItemKind.UNANSWERED
    ]
    combined = [*questions, *legacy]
    return sorted(
        combined,
        key=lambda q: _ORDER.index(q.status) if q.status in _ORDER else 0,
    )

FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, "
    "Arial, sans-serif"
)
DEFAULT_ACCENT = "#1f6f5c"

INK = "#111827"
MUTED = "#6b7280"
LINE = "#e5e7eb"
PAGE = "#f3f4f6"


def _e(text: str) -> str:
    return escape(str(text), quote=True)


def _accent(tenant: Tenant) -> str:
    """The tenant's colour, if it is a plain hex value safe to inline."""
    colour = tenant.accent_colour or ""
    return colour if re.fullmatch(r"#[0-9a-fA-F]{6}", colour) else DEFAULT_ACCENT


def _text_on(hex_colour: str) -> str:
    """White or near-black, whichever reads better on this background."""
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return "#ffffff" if luminance < 0.45 else INK


def _section(
    title: str,
    body: str,
    background: str = "#ffffff",
    border: str = LINE,
    title_colour: str = MUTED,
) -> str:
    """One boxed section: a small uppercase heading over its content."""
    return f"""
<tr><td style="padding:0 32px 16px 32px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="border-collapse:separate;background:{background};border:1px solid {border};border-radius:10px;">
    <tr><td style="padding:18px 20px;">
      <div style="font:600 11px/1.4 {FONT};letter-spacing:0.08em;text-transform:uppercase;color:{title_colour};margin:0 0 10px 0;">{_e(title)}</div>
      {body}
    </td></tr>
  </table>
</td></tr>"""


def request_note(tenant: Tenant) -> str:
    """The line for a contact request, with the reply time the visitor was
    shown in the chat (Tenant.reply_time), so the team knows what was said."""
    note = "They asked the team to get in touch."
    if tenant.reply_time:
        note += f" The chat told them you usually reply {tenant.reply_time}."
    return note


def _note(text: str, ink: str, fill: str, border: str) -> str:
    """A one-line tinted banner between the header and the sections."""
    return f"""
<tr><td style="padding:0 32px 16px 32px;">
  <div style="font:14px/1.5 {FONT};color:{ink};background:{fill};border:1px solid {border};border-radius:8px;padding:10px 14px;">
    {_e(text)}
  </div>
</td></tr>"""


def _question_rows(questions: list[AskedQuestion]) -> str:
    """Each question with its status as a small pill above it."""
    rows = []
    for index, question in enumerate(questions):
        status = question.status if question.status in STATUS_LABELS else "unchecked"
        ink, fill = STATUS_COLOURS[status]
        divider = "" if index == 0 else f"border-top:1px solid {LINE};"
        rows.append(
            f'<div style="padding:10px 0;{divider}">'
            f'<span style="display:inline-block;font:600 11px/1 {FONT};'
            f"letter-spacing:0.02em;color:{ink};background:{fill};"
            f'border-radius:999px;padding:4px 9px;">{STATUS_LABELS[status]}</span>'
            f'<div style="font:15px/1.5 {FONT};color:{INK};margin-top:6px;">'
            f"{_e(question.text)}</div></div>"
        )
    return "".join(rows)


def _contact_rows(lead: Lead, tenant: Tenant, accent: str) -> str:
    rows = []
    if lead.email:
        warning = (
            ' <span style="font-size:12px;color:#92400e;">'
            "(throwaway address, may not be real)</span>"
            if looks_like_throwaway(lead.email) else ""
        )
        rows.append(
            ("Email", f'<a href="mailto:{_e(lead.email)}" style="color:{accent};'
             f'text-decoration:none;">{_e(lead.email)}</a>{warning}')
        )
    if lead.phone:
        rows.append(
            ("Phone", f'<a href="tel:{_e(lead.phone)}" style="color:{accent};'
             f'text-decoration:none;">{_e(lead.phone)}</a>')
        )
    for label, value in (
        ("Name", lead.name),
        ("Country", lead.country),
        (tenant.wording.when_label, lead.intended_month),
        ("Interested in", lead.course_interest),
    ):
        if value:
            rows.append((label, _e(value)))

    cells = "".join(
        f"""<tr>
  <td width="140" valign="top" style="font:14px/1.5 {FONT};color:{MUTED};padding:4px 12px 4px 0;">{label}</td>
  <td valign="top" style="font:15px/1.5 {FONT};color:{INK};padding:4px 0;">{value}</td>
</tr>"""
        for label, value in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0">{cells}</table>'
    )


def _draft_section(lead: Lead, tenant: Tenant, questions: Sequence[AskedQuestion]) -> str:
    """The suggested reply as text to copy, for when the button cannot open
    it (a long draft, or an email program that ignores mailto bodies)."""
    draft = draft_body(lead, tenant, questions)
    return _section(
        "Suggested reply",
        f'<div style="font:14px/1.5 {FONT};color:{MUTED};margin:0 0 10px 0;">'
        "The button below opens this in a new email to the visitor. Fill in "
        "the answers, check it, then send it from your own address.</div>"
        f'<div style="font:15px/1.6 {FONT};color:{INK};white-space:pre-wrap;'
        f'background:{PAGE};border-radius:8px;padding:12px 14px;">{_e(draft)}</div>',
    )


def _button(lead: Lead, tenant: Tenant, accent: str, questions=()) -> str:
    if lead.email:
        href = _e(mailto_link(lead, tenant, draft_body(lead, tenant, questions)))
        label = f"Reply to {_e(lead.email)}"
    elif lead.phone:
        href = f"tel:{_e(lead.phone)}"
        label = f"Call {_e(lead.phone)}"
    else:
        return ""

    return f"""
<tr><td style="padding:8px 32px 24px 32px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="border-radius:8px;background:{accent};">
      <a href="{href}" style="display:inline-block;padding:12px 22px;font:600 15px/1 {FONT};color:{_text_on(accent)};text-decoration:none;border-radius:8px;">{label}</a>
    </td></tr>
  </table>
</td></tr>"""


def render_lead_email(
    lead: Lead,
    tenant: Tenant,
    summary: str,
    questions: Sequence[AskedQuestion] = (),
    links: bool = False,
) -> str:
    """The full HTML document for one lead email. links: visitor text held
    links, already defanged by the caller; adds the caution note."""
    accent = _accent(tenant)
    asked = questions_for_email(lead, questions)
    # The request's own reason text is not shown. The model writes it, it
    # repeats or rewords questions, and only the first request in a visit is
    # kept; "What they asked" is the complete list of what to follow up.
    requested = any(
        i.kind == ItemKind.CONTACT_REQUEST for i in lead.unsent_items
    )

    # Shown as the inbox preview line, then hidden in the body.
    to_answer = [q.text for q in asked if q.status in ("not_answered", "partly")]
    preview = " ".join(
        part
        for part in (
            "They asked the team to get in touch." if requested else "",
            f"Not answered: {to_answer[0]}" if to_answer else "",
        )
        if part
    ) or "A visitor needs a reply."

    sections = []

    if lead.last_notified_at:
        sections.append(
            _note(
                "This visitor has been in touch before. Only what is new since "
                "the last email is listed.",
                ink="#1e3a5f", fill="#eff6ff", border="#bfdbfe",
            )
        )

    if links:
        sections.append(
            _note(LINK_CAUTION, ink="#92400e", fill="#fffbeb", border="#fde68a")
        )

    if requested:
        sections.append(
            _note(
                request_note(tenant),
                ink="#065f46", fill="#ecfdf5", border="#a7f3d0",
            )
        )

    sections.append(_section("Contact", _contact_rows(lead, tenant, accent)))

    if asked:
        sections.append(_section(f"What they asked ({len(asked)})", _question_rows(asked)))

    sections.append(
        _section(
            "Conversation summary",
            f'<div style="font:15px/1.6 {FONT};color:{INK};">{_e(summary)}</div>',
        )
    )

    if lead.email:
        sections.append(_draft_section(lead, tenant, questions))
        footer = (
            "Replying to this email reaches the visitor directly. They have "
            "not been sent a copy of it."
        )
    else:
        footer = "The visitor left a phone number but no email address."

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>New enquiry</title>
</head>
<body style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_e(preview)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE};">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
           style="width:100%;max-width:600px;background:#ffffff;border-radius:12px;border-collapse:separate;overflow:hidden;">
      <tr><td style="height:4px;line-height:4px;font-size:0;background:{accent};">&nbsp;</td></tr>
      <tr><td style="padding:28px 32px 20px 32px;">
        <div style="font:600 12px/1.4 {FONT};letter-spacing:0.08em;text-transform:uppercase;color:{accent};">New enquiry</div>
        <div style="font:700 22px/1.3 {FONT};color:{INK};margin:6px 0 4px 0;">A visitor needs a reply</div>
        <div style="font:14px/1.5 {FONT};color:{MUTED};">From the chat on {_e(tenant.display_name)}'s website</div>
      </td></tr>
      {"".join(sections)}
      {_button(lead, tenant, accent, questions)}
      <tr><td style="padding:16px 32px 28px 32px;border-top:1px solid {LINE};">
        <div style="font:13px/1.5 {FONT};color:{MUTED};">{footer}</div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""
