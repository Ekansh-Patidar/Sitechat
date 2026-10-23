"""The daily token budget per tenant (backlog 103): a cap on cost, not
request count."""

from unittest.mock import MagicMock, patch

import pytest

import app.agent.llm as llm
import app.usage.budget as budget
from app.agent.orchestrator import generate_reply
from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.tenants.models import Tenant
from app.usage.store import InMemoryUsageStore


@pytest.fixture(autouse=True)
def fresh_budget_cache():
    budget._cache.clear()
    budget._warned.clear()
    yield
    budget._cache.clear()
    budget._warned.clear()


@pytest.fixture
def openai(monkeypatch):
    """The real llm module over a fake OpenAI client: every call costs 1,000 tokens."""
    message = MagicMock(content="Hi there.", tool_calls=None)
    response = MagicMock()
    response.choices = [MagicMock(message=message, finish_reason="stop")]
    response.usage.total_tokens = 1000
    client = MagicMock()
    client.chat.completions.create.return_value = response
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    return client.chat.completions.create


TENANT = Tenant(tenant_id="t1", site_key="k", display_name="Pyramid Yogshala")


def chat(usage, tenant=TENANT, message="hello"):
    return generate_reply(tenant, "c1", message, InMemoryConversationStore(), usage_store=usage)


def test_each_reply_records_the_tokens_it_used(openai):
    usage = InMemoryUsageStore()
    chat(usage)
    chat(usage)
    assert usage.tokens_on("t1", budget._today()) == 2000


def test_once_the_budget_is_spent_nothing_more_is_spent(openai, monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "daily_token_budget", 2500)
    usage = InMemoryUsageStore()
    chat(usage)
    chat(usage)
    chat(usage)  # 3,000 used: over, but this one was allowed to start
    openai.reset_mock()

    with pytest.raises(llm.LLMError) as error:
        chat(usage)
    assert "taking a break until tomorrow" in error.value.user_message
    assert "Pyramid Yogshala" in error.value.user_message
    openai.assert_not_called()
    # Reported once, as an error (Sentry), not on every refused message.
    with pytest.raises(llm.LLMError):
        chat(usage)
    assert sum("daily token budget" in r.getMessage() for r in caplog.records
               if r.levelname == "ERROR") == 1


def test_a_new_day_starts_a_new_budget(openai, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_token_budget", 1000)
    usage = InMemoryUsageStore()
    chat(usage)
    with pytest.raises(llm.LLMError):
        chat(usage)
    monkeypatch.setattr(budget, "_today", lambda: "2099-01-01")
    assert chat(usage)[0] == "Hi there."


def test_a_tenant_budget_overrides_the_default(openai, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_token_budget", 1000)
    busy = TENANT.model_copy(update={"daily_token_budget": 5000})
    usage = InMemoryUsageStore()
    for _ in range(3):
        chat(usage, tenant=busy)  # would be refused on the default


def test_zero_means_no_budget(openai, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_token_budget", 0)
    usage = InMemoryUsageStore()
    usage.add_tokens("t1", budget._today(), 10**9)
    assert chat(usage)[0] == "Hi there."


def test_a_broken_counter_does_not_stop_the_chat(openai, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_token_budget", 1000)
    broken = MagicMock()
    broken.tokens_on.side_effect = RuntimeError("firestore down")
    broken.add_tokens.side_effect = RuntimeError("firestore down")
    assert chat(broken)[0] == "Hi there."
    assert chat(broken)[0] == "Hi there."


def test_the_api_returns_the_message_with_a_503(monkeypatch):
    from tests.test_failure_injection import make_client

    monkeypatch.setattr(get_settings(), "daily_token_budget", 1)
    client, stores = make_client()
    stores["usage_store"].add_tokens("t1", budget._today(), 5)
    with client, patch("app.agent.orchestrator.complete_with_tools") as model:
        response = client.post(
            "/chat", json={"message": "hi"},
            headers={"X-Site-Key": "pk_valid", "Origin": "https://example.com"},
        )
    assert response.status_code == 503
    assert "taking a break" in response.json()["detail"]
    model.assert_not_called()
