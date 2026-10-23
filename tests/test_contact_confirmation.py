"""The confirmation card in the chat when a contact request is received
(backlog 82, part 3): when it appears, and what it says."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api.dependencies as deps
from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.documents.store import InMemoryChunkStore
from app.facts.store import InMemoryFactStore
from app.leads.store import InMemoryLeadStore
from app.main import create_app
from app.programmes.store import InMemoryProgrammeStore
from app.usage.store import InMemoryUsageStore
from app.feedback.store import InMemoryFeedbackStore
from app.rate_limit import limiter
from app.tenants.models import Tenant
from app.tenants.store import InMemoryTenantStore
from tests.test_api_security import NoOpVectorStore


def reply(content=None, tool_calls=None):
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    return message


def contact_team_call():
    call = MagicMock()
    call.id = "call_1"
    call.function.name = "contact_team"
    call.function.arguments = json.dumps({"reason": "wants a call"})
    call.model_dump.return_value = {
        "id": "call_1", "type": "function",
        "function": {"name": "contact_team", "arguments": call.function.arguments},
    }
    return call


def model(messages, tools, **_):
    """Calls contact_team when asked to, then answers; otherwise answers."""
    if messages[-1]["role"] == "tool":
        return reply("Someone from the team will be in touch.")
    if "contact me" in messages[-1]["content"]:
        return reply(None, [contact_team_call()])
    return reply("Thanks!")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))
    tenants = InMemoryTenantStore()
    tenants.save(Tenant(tenant_id="t1", site_key="pk_valid", display_name="Pyramid Yogshala",
                        allowed_domains=["example.com"], team_email="team@example.com",
                        reply_time="within 24 hours"))
    tenants.save(Tenant(tenant_id="t2", site_key="pk_no_email", display_name="No Email School",
                        allowed_domains=["example.com"]))
    deps._cache.clear()
    limiter.reset()
    app = create_app(
        tenant_store=tenants, conversation_store=InMemoryConversationStore(),
        fact_store=InMemoryFactStore(), vector_store=NoOpVectorStore(),
        chunk_store=InMemoryChunkStore(), lead_store=InMemoryLeadStore(),
        programme_store=InMemoryProgrammeStore(),
        usage_store=InMemoryUsageStore(),
        feedback_store=InMemoryFeedbackStore(),
    )
    with patch("app.agent.orchestrator.complete_with_tools", side_effect=model), \
         patch("app.agent.orchestrator.complete", return_value='{"summary": "S."}'), \
         patch("app.agent.orchestrator.send_lead_email", return_value=True), \
         patch("app.retrieval.pipeline.embed_query", return_value=[0.0] * 1536), \
         TestClient(app) as c:
        yield c


def say(client, message, site_key="pk_valid"):
    response = client.post(
        "/chat",
        json={"message": message, "conversation_id": "c1"},
        headers={"X-Site-Key": site_key, "Origin": "https://example.com"},
    )
    assert response.status_code == 200
    return response.json()


EXPECTED = ("Request received. Pyramid Yogshala will reply to the email address "
            "you gave, usually within 24 hours.")


def test_request_with_an_email_already_given_is_confirmed_once(client):
    assert say(client, "my email is sam@example.com")["confirmation"] is None
    assert say(client, "please contact me")["confirmation"] == EXPECTED
    # Emailed in the background after that reply; nothing more to confirm.
    assert say(client, "thanks")["confirmation"] is None


def test_request_before_the_email_is_confirmed_when_the_email_arrives(client):
    assert say(client, "please contact me")["confirmation"] is None  # no address yet
    assert say(client, "sam@example.com")["confirmation"] == EXPECTED
    assert say(client, "great")["confirmation"] is None


def test_phone_only(client):
    say(client, "call me on +44 7700 900123")
    assert say(client, "please contact me")["confirmation"] == (
        "Request received. Pyramid Yogshala will reply to the phone number you "
        "gave, usually within 24 hours."
    )


def test_no_reply_time_promised_leaves_it_out(client):
    # Same flow, a tenant without reply_time.
    tenants = client.app.state.tenant_store
    tenant = tenants.get_by_id("t1").model_copy(update={"reply_time": ""})
    tenants.save(tenant)
    deps._cache.clear()
    say(client, "sam@example.com")
    assert say(client, "please contact me")["confirmation"] == (
        "Request received. Pyramid Yogshala will reply to the email address you gave."
    )


def test_no_confirmation_when_the_team_cannot_be_emailed(client):
    say(client, "sam@example.com", site_key="pk_no_email")
    assert say(client, "please contact me", site_key="pk_no_email")["confirmation"] is None
