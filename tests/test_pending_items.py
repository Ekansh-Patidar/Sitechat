"""Tests for pending items.

The behaviour that matters: an item survives the visitor changing subject,
a second email carries only what is new, a failed send is retried, and
nothing sends without contact details.

The lead email goes out in after_reply, once the reply has been sent, so
tests that check sending run a whole turn with take_turn.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.agent.orchestrator import after_reply, generate_reply
from pydantic import SecretStr

from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.leads.pending import ItemKind, PendingItem
from app.leads.store import InMemoryLeadStore
from app.tenants.models import Tenant


@pytest.fixture(autouse=True)
def resend_configured(monkeypatch):
    """Email counts as set up; sending itself is patched in each test."""
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))


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


def take_turn(tenant, conversation_id, message, store, lead_store):
    """Reply, then run the background work, as the API route does."""
    reply, _, stored = generate_reply(
        tenant, conversation_id, message, store, lead_store=lead_store
    )
    after_reply(
        tenant, conversation_id, stored, reply, store, lead_store, get_settings()
    )
    return reply


def contact_then_reply(reply="Done."):
    contact = make_tool_call("c1", "contact_team", '{"reason": "asked"}')
    return [
        make_message(content=None, tool_calls=[contact]),
        make_message(content=reply, tool_calls=None),
    ]


@patch("app.agent.orchestrator.complete", return_value="summary")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_contact_request_without_email_is_recorded_not_sent(
    mock_complete, mock_send, mock_summary
):
    """The request must survive until an address arrives."""
    mock_complete.side_effect = contact_then_reply("What is your email?")

    lead_store = InMemoryLeadStore()

    take_turn(
        make_tenant(),
        "c1",
        "Can someone call me?",
        InMemoryConversationStore(),
        lead_store,
    )

    mock_send.assert_not_called()
    mock_summary.assert_not_called()

    lead = lead_store.get("t1", "c1")
    assert len(lead.unsent_items) == 1
    assert lead.unsent_items[0].kind == ItemKind.CONTACT_REQUEST


@patch("app.agent.orchestrator.complete", return_value="summary")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_waiting_request_goes_out_once_an_email_arrives(
    mock_complete, mock_send, _
):
    tenant = make_tenant()
    store = InMemoryConversationStore()
    lead_store = InMemoryLeadStore()

    mock_complete.side_effect = contact_then_reply("What is your email?")
    take_turn(tenant, "c1", "Can someone call me?", store, lead_store)
    mock_send.assert_not_called()

    mock_complete.side_effect = None
    mock_complete.return_value = make_message("Thanks!")
    take_turn(tenant, "c1", "sam@example.com", store, lead_store)

    assert mock_send.call_count == 1
    assert not lead_store.get("t1", "c1").has_unsent_items


@patch("app.agent.orchestrator.complete_with_tools")
def test_pending_item_prompts_a_reminder(mock_complete):
    """The server reminds the model, rather than relying on it to remember."""
    mock_complete.return_value = make_message("Sure, about the dates...")

    lead_store = InMemoryLeadStore()
    lead_store.add_item(
        "t1",
        "c1",
        PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="wants a call"),
    )

    generate_reply(
        make_tenant(),
        "c1",
        "Actually, what are the dates?",
        InMemoryConversationStore(),
        lead_store=lead_store,
    )

    sent = mock_complete.call_args[0][0]
    combined = " ".join(m.get("content") or "" for m in sent)
    assert "has not given contact details" in combined


@patch("app.agent.orchestrator.complete", return_value="summary")
@patch("app.agent.orchestrator.send_lead_email")
@patch("app.agent.orchestrator.complete_with_tools")
def test_second_email_carries_only_new_items(
    mock_complete, mock_send, _, monkeypatch
):
    """A returning visitor generates a second email, not a repeat."""
    # Back-to-back requests; the cooldown has its own tests below.
    monkeypatch.setattr(get_settings(), "lead_email_cooldown_minutes", 0)
    # The in-memory store hands out the live lead, so count at send time.
    items_per_email = []
    mock_send.side_effect = lambda lead, tenant, summary, questions=(): (
        items_per_email.append(len(lead.unsent_items)) or True
    )
    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})

    mock_complete.side_effect = contact_then_reply() + contact_then_reply()

    tenant = make_tenant()
    store = InMemoryConversationStore()

    take_turn(tenant, "c1", "please contact me", store, lead_store)
    take_turn(tenant, "c1", "and about the dates too", store, lead_store)

    # Two sends, because the second turn produced a new unsent item.
    assert items_per_email == [1, 1]

    # Every item ends up marked as sent, so nothing is carried twice.
    lead = lead_store.get("t1", "c1")
    assert len(lead.items) == 2
    assert all(item.is_sent for item in lead.items)


@patch("app.agent.orchestrator.complete", return_value="summary")
@patch("app.agent.orchestrator.send_lead_email")
@patch("app.agent.orchestrator.complete_with_tools")
def test_failed_send_is_retried_when_retries_are_on(
    mock_complete, mock_send, _, monkeypatch
):
    monkeypatch.setattr(get_settings(), "retry_failed_lead_emails", True)
    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    tenant = make_tenant()
    store = InMemoryConversationStore()

    mock_send.return_value = False
    mock_complete.side_effect = contact_then_reply()
    take_turn(tenant, "c1", "please contact me", store, lead_store)
    assert lead_store.get("t1", "c1").has_unsent_items

    # The next turn makes no new request, but the waiting one goes out.
    mock_send.return_value = True
    mock_complete.side_effect = None
    mock_complete.return_value = make_message("Anything else?")
    take_turn(tenant, "c1", "thanks", store, lead_store)

    assert mock_send.call_count == 2
    assert not lead_store.get("t1", "c1").has_unsent_items


@patch("app.agent.orchestrator.complete_with_tools")
def test_bot_is_told_the_request_was_passed_on_not_emailed(mock_complete):
    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    mock_complete.side_effect = contact_then_reply()

    generate_reply(
        make_tenant(), "c1", "please contact me",
        InMemoryConversationStore(), lead_store=lead_store,
    )

    tool_result = mock_complete.call_args[0][0][-1]
    assert tool_result["role"] == "tool"
    assert "passed to the team" in tool_result["content"]
    assert "Do not say that an email has been sent" in tool_result["content"]


@patch("app.agent.orchestrator.complete", return_value="summary")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_no_email_setup_skips_the_summary(
    mock_complete, mock_send, mock_summary, monkeypatch
):
    """No Resend key: the bot gives contact details, nothing is written."""
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr(""))
    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    mock_complete.side_effect = contact_then_reply()

    take_turn(
        make_tenant(), "c1", "please contact me",
        InMemoryConversationStore(), lead_store,
    )

    tool_result = mock_complete.call_args[0][0][-1]
    assert "Could not reach the team system" in tool_result["content"]
    mock_summary.assert_not_called()
    mock_send.assert_not_called()
    # Kept, so it goes out once email is set up.
    assert lead_store.get("t1", "c1").has_unsent_items


@patch("app.agent.orchestrator.complete", return_value="Sam wants a call.")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_lead_summary_includes_the_whole_current_turn(
    mock_complete, mock_send, mock_summary
):
    """The email is written after the turn is stored, reply included."""
    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})

    store = InMemoryConversationStore()
    store.append_turn("t1", "c1", "what are the dates?", "June.")
    store.apply_summary("t1", "c1", "Sam asked about fees.", [])

    mock_complete.side_effect = contact_then_reply("Someone will be in touch.")

    take_turn(
        make_tenant(), "c1", "please call me about accommodation",
        store, lead_store,
    )

    summarised = mock_summary.call_args[0][0][-1]["content"]
    assert "Sam asked about fees." in summarised
    assert "what are the dates?" in summarised
    assert "please call me about accommodation" in summarised
    assert "Someone will be in touch." in summarised

    # The summary reaches the email as text.
    assert mock_send.call_args[0][2] == "Sam wants a call."


def test_only_the_items_in_the_email_are_marked_sent():
    """An item added while the email was going out must go in the next one."""
    lead_store = InMemoryLeadStore()
    lead_store.add_item(
        "t1", "c1", PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="call me")
    )
    in_email = lead_store.get("t1", "c1").unsent_items

    lead_store.add_item(
        "t1", "c1", PendingItem(kind=ItemKind.UNANSWERED, detail="pets?")
    )
    lead_store.mark_items_sent("t1", "c1", in_email)

    unsent = lead_store.get("t1", "c1").unsent_items
    assert [item.detail for item in unsent] == ["pets?"]


@patch("app.agent.orchestrator.complete", return_value="summary")
@patch("app.agent.orchestrator.send_lead_email")
@patch("app.agent.orchestrator.complete_with_tools")
def test_failed_send_is_not_retried_by_default(mock_complete, mock_send, _):
    """A send that fails is given up on: no repeat summary, send or error."""
    assert get_settings().retry_failed_lead_emails is False

    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    tenant = make_tenant()
    store = InMemoryConversationStore()

    mock_send.return_value = False
    mock_complete.side_effect = contact_then_reply()
    take_turn(tenant, "c1", "please contact me", store, lead_store)

    lead = lead_store.get("t1", "c1")
    assert not lead.has_unsent_items
    assert lead.items[0].failed_at is not None
    assert lead.items[0].sent_at is None

    # A later message does not resend it.
    mock_complete.side_effect = None
    mock_complete.return_value = make_message("Anything else?")
    take_turn(tenant, "c1", "thanks", store, lead_store)
    assert mock_send.call_count == 1

    # A new request later goes out on its own, without the failed one.
    items_per_email = []
    mock_send.side_effect = lambda lead, tenant, summary, questions=(): (
        items_per_email.append([i.detail for i in lead.unsent_items]) or True
    )
    mock_complete.side_effect = [
        make_message(
            content=None,
            tool_calls=[make_tool_call("c2", "contact_team", '{"reason": "again"}')],
        ),
        make_message(content="Done.", tool_calls=None),
    ]
    take_turn(tenant, "c1", "please try again", store, lead_store)
    assert items_per_email == [["again"]]


# --- every question is recorded and emailed ---------------------------------

import json as _json

from app.agent.orchestrator import _parse_for_team
from app.agent.prompts import SUMMARY_PROMPT
from app.conversations.models import Question


def team_json(summary="Sam asked about the course.", questions=()):
    """What the model returns: questions as (id, text, status) tuples."""
    return _json.dumps(
        {
            "summary": summary,
            "questions": [
                {"id": qid, "question": text, "status": status}
                for qid, text, status in questions
            ],
        }
    )


def capture_emails(mock_send):
    """Record (questions, summary) for each email, at send time."""
    emails = []

    def send(lead, tenant, summary, questions=()):
        emails.append(([(q.text, q.status) for q in questions], summary))
        return True

    mock_send.side_effect = send
    return emails


def plain_reply(text="Sure."):
    return make_message(content=text, tool_calls=None)


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email")
@patch("app.agent.orchestrator.complete_with_tools")
def test_every_question_goes_in_the_email_with_its_status(
    mock_complete, mock_send, mock_compose
):
    emails = capture_emails(mock_send)
    mock_compose.return_value = team_json(
        questions=[
            ("Q1", "How long is the course?", "answered"),
            ("Q2", "How many teachers teach it?", "not_answered"),
        ]
    )
    tenant, store, lead_store = make_tenant(), InMemoryConversationStore(), InMemoryLeadStore()

    mock_complete.return_value = plain_reply("It is 28 days.")
    take_turn(tenant, "c1", "how long is the course for?", store, lead_store)
    mock_complete.return_value = plain_reply("I don't have details on that.")
    take_turn(tenant, "c1", "How many teached will teach this course?", store, lead_store)
    mock_complete.return_value = plain_reply("Okay.")
    take_turn(tenant, "c1", "okay", store, lead_store)

    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    mock_complete.side_effect = contact_then_reply()
    take_turn(tenant, "c1", "please ask the team", store, lead_store)

    questions, _ = emails[0]
    assert questions == [
        ("How long is the course?", "answered"),
        ("How many teachers teach it?", "not_answered"),
        ("please ask the team", "unchecked"),
    ]
    # The model got each question with the reply it was given.
    sent = mock_compose.call_args[0][0][-1]["content"]
    assert "Q2: How many teached will teach this course?" in sent
    assert "Assistant replied: I don't have details on that." in sent
    assert mock_compose.call_args.kwargs["json_mode"] is True


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email")
@patch("app.agent.orchestrator.complete_with_tools")
def test_a_later_email_lists_only_new_questions(
    mock_complete, mock_send, mock_compose, monkeypatch
):
    monkeypatch.setattr(get_settings(), "lead_email_cooldown_minutes", 0)
    emails = capture_emails(mock_send)
    mock_compose.return_value = team_json()
    tenant, store, lead_store = make_tenant(), InMemoryConversationStore(), InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})

    mock_complete.return_value = plain_reply()
    take_turn(tenant, "c1", "can I bring pets?", store, lead_store)
    mock_complete.side_effect = contact_then_reply()
    take_turn(tenant, "c1", "please contact me", store, lead_store)

    mock_complete.side_effect = None
    mock_complete.return_value = plain_reply()
    take_turn(tenant, "c1", "is there an exam?", store, lead_store)
    mock_complete.side_effect = contact_then_reply()
    take_turn(tenant, "c1", "please ask about that too", store, lead_store)

    first, second = [[text for text, _ in qs] for qs, _ in emails]
    assert first == ["can I bring pets?", "please contact me"]
    assert second == ["is there an exam?", "please ask about that too"]
    assert store.get("t1", "c1").questions_emailed == 4


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email", return_value=False)
@patch("app.agent.orchestrator.complete_with_tools")
def test_questions_from_a_failed_email_go_in_the_next_one(
    mock_complete, mock_send, mock_compose
):
    mock_compose.return_value = team_json()
    tenant, store, lead_store = make_tenant(), InMemoryConversationStore(), InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    mock_complete.side_effect = contact_then_reply()

    take_turn(tenant, "c1", "can I bring pets?", store, lead_store)

    assert store.get("t1", "c1").questions_emailed == 0


@patch("app.agent.orchestrator.complete", return_value="Plain text summary.")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_summary_that_is_not_json_is_still_sent(mock_complete, mock_send, _):
    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    mock_complete.side_effect = contact_then_reply()

    take_turn(
        make_tenant(), "c1", "please contact me",
        InMemoryConversationStore(), lead_store,
    )

    _, _, summary, questions = mock_send.call_args[0]
    assert summary == "Plain text summary."
    assert [(q.text, q.status) for q in questions] == [
        ("please contact me", "unchecked")
    ]


def recorded(*texts):
    return [Question(text=text, reply="r") for text in texts]


def test_parse_keeps_every_question_whatever_the_model_returns():
    raw = team_json(
        "S.",
        [
            ("Q2", "Is there an exam?", "not_answered"),
            ("Q9", "Out of range", "answered"),
            ("Q1", "", "answered"),
            ("Q3", "Bad status", "maybe"),
        ],
    )
    summary, asked = _parse_for_team(raw, recorded("how long?", "exaam at end", "pets"))

    assert summary == "S."
    assert [(a.text, a.status) for a in asked] == [
        ("how long?", "answered"),           # blank rewrite: original text kept
        ("Is there an exam?", "not_answered"),
        ("Bad status", "unchecked"),         # invalid status: not trusted
    ]


@pytest.mark.parametrize(
    "raw, summary",
    [
        (_json.dumps({"summary": "S."}), "S."),
        (_json.dumps({"questions": "not a list"}), "(summary unavailable)"),
        (_json.dumps(["not", "an", "object"]), "(summary unavailable)"),
        ("", "(summary unavailable)"),
    ],
)
def test_parse_falls_back_to_the_recorded_questions(raw, summary):
    got_summary, asked = _parse_for_team(raw, recorded("a?", "b?"))
    assert got_summary == summary
    assert [(a.text, a.status) for a in asked] == [
        ("a?", "unchecked"), ("b?", "unchecked")
    ]


def test_there_is_no_unanswered_tool_and_the_prompt_does_not_mention_one():
    """Unanswered questions come from the transcript, not a tool call."""
    from app.agent.prompts import build_system_prompt
    from app.agent.tools import tool_schemas

    names = [tool["function"]["name"] for tool in tool_schemas(make_tenant())]
    assert names == ["contact_team", "record_visitor_details"]
    assert "record_unanswered" not in build_system_prompt(make_tenant())


# --- replay of a real 16-turn chat (2026-10-15) ----------------------------

REAL_CHAT = [
    "Hi",
    "What all courses are offered?",
    "How long does it take for a course to complete for 200 hours, are weekends included?",
    "Are there any days off?",
    "okay",
    "sandeep@example.com",
    "I did my 200 hours in the UK, can I enrol in the 300 hour course here?",
    "is there an age limit?",
    "what payment methods do you accept",
    "okay",
    "how old is your institute?",
    "okay",
    "what is the average size of a class?",
    "okay, is the property LGBTQ friendly?",
    "okay",
    "sandeep@example.com",
]

REAL_QUESTIONS = [
    "What all courses are offered?",
    "How long does it take for a course to complete for 200 hours, are weekends included?",
    "Are there any days off?",
    "I did my 200 hours in the UK, can I enrol in the 300 hour course here?",
    "is there an age limit?",
    "what payment methods do you accept",
    "how old is your institute?",
    "what is the average size of a class?",
    "okay, is the property LGBTQ friendly?",
]


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email")
@patch("app.agent.orchestrator.complete_with_tools")
def test_real_chat_every_question_reaches_the_team(
    mock_complete, mock_send, mock_compose, monkeypatch
):
    """Two emails used to drop questions once older messages were summarised.

    Here the running summary runs several times, the model labels nothing,
    and every question still arrives, each in exactly one email.
    """
    monkeypatch.setattr(get_settings(), "lead_email_cooldown_minutes", 0)
    emails = capture_emails(mock_send)

    def model(messages, json_mode=False):
        if messages[0]["content"] == SUMMARY_PROMPT:
            return "Running summary."
        return team_json("Summary for the team.")  # labels nothing

    mock_compose.side_effect = model
    tenant, store, lead_store = make_tenant(), InMemoryConversationStore(), InMemoryLeadStore()

    for message in REAL_CHAT:
        if message == "okay":
            mock_complete.side_effect = contact_then_reply("Your email?")
        else:
            mock_complete.side_effect = None
            mock_complete.return_value = plain_reply("I don't have details on that.")
        take_turn(tenant, "c1", message, store, lead_store)

    assert store.get("t1", "c1").summary == "Running summary."
    emailed = [text for questions, _ in emails for text, _ in questions]
    assert emailed == REAL_QUESTIONS


@patch("app.agent.orchestrator.complete_with_tools")
def test_bot_is_told_the_email_is_already_on_file(mock_complete):
    """It kept asking for an email it had, once that message was summarised."""
    mock_complete.return_value = plain_reply()
    lead_store = InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})

    generate_reply(
        make_tenant(), "c1", "okay", InMemoryConversationStore(),
        lead_store=lead_store,
    )

    sent = " ".join(m.get("content") or "" for m in mock_complete.call_args[0][0])
    assert "do not ask for their email again" in sent
    assert "has not given contact details" not in sent


# --- one email per visit ---------------------------------------------------


def emailed_once(lead_store, store, mock_complete, mock_compose):
    """Run one turn that emails the team, as the starting point."""
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    mock_compose.return_value = team_json()
    mock_complete.side_effect = contact_then_reply()
    take_turn(make_tenant(), "c1", "please contact me", store, lead_store)


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_goodbye_request_soon_after_an_email_is_not_emailed(
    mock_complete, mock_send, mock_compose
):
    lead_store, store = InMemoryLeadStore(), InMemoryConversationStore()
    emailed_once(lead_store, store, mock_complete, mock_compose)
    first_email_at = lead_store.get("t1", "c1").last_notified_at
    calls_before = mock_compose.call_count

    # The model calls contact_team again on goodbye; nothing new was asked.
    mock_complete.side_effect = contact_then_reply("Bye!")
    take_turn(make_tenant(), "c1", "No that's it, thanks", store, lead_store)

    assert mock_send.call_count == 1
    assert mock_compose.call_count == calls_before  # no model call either
    lead = lead_store.get("t1", "c1")
    assert not lead.has_unsent_items
    assert lead.last_notified_at == first_email_at


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_new_question_soon_after_an_email_is_still_emailed(
    mock_complete, mock_send, mock_compose
):
    lead_store, store = InMemoryLeadStore(), InMemoryConversationStore()
    emailed_once(lead_store, store, mock_complete, mock_compose)

    mock_complete.side_effect = contact_then_reply()
    take_turn(make_tenant(), "c1", "can I reschedule midway?", store, lead_store)

    assert mock_send.call_count == 2


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_request_after_the_cooldown_is_emailed(
    mock_complete, mock_send, mock_compose
):
    from datetime import timedelta

    lead_store, store = InMemoryLeadStore(), InMemoryConversationStore()
    emailed_once(lead_store, store, mock_complete, mock_compose)
    lead = lead_store.get("t1", "c1")
    minutes = get_settings().lead_email_cooldown_minutes
    lead.last_notified_at -= timedelta(minutes=minutes + 1)

    mock_complete.side_effect = contact_then_reply()
    take_turn(make_tenant(), "c1", "thanks", store, lead_store)

    assert mock_send.call_count == 2


# --- the same request is not recorded twice --------------------------------


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_calling_contact_team_again_after_the_email_arrives_adds_nothing(
    mock_complete, mock_send, mock_compose
):
    """From a real chat: called on "okay", then again when the email came."""
    mock_compose.return_value = team_json()
    tenant = make_tenant()
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()

    exam = make_tool_call("c1", "contact_team", '{"reason": "exam at the end?"}')
    mock_complete.side_effect = [
        make_message(content=None, tool_calls=[exam]),
        make_message(content="What is your email?", tool_calls=None),
    ]
    take_turn(tenant, "c1", "okay", store, lead_store)

    first_result = mock_complete.call_args[0][0][-1]["content"]
    assert "Do not call contact_team again" in first_result

    again = make_tool_call("c2", "contact_team", '{"reason": "follow up on exam"}')
    mock_complete.side_effect = [
        make_message(content=None, tool_calls=[again]),
        make_message(content="Thanks!", tool_calls=None),
    ]
    take_turn(tenant, "c1", "sam@example.com", store, lead_store)

    requests = [
        item.detail for item in lead_store.get("t1", "c1").items
        if item.kind == ItemKind.CONTACT_REQUEST
    ]
    assert requests == ["exam at the end?"]
    assert mock_send.call_count == 1


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.send_lead_email", return_value=True)
@patch("app.agent.orchestrator.complete_with_tools")
def test_question_labels_are_kept_after_the_email(
    mock_complete, mock_send, mock_compose
):
    """So reports on unanswered questions need no further model calls."""
    tenant, store, lead_store = make_tenant(), InMemoryConversationStore(), InMemoryLeadStore()
    lead_store.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    mock_compose.return_value = team_json(
        questions=[("Q1", "Pets?", "not_answered")]  # Q2 left unlabelled
    )

    mock_complete.return_value = plain_reply("I don't know.")
    take_turn(tenant, "c1", "can I bring pets?", store, lead_store)
    mock_complete.side_effect = contact_then_reply()
    take_turn(tenant, "c1", "please ask the team", store, lead_store)

    statuses = [q.status for q in store.get("t1", "c1").questions]
    assert statuses == ["not_answered", "unchecked"]


@patch("app.agent.orchestrator.complete", return_value="{}")
def test_shortened_replies_are_marked_for_the_labelling_model(mock_compose):
    """A long answer cut to REPLY_LIMIT must not read as no answer."""
    from app.agent.orchestrator import _compose_for_team
    from app.conversations.models import REPLY_LIMIT, Conversation, Message

    convo = Conversation(
        conversation_id="c1", tenant_id="t1",
        messages=[Message(role="user", content="courses?")],
    )
    questions = [
        Question(text="What courses?", reply="x" * REPLY_LIMIT),
        Question(text="Pets?", reply="I don't know."),
    ]
    _compose_for_team(convo, questions)

    sent = mock_compose.call_args[0][0][-1]["content"]
    assert "x" * REPLY_LIMIT + " [shortened]" in sent
    assert "I don't know. [shortened]" not in sent
