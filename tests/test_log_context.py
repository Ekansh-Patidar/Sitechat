"""Every log line of a chat carries its request, tenant and conversation id
(backlog 86), including lines from the worker thread and the background step.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api.dependencies as deps
from app.agent.orchestrator import send_due_digests
from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.documents.store import InMemoryChunkStore
from app.facts.store import InMemoryFactStore
from app.leads.store import InMemoryLeadStore
from app.logging_setup import JsonFormatter
from app.main import create_app
from app.programmes.store import InMemoryProgrammeStore
from app.usage.store import InMemoryUsageStore
from app.feedback.store import InMemoryFeedbackStore
from app.rate_limit import limiter
from app.tenants.models import Tenant
from app.tenants.store import InMemoryTenantStore
from tests.test_api_security import NoOpVectorStore
from tests.test_contact_confirmation import model


class Capture(logging.Handler):
    """Formats each record as production would, as it is emitted."""

    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def logs():
    handler = Capture()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler.lines
    root.removeHandler(handler)
    root.setLevel(old_level)


@pytest.fixture
def stores(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))
    tenants = InMemoryTenantStore()
    tenants.save(Tenant(tenant_id="t1", site_key="pk_valid", display_name="School",
                        allowed_domains=["example.com"], team_email="team@example.com"))
    return dict(
        tenant_store=tenants, conversation_store=InMemoryConversationStore(),
        fact_store=InMemoryFactStore(), vector_store=NoOpVectorStore(),
        chunk_store=InMemoryChunkStore(), lead_store=InMemoryLeadStore(),
        programme_store=InMemoryProgrammeStore(),
        usage_store=InMemoryUsageStore(),
        feedback_store=InMemoryFeedbackStore(),
    )


@pytest.fixture
def client(stores):
    deps._cache.clear()
    limiter.reset()
    with patch("app.agent.orchestrator.complete_with_tools", side_effect=model), \
         patch("app.agent.orchestrator.complete", return_value='{"summary": "S."}'), \
         patch("app.agent.orchestrator.send_lead_email", return_value=True), \
         patch("app.retrieval.pipeline.embed_query", return_value=[0.0] * 1536), \
         TestClient(create_app(**stores)) as c:
        yield c


def say(client, message, conversation_id):
    response = client.post(
        "/chat", json={"message": message, "conversation_id": conversation_id},
        headers={"X-Site-Key": "pk_valid", "Origin": "https://example.com"},
    )
    assert response.status_code == 200


def ours(lines):
    return [line for line in lines if line["logger"].startswith("app.")]


def test_every_line_of_a_chat_carries_its_ids(client, logs):
    say(client, "my email is sam@example.com", "c1")
    say(client, "please contact me", "c1")  # emails the team in the background

    lines = ours(logs)
    messages = " | ".join(line["message"] for line in lines)
    # From the route, the reply (worker thread) and after_reply (background).
    assert "Chat request" in messages
    assert "Timing for" in messages
    assert "Captured contact details" in messages
    assert "mail" in messages.lower()
    for line in lines:
        assert line.get("tenant_id") == "t1", line
        assert line.get("conversation_id") == "c1", line
        assert line.get("request_id"), line


def test_each_request_has_its_own_request_id(client, logs):
    say(client, "hello", "c1")
    say(client, "hello again", "c2")
    by_conversation = {}
    for line in ours(logs):
        by_conversation.setdefault(line["conversation_id"], set()).add(line["request_id"])
    assert len(by_conversation["c1"]) == len(by_conversation["c2"]) == 1
    assert by_conversation["c1"] != by_conversation["c2"]


def test_digest_lines_name_the_conversation_they_are_about(client, stores, logs):
    # A question and an email, no contact request: flagged for the digest.
    say(client, "When is the next course?", "c9")
    say(client, "sam@example.com", "c9")
    logs.clear()

    later = datetime.now(timezone.utc) + timedelta(hours=1)
    counts = send_due_digests(stores["tenant_store"], stores["conversation_store"],
                              stores["lead_store"], now=later)
    assert counts["sent"] == 1

    lines = ours(logs)
    sweep = [line for line in lines if "Lead digest sweep" in line["message"]]
    about = [line for line in lines if line not in sweep]
    assert about and all(line.get("conversation_id") == "c9" for line in about)
    assert all(line.get("tenant_id") == "t1" for line in about)
    assert sweep and "conversation_id" not in sweep[0]  # reset after the loop
