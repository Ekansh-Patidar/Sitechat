"""Tests for the HTML lead email.

The one that matters most is escaping: anything a visitor typed must
arrive as text, never as markup in the team's inbox.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from pydantic import SecretStr

from app.config import get_settings
from app.leads.email_html import render_lead_email
from app.leads.models import AskedQuestion, Lead
from app.leads.notify import send_lead_email
from app.leads.pending import ItemKind, PendingItem
from app.tenants.models import Tenant


def make_tenant(**fields) -> Tenant:
    return Tenant(
        tenant_id="t1",
        site_key="pk_t1",
        display_name="Test School",
        team_email="team@example.com",
        **fields,
    )


def make_lead(**fields) -> Lead:
    return Lead(tenant_id="t1", conversation_id="c1", **fields)


def contact(detail):
    return PendingItem(kind=ItemKind.CONTACT_REQUEST, detail=detail)


def unanswered(detail):
    return PendingItem(kind=ItemKind.UNANSWERED, detail=detail)


def test_each_kind_of_content_gets_its_own_section():
    lead = make_lead(
        email="sam@example.com",
        country="India",
        items=[contact("wants a call about fees")],
    )
    questions = [
        AskedQuestion(text="How long is the course?", status="answered"),
        AskedQuestion(text="Is there an exam?", status="not_answered"),
    ]
    html = render_lead_email(lead, make_tenant(), "Sam asked about fees.", questions)

    assert "Contact" in html and "India" in html
    assert "They asked the team to get in touch." in html
    # The request's model-written reason is not shown; the questions are.
    assert "wants a call about fees" not in html
    assert "What they asked (2)" in html
    assert "Conversation summary" in html and "Sam asked about fees." in html
    assert 'href="mailto:sam@example.com?subject=' in html


def test_questions_needing_a_reply_come_first_with_their_status():
    lead = make_lead(
        email="sam@example.com",
        # Left on a lead from before questions were recorded per turn.
        items=[unanswered("Do you allow pets?")],
    )
    questions = [
        AskedQuestion(text="How long is the course?", status="answered"),
        AskedQuestion(text="Is there an exam?", status="not_answered"),
        AskedQuestion(text="Do you take cards?", status="partly"),
        AskedQuestion(text="okay great", status="comment"),
        AskedQuestion(text="hmm", status="unchecked"),
    ]
    html = render_lead_email(lead, make_tenant(), "s", questions)

    order = [
        "Is there an exam?", "Do you allow pets?", "Do you take cards?",
        "hmm", "How long is the course?", "okay great",
    ]
    positions = [html.index(text) for text in order]
    assert positions == sorted(positions)
    for label in ("Not answered", "Partly answered", "Not checked", "Answered", "Comment"):
        assert f">{label}</span>" in html


def test_empty_sections_are_left_out():
    lead = make_lead(email="sam@example.com", items=[contact("call me")])
    html = render_lead_email(lead, make_tenant(), "summary")

    assert "What they asked" not in html
    assert "been in touch before" not in html
    assert "Country" not in html


def test_visitor_and_model_text_is_escaped():
    lead = make_lead(
        email="sam@example.com",
        name='<img src=x onerror="alert(1)">',
        items=[unanswered("<script>alert(1)</script>")],
    )
    html = render_lead_email(lead, make_tenant(), "<b>bold</b> & more")

    assert "<script>" not in html
    assert "<img" not in html
    assert "<b>bold</b>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in html


def test_returning_visitor_banner():
    lead = make_lead(
        email="sam@example.com",
        last_notified_at=datetime.now(timezone.utc),
        items=[contact("new request")],
    )
    html = render_lead_email(lead, make_tenant(), "summary")
    assert "been in touch before" in html


def test_phone_only_lead_gets_a_call_button():
    lead = make_lead(phone="+44 7700 900123", items=[contact("call me")])
    html = render_lead_email(lead, make_tenant(), "summary")

    assert 'href="tel:+44 7700 900123"' in html
    assert "mailto:" not in html
    assert "no email address" in html


def test_unsafe_accent_colour_falls_back_to_the_default():
    lead = make_lead(email="sam@example.com")
    html = render_lead_email(
        lead, make_tenant(accent_colour='red;"><script>'), "summary"
    )
    assert "<script>" not in html
    assert "#1f6f5c" in html


def test_light_accent_gets_dark_button_text():
    lead = make_lead(email="sam@example.com")
    html = render_lead_email(lead, make_tenant(accent_colour="#fde68a"), "s")
    assert "color:#111827;text-decoration:none;border-radius:8px" in html


@patch("app.leads.notify.resend.Emails.send")
def test_email_is_sent_with_html_and_text(mock_send, monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))
    lead = make_lead(email="sam@example.com", items=[contact("call me")])

    assert send_lead_email(lead, make_tenant(), "summary") is True

    params = mock_send.call_args[0][0]
    assert params["html"].startswith("<!doctype html>")
    assert "They asked the team to get in touch." in params["text"]


def test_request_note_and_preview_line():
    asked = [
        AskedQuestion(text="When do classes start?", status="answered"),
        AskedQuestion(text="Can I bring my pets?", status="not_answered"),
    ]
    with_request = render_lead_email(
        make_lead(email="sam@example.com", items=[contact("pets")]),
        make_tenant(), "s", asked,
    )
    without = render_lead_email(make_lead(email="sam@example.com"), make_tenant(), "s", asked)

    assert "They asked the team to get in touch." in with_request
    assert "They asked the team to get in touch." not in without
    # The hidden preview line, shown in the inbox list.
    preview = with_request.split('opacity:0;">', 1)[1].split("</div>", 1)[0]
    assert preview == (
        "They asked the team to get in touch. Not answered: Can I bring my pets?"
    )


def test_throwaway_email_is_flagged_but_still_sent():
    from app.leads.notify import _format_body

    lead = make_lead(email="someone@mailinator.com", items=[contact("call me")])
    html = render_lead_email(lead, make_tenant(), "s")
    text = _format_body(lead, make_tenant(), "s")

    assert "throwaway address" in html
    assert "someone@mailinator.com (throwaway address, may not be real)" in text

    normal = render_lead_email(make_lead(email="sam@gmail.com"), make_tenant(), "s")
    assert "throwaway" not in normal


@patch("app.leads.notify.resend.Emails.send")
def test_reply_time_shown_to_the_visitor_is_repeated_for_the_team(mock_send, monkeypatch):
    """The chat told the visitor how soon to expect a reply; the team sees
    the same promise. The inbox preview stays short."""
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))
    tenant = make_tenant(reply_time="within 24 hours")
    lead = make_lead(email="sam@example.com", items=[contact("call me")])

    send_lead_email(lead, tenant, "summary")

    params = mock_send.call_args[0][0]
    note = "The chat told them you usually reply within 24 hours."
    assert note in params["text"]
    assert note in params["html"]
    preview = params["html"].split('opacity:0;">', 1)[1].split("</div>", 1)[0]
    assert "24 hours" not in preview


@patch("app.leads.notify.resend.Emails.send")
def test_without_a_reply_time_no_promise_is_mentioned(mock_send, monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))
    send_lead_email(make_lead(email="sam@example.com", items=[contact("x")]),
                    make_tenant(), "summary")

    assert "usually reply" not in mock_send.call_args[0][0]["text"]
