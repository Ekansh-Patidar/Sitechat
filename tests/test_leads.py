"""Tests for lead capture.

The extraction tests matter most: a missed email is a lost lead, and an
email that reaches the model is personal data in a place it should not be.
"""

from unittest.mock import MagicMock, patch

from app.agent.orchestrator import after_reply, generate_reply
from pydantic import SecretStr

from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.leads.extraction import extract_contact, looks_like_valid_email
from app.leads.store import InMemoryLeadStore
from app.tenants.models import Tenant


def make_tenant() -> Tenant:
    return Tenant(
        tenant_id="t1",
        site_key="pk_t1",
        display_name="Test School",
        team_email="team@example.com",
    )


def make_message(content=None, tool_calls=None):
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    return message


def make_tool_call(call_id: str, name: str, arguments: str):
    call = MagicMock()
    call.id = call_id
    call.function.name = name
    call.function.arguments = arguments
    return call


def test_email_is_extracted_and_redacted():
    email, _, redacted = extract_contact("My email is sam@example.com, thanks")

    assert email == "sam@example.com"
    assert "sam@example.com" not in redacted
    assert "[email provided]" in redacted


def test_phone_is_extracted():
    _, phone, redacted = extract_contact("Call me on +91 73009 97877")

    assert "7300997877" in phone.replace(" ", "")
    assert "73009" not in redacted


def test_short_numbers_are_not_treated_as_phones():
    """Prices, dates and course hours must not be redacted."""
    _, phone, redacted = extract_contact("Is the 200 hour course 1450 dollars?")

    assert phone == ""
    assert "200" in redacted
    assert "1450" in redacted


def test_email_validation_rejects_malformed():
    assert looks_like_valid_email("sam@example.com")
    assert not looks_like_valid_email("sam@example")
    assert not looks_like_valid_email("sam.example.com")
    assert not looks_like_valid_email("sam@@example.com")
    assert not looks_like_valid_email("")


@patch("app.agent.orchestrator.complete_with_tools")
def test_email_reaches_the_lead_store_not_the_model(mock_complete):
    """The whole point: stored on the lead, absent from the prompt."""
    mock_complete.return_value = make_message(content="Thanks!")

    lead_store = InMemoryLeadStore()

    generate_reply(
        make_tenant(),
        "c1",
        "My email is sam@example.com",
        InMemoryConversationStore(),
        lead_store=lead_store,
    )

    lead = lead_store.get("t1", "c1")
    assert lead.email == "sam@example.com"

    sent = mock_complete.call_args[0][0]
    combined = " ".join(m.get("content") or "" for m in sent)
    assert "sam@example.com" not in combined


@patch("app.agent.orchestrator.complete_with_tools")
def test_details_tool_updates_the_lead(mock_complete):
    tool_call = make_tool_call(
        "call_1",
        "record_visitor_details",
        '{"country": "Germany", "intended_month": "March 2027"}',
    )
    mock_complete.side_effect = [
        make_message(content=None, tool_calls=[tool_call]),
        make_message(content="Lovely.", tool_calls=None),
    ]

    lead_store = InMemoryLeadStore()

    generate_reply(
        make_tenant(),
        "c1",
        "I'm from Germany, thinking about March",
        InMemoryConversationStore(),
        lead_store=lead_store,
    )

    lead = lead_store.get("t1", "c1")
    assert lead.country == "Germany"
    assert lead.intended_month == "March 2027"


@patch("app.agent.orchestrator.complete", return_value="summary")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_contact_team_requires_contact_details(
    mock_complete, mock_send, _, monkeypatch
):
    """The bot must not email the team with no way to reply."""
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))
    tool_call = make_tool_call("call_1", "contact_team", '{"reason": "asked"}')
    mock_complete.side_effect = [
        make_message(content=None, tool_calls=[tool_call]),
        make_message(content="What is your email?", tool_calls=None),
    ]

    tenant = make_tenant()
    store = InMemoryConversationStore()
    lead_store = InMemoryLeadStore()

    # The email goes out after the reply, so run that step too.
    reply, _, stored = generate_reply(
        tenant, "c1", "Can someone call me?", store, lead_store=lead_store
    )
    after_reply(tenant, "c1", stored, reply, store, lead_store, get_settings())

    mock_send.assert_not_called()

def test_extracted_email_is_lowercased_and_still_redacted():
    """Resend matches recipients exactly, so "Sam@" and "sam@" differ."""
    email, _, redacted = extract_contact("It's Sandeep.Dot4@Gmail.com thanks")
    assert email == "sandeep.dot4@gmail.com"
    assert "andeep" not in redacted
