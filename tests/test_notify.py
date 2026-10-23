"""Tests for the lead email itself: its body, recipients and skip paths.

resend.Emails.send is patched, so nothing is sent.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from pydantic import SecretStr

from app.config import get_settings
from app.leads.models import AskedQuestion, Lead
from app.leads.notify import _format_body, send_lead_email
from app.leads.pending import ItemKind, PendingItem
from app.tenants.models import Tenant


def make_tenant(team_email="team@example.com") -> Tenant:
    return Tenant(
        tenant_id="t1",
        site_key="pk_t1",
        display_name="Test School",
        team_email=team_email,
    )


def make_lead(**fields) -> Lead:
    return Lead(tenant_id="t1", conversation_id="c1", **fields)


@pytest.fixture
def resend_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))


# --- body ------------------------------------------------------------------


def test_body_lists_known_details_and_skips_empty_ones():
    lead = make_lead(email="sam@example.com", country="India")
    body = _format_body(lead, make_tenant(), "Sam wants a call.")

    assert "Email: sam@example.com" in body
    assert "Country: India" in body
    assert "Phone:" not in body
    assert "Name:" not in body
    assert "Test School" in body
    assert "Sam wants a call." in body


def test_body_labels_each_kind_of_item():
    lead = make_lead(
        email="sam@example.com",
        items=[
            PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="wants a call"),
            PendingItem(kind=ItemKind.UNANSWERED, detail="Do you allow pets?"),
        ],
    )
    questions = [AskedQuestion(text="How long is it?", status="answered")]
    body = _format_body(lead, make_tenant(), "summary", questions)

    assert "They asked the team to get in touch." in body
    # The request's model-written reason is not listed; the questions are.
    assert "wants a call" not in body
    assert "What they asked:" in body
    assert "- [Not answered] Do you allow pets?" in body
    assert "- [Answered] How long is it?" in body


def test_body_lists_only_unsent_items():
    sent = datetime.now(timezone.utc)
    lead = make_lead(
        email="sam@example.com",
        items=[
            PendingItem(kind=ItemKind.UNANSWERED, detail="Old question?", sent_at=sent),
            PendingItem(kind=ItemKind.UNANSWERED, detail="New question?"),
        ],
        last_notified_at=sent,
    )
    body = _format_body(lead, make_tenant(), "summary")

    assert "New question?" in body
    assert "Old question?" not in body
    assert "has been in touch before" in body


def test_body_without_a_request_or_questions_has_neither_section():
    body = _format_body(make_lead(email="sam@example.com"), make_tenant(), "s")
    assert "get in touch" not in body
    assert "What they asked" not in body
    assert "has been in touch before" not in body


# --- sending ---------------------------------------------------------------


@patch("app.leads.notify.resend.Emails.send")
def test_only_the_team_is_emailed_and_replies_go_to_the_visitor(mock_send, resend_key):
    """Anyone can type anyone's address into the chat, so we never send to
    it: no to, cc or bcc. The team's reply reaches the visitor via Reply-To."""
    lead = make_lead(email="sam@example.com")
    assert send_lead_email(lead, make_tenant(), "summary") is True

    params = mock_send.call_args[0][0]
    assert params["to"] == ["team@example.com"]
    assert "cc" not in params and "bcc" not in params
    assert params["reply_to"] == "sam@example.com"
    assert "summary" in params["text"]
    assert "Suggested reply" in params["text"]


@patch("app.leads.notify.resend.Emails.send")
def test_phone_only_lead_has_no_reply_to(mock_send, resend_key):
    lead = make_lead(phone="+44 7700 900123")
    assert send_lead_email(lead, make_tenant(), "summary") is True
    params = mock_send.call_args[0][0]
    assert "reply_to" not in params and "cc" not in params
    assert "Suggested reply" not in params["text"]


@patch("app.leads.notify.resend.Emails.send")
def test_no_resend_key_skips_sending(mock_send, monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr(""))
    lead = make_lead(email="sam@example.com")
    assert send_lead_email(lead, make_tenant(), "summary") is False
    mock_send.assert_not_called()


@patch("app.leads.notify.resend.Emails.send")
def test_no_team_email_skips_sending(mock_send, resend_key):
    lead = make_lead(email="sam@example.com")
    assert send_lead_email(lead, make_tenant(team_email=""), "summary") is False
    mock_send.assert_not_called()


@patch("app.leads.notify.resend.Emails.send", side_effect=RuntimeError("down"))
def test_resend_failure_returns_false(mock_send, resend_key):
    lead = make_lead(email="sam@example.com")
    assert send_lead_email(lead, make_tenant(), "summary") is False


@patch("app.leads.notify.resend.Emails.send")
def test_reply_to_is_lowercased_for_older_leads(mock_send, resend_key):
    lead = make_lead(email="Sam.Smith@Example.com")
    assert send_lead_email(lead, make_tenant(), "summary") is True
    assert mock_send.call_args[0][0]["reply_to"] == "sam.smith@example.com"
