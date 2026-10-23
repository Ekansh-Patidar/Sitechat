"""Tests for the suggested reply in the lead email (backlog 82)."""

import re
from urllib.parse import unquote

from app.leads.email_html import render_lead_email
from app.leads.models import AskedQuestion, Lead
from app.leads.reply_draft import ANSWER_GAP, MAILTO_LIMIT, draft_body, mailto_link
from app.tenants.models import Tenant


def tenant():
    return Tenant(tenant_id="t1", site_key="pk", display_name="Pyramid Yogshala",
                  team_email="team@example.com")


def lead(**fields):
    return Lead(tenant_id="t1", conversation_id="c1", **fields)


QUESTIONS = [
    AskedQuestion(text="What does the 200-hour course cost?", status="answered"),
    AskedQuestion(text="Is there an exam?", status="not_answered"),
    AskedQuestion(text="Can I bring my dog?", status="partly"),
    AskedQuestion(text="Thanks, that's great", status="comment"),
]


def test_draft_lists_only_what_still_needs_an_answer():
    body = draft_body(lead(email="sam@example.com", name="Sam Smith"), tenant(), QUESTIONS)

    assert body.startswith("Hi Sam,")
    assert "Thank you for getting in touch with Pyramid Yogshala" in body
    assert "- Is there an exam?" in body
    assert "- Can I bring my dog?" in body
    # Answered in the chat already, or not a question: not listed.
    assert "200-hour" not in body
    assert "Thanks, that's great" not in body
    # A gap for the team under each, never an answer.
    assert body.count(ANSWER_GAP) == 2
    assert body.rstrip().endswith("Pyramid Yogshala")


def test_draft_without_a_name_or_open_questions():
    body = draft_body(lead(email="sam@example.com"), tenant(),
                      [AskedQuestion(text="Price?", status="answered")])
    assert body.startswith("Hi,")
    assert "You asked" not in body
    assert ANSWER_GAP not in body


def test_mailto_carries_the_subject_and_draft():
    body = draft_body(lead(email="sam@example.com"), tenant(), QUESTIONS)
    link = mailto_link(lead(email="sam@example.com"), tenant(), body)

    assert link.startswith("mailto:sam@example.com?subject=")
    assert unquote(link.split("subject=")[1].split("&")[0]) == "Your enquiry to Pyramid Yogshala"
    sent_body = unquote(link.split("&body=")[1])
    assert sent_body == body.replace("\n", "\r\n")


def test_a_draft_too_long_for_a_link_opens_with_the_subject_only():
    many = [AskedQuestion(text=f"Question number {i} about the course?", status="not_answered")
            for i in range(60)]
    body = draft_body(lead(email="sam@example.com"), tenant(), many)
    link = mailto_link(lead(email="sam@example.com"), tenant(), body)
    assert "&body=" not in link
    assert len(link) <= MAILTO_LIMIT


def test_visitor_text_cannot_break_out_of_the_link_or_the_page():
    """A question is visitor text: in the link it is percent-encoded, in the
    copyable draft it is HTML-escaped."""
    nasty = AskedQuestion(text='Hi"><script>alert(1)</script>&cc=evil@x.com', status="not_answered")
    html = render_lead_email(lead(email="sam@example.com"), tenant(), "Summary.", [nasty])

    assert "<script>" not in html
    links = re.findall(r'href="(mailto:[^"]*)"', html)
    href = next(link for link in links if "?subject=" in link).replace("&amp;", "&")
    # The only parameters are the ones we set; the visitor's "&cc=" is
    # encoded inside the body, so it cannot add a recipient.
    assert [part.split("=")[0] for part in href.split("?")[1].split("&")] == ["subject", "body"]
    assert "%26cc%3Devil" in href


def test_html_email_shows_the_draft_and_the_button_opens_it():
    html = render_lead_email(lead(email="sam@example.com", name="Sam"), tenant(), "Summary.", QUESTIONS)
    assert "Suggested reply" in html
    assert "Hi Sam," in html
    assert "Reply to sam@example.com" in html
    assert "&amp;body=" in html  # the draft rides in the mailto link
    assert "They have not been sent a copy" in html


def test_phone_only_lead_gets_no_draft():
    html = render_lead_email(lead(phone="+44 7700 900123"), tenant(), "Summary.", QUESTIONS)
    assert "Suggested reply" not in html
    assert "tel:" in html
