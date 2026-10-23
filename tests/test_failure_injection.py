"""Failure injection (backlog 51): what a visitor and the team get when a
dependency fails.

Each test breaks one thing (the model API, a tool call, a store, the lead
summary) and checks the failure is contained: a polite message rather than
a stack trace, nothing half-stored, and the lead email still going out
where it can. Known gaps are marked xfail with the backlog item that fixes
them, so they flip to passing (and fail the strict xfail) once fixed.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api.dependencies as deps
from app.agent import llm
from app.agent.orchestrator import after_reply, generate_reply
from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.documents.store import InMemoryChunkStore
from app.facts.store import InMemoryFactStore
from app.leads.pending import ItemKind, PendingItem
from app.leads.store import InMemoryLeadStore
from app.main import create_app
from app.programmes.store import InMemoryProgrammeStore
from app.usage.store import InMemoryUsageStore
from app.feedback.store import InMemoryFeedbackStore
from app.rate_limit import limiter
from app.tenants.models import Tenant
from app.tenants.store import InMemoryTenantStore
from tests.test_api_security import NoOpVectorStore

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


@pytest.fixture(autouse=True)
def no_network():
    """No test here may reach a real API: embeddings are stubbed for all."""
    with patch("app.retrieval.pipeline.embed_query", return_value=[0.0] * 1536):
        yield


def tenant():
    return Tenant(tenant_id="t1", site_key="pk_valid", display_name="School",
                  allowed_domains=["example.com"], team_email="team@example.com")


def make_client(**overrides):
    tenants = InMemoryTenantStore()
    tenants.save(tenant())
    stores = dict(
        tenant_store=tenants,
        conversation_store=InMemoryConversationStore(),
        fact_store=InMemoryFactStore(),
        vector_store=NoOpVectorStore(),
        chunk_store=InMemoryChunkStore(),
        lead_store=InMemoryLeadStore(),
        programme_store=InMemoryProgrammeStore(),
        usage_store=InMemoryUsageStore(),
        feedback_store=InMemoryFeedbackStore(),
    )
    stores.update(overrides)
    deps._cache.clear()
    limiter.reset()
    # A crash should reach the visitor as a plain 500, not a test exception.
    return TestClient(create_app(**stores), raise_server_exceptions=False), stores


def chat(client, message="Is there an exam?", conversation_id="c1"):
    return client.post(
        "/chat",
        json={"message": message, "conversation_id": conversation_id},
        headers={"X-Site-Key": "pk_valid", "Origin": "https://example.com"},
    )


def reply(content="Fine.", tool_calls=None):
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    return message


def tool_call(name, arguments, call_id="call_1"):
    call = MagicMock()
    call.id = call_id
    call.function.name = name
    call.function.arguments = arguments
    call.model_dump.return_value = {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    return call


# --- the model API ------------------------------------------------------------


OPENAI_FAILURES = [
    (openai.RateLimitError("slow down", response=httpx.Response(429, request=REQUEST), body=None),
     "We're a bit busy right now. Please try again in a moment."),
    (openai.APITimeoutError(request=REQUEST),
     "That took longer than it should. Please try again."),
    (openai.APIConnectionError(request=REQUEST),
     "I couldn't reach the service. Please try again."),
    (openai.InternalServerError("boom", response=httpx.Response(500, request=REQUEST), body=None),
     "Something went wrong. Please try again."),
]


@pytest.mark.parametrize("error, visitor_message", OPENAI_FAILURES)
def test_model_api_failure_is_a_503_with_a_readable_message(error, visitor_message):
    client, stores = make_client()
    broken = MagicMock()
    broken.chat.completions.create.side_effect = error

    with client, patch.object(llm, "_get_client", return_value=broken):
        response = chat(client)

    assert response.status_code == 503
    assert response.json() == {"detail": visitor_message}
    # Nothing half-stored: the turn is not saved without a reply.
    assert stores["conversation_store"].get("t1", "c1") is None


def test_model_failure_after_a_tool_call_keeps_the_recorded_request():
    """The model asks for contact_team, then fails writing the answer. The
    visitor sees an error, but the request it recorded is not lost."""
    client, stores = make_client()
    calls = [
        reply(None, [tool_call("contact_team", json.dumps({"reason": "call me"}))]),
        llm.LLMError("Something went wrong. Please try again."),
    ]

    def model(messages, tools, **_):
        result = calls.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with client, patch("app.agent.orchestrator.complete_with_tools", side_effect=model):
        assert chat(client, "Can someone call me?").status_code == 503

    lead = stores["lead_store"].get("t1", "c1")
    assert [item.kind for item in lead.items] == [ItemKind.CONTACT_REQUEST]


# --- broken tool calls --------------------------------------------------------


def run_turn(model_replies, message="Can someone call me?"):
    store, leads = InMemoryConversationStore(), InMemoryLeadStore()
    with patch("app.agent.orchestrator.complete_with_tools", side_effect=model_replies):
        text, _, _ = generate_reply(tenant(), "c1", message, store, lead_store=leads)
    return text, leads


def test_tool_arguments_that_are_not_json_still_record_the_request():
    text, leads = run_turn([
        reply(None, [tool_call("contact_team", "{not json")]),
        reply("Someone will be in touch."),
    ])
    assert text == "Someone will be in touch."
    item = leads.get("t1", "c1").items[0]
    # No usable reason from the model, so the visitor's message stands in.
    assert item.kind == ItemKind.CONTACT_REQUEST
    assert item.detail == "Can someone call me?"


def test_unknown_tool_is_reported_back_and_the_turn_continues():
    seen = []

    def model(messages, tools, **_):
        seen.append(messages)
        if len(seen) == 1:
            return reply(None, [tool_call("delete_everything", "{}")])
        return reply("Here is what I know.")

    text, _ = run_turn(model, "hello")
    assert text == "Here is what I know."
    assert seen[1][-1] == {"role": "tool", "tool_call_id": "call_1",
                           "content": "Error: unknown tool 'delete_everything'"}


def test_a_model_that_keeps_calling_tools_is_stopped():
    endless = lambda messages, tools, **_: reply(  # noqa: E731
        None, [tool_call("record_visitor_details", json.dumps({"name": "Sam"}))]
    )
    text, _ = run_turn(endless, "I'm Sam")
    assert text == "I'm having trouble with that one. Could you rephrase it?"


def test_tool_arguments_of_the_wrong_type_are_ignored():
    text, leads = run_turn([
        reply(None, [tool_call("record_visitor_details",
                               json.dumps({"name": ["not", "a", "string"], "country": "India",
                                           "is_admin": "yes"}))]),
        reply("Thanks!"),
    ], "I'm from India")
    lead = leads.get("t1", "c1")
    assert lead.country == "India"
    assert lead.name in ("", None)
    assert not hasattr(lead, "is_admin")


# --- the lead email -----------------------------------------------------------


@pytest.fixture
def resend_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))


def test_lead_summary_failure_still_sends_the_email(resend_configured):
    """The summary/labelling call fails: the team still gets the email,
    with every question, unlabelled, and a placeholder summary."""
    store, leads = InMemoryConversationStore(), InMemoryLeadStore()
    store.append_turn("t1", "c1", "Is there an exam?", "I don't have details.", is_question=True)
    leads.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    leads.add_item("t1", "c1", PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="call me"))

    with patch("app.agent.orchestrator.complete",
               side_effect=llm.LLMError("down")), \
         patch("app.agent.orchestrator.send_lead_email", return_value=True) as send:
        after_reply(tenant(), "c1", "Please contact me", "Will do.", store, leads, get_settings())

    send.assert_called_once()
    _, _, summary, asked = send.call_args[0]
    assert summary == "(summary unavailable)"
    assert [(q.text, q.status) for q in asked] == [
        ("Is there an exam?", "unchecked"), ("Please contact me", "unchecked"),
    ]
    assert all(item.is_sent for item in leads.get("t1", "c1").items)


def test_running_summary_failure_does_not_skip_the_lead_email(resend_configured, monkeypatch):
    """Once history passes summarize_trigger, persist_turn makes a model call.
    If that fails, the contact-request email for the turn should still go."""
    monkeypatch.setattr(get_settings(), "summarize_trigger", 2)
    store, leads = InMemoryConversationStore(), InMemoryLeadStore()
    for i in range(2):
        store.append_turn("t1", "c1", f"question {i}?", "answer", is_question=True)
    leads.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    leads.add_item("t1", "c1", PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="call me"))

    with patch("app.agent.orchestrator.complete", side_effect=llm.LLMError("down")), \
         patch("app.agent.orchestrator.send_lead_email", return_value=True) as send:
        after_reply(tenant(), "c1", "Please call me", "Sure.", store, leads, get_settings())

    send.assert_called_once()
    assert store.get("t1", "c1").messages[-1].content == "Sure."  # the turn was kept


def test_a_failed_turn_write_still_emails_and_flags(resend_configured, caplog):
    """Firestore refuses the turn: the email still goes, and the failure is
    logged as an error (Sentry reports it)."""
    store, leads = InMemoryConversationStore(), InMemoryLeadStore()
    store.append_turn("t1", "c1", "When is the course?", "March.", is_question=True)
    leads.upsert_fields("t1", "c1", {"email": "sam@example.com"})
    leads.add_item("t1", "c1", PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="call me"))

    with patch.object(store, "append_turn", side_effect=RuntimeError("firestore down")), \
         patch("app.agent.orchestrator.complete", return_value='{"summary": "S."}'), \
         patch("app.agent.orchestrator.send_lead_email", return_value=True) as send:
        after_reply(tenant(), "c1", "Please call me", "Sure.", store, leads, get_settings())

    send.assert_called_once()
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert any("persist_turn failed" in r.getMessage() for r in errors)


# --- stores -------------------------------------------------------------------


class Broken:
    """A store whose every method raises, like Firestore during an outage."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError(f"firestore unavailable ({name})")
        return fail


def test_store_outage_is_a_plain_500_without_details():
    client, _ = make_client(conversation_store=Broken())
    with client, patch("app.agent.orchestrator.complete_with_tools", return_value=reply()):
        response = chat(client)
    assert response.status_code == 500
    assert "firestore" not in response.text.lower()


def test_tenant_store_outage_is_a_plain_500():
    client, _ = make_client(tenant_store=Broken())
    with client:
        response = client.get("/config", headers={"X-Site-Key": "pk_valid"})
    assert response.status_code == 500
    assert "firestore" not in response.text.lower()


def test_facts_outage_still_gets_a_reply():
    """Like retrieval, a facts sheet that cannot be read does not fail the
    turn: the reply goes without facts."""
    client, _ = make_client(fact_store=Broken())
    with client, patch("app.agent.orchestrator.complete_with_tools", return_value=reply("Hi")):
        assert chat(client).status_code == 200


def test_lead_store_outage_still_gets_a_reply():
    """Leads are secondary to answering: the visitor should still get a
    reply when the lead store is down (the failure logged for Sentry)."""
    client, _ = make_client(lead_store=Broken())
    with client, patch("app.agent.orchestrator.complete_with_tools", return_value=reply("Hi")):
        assert chat(client).status_code == 200


def test_lead_store_outage_during_a_contact_request():
    """The visitor gives an email and asks to be contacted while the lead
    store is down: they still get a reply, the bot is told to give the
    direct contact details instead, and the failure is logged as an error."""
    tool_results = []

    def model(messages, tools, **_):
        if messages[-1]["role"] == "tool":
            tool_results.append(messages[-1]["content"])
            return reply("Sorry, please email the school directly.")
        return reply(None, [tool_call("contact_team", json.dumps({"reason": "call me"}))])

    client, _ = make_client(lead_store=Broken())
    # The app's startup replaces logging handlers, so caplog cannot see
    # these; the logger itself is watched instead.
    with client, patch("app.agent.orchestrator.complete_with_tools", side_effect=model), \
         patch("app.leads.store.logger.exception") as logged:
        response = chat(client, "my email is sam@example.com, please contact me")
    assert response.status_code == 200
    assert response.json()["confirmation"] is None
    assert "Could not reach the team system" in tool_results[0]
    assert logged.called
