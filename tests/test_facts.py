"""Tests for structured facts.

The important behaviour: facts reach the model, and they reach it exactly
as written. A test that only checks "facts were included" would pass even
if the value were mangled, so these check the literal text.
"""

from unittest.mock import patch

from app.agent.orchestrator import generate_reply
from app.agent.prompts import build_facts_message
from app.conversations.store import InMemoryConversationStore
from app.facts.models import Fact, FactSheet
from app.facts.store import InMemoryFactStore
import app.facts.store as fact_store_module
from app.tenants.models import Tenant

from unittest.mock import MagicMock


def make_message(content: str = "ok"):
    """A model reply with no tool calls."""
    message = MagicMock()
    message.content = content
    message.tool_calls = None
    return message


def make_tenant(tenant_id: str = "t1") -> Tenant:
    return Tenant(
        tenant_id=tenant_id,
        site_key=f"pk_{tenant_id}",
        display_name="Test School",
    )


def make_sheet(tenant_id: str = "t1") -> FactSheet:
    return FactSheet(
        tenant_id=tenant_id,
        facts=[
            Fact(category="Fees", label="Course fee", value="USD 1,450"),
            Fact(category="Dates", label="Next course", value="2 March 2027"),
        ],
    )


def setup_function():
    """Clear the facts cache so tests don't affect each other."""
    fact_store_module._cache.clear()


def test_no_facts_produces_no_message():
    assert build_facts_message(None) is None
    assert build_facts_message(FactSheet(tenant_id="t1")) is None


def test_facts_are_grouped_by_category():
    message = build_facts_message(make_sheet())
    assert "Fees:" in message
    assert "Dates:" in message


@patch("app.agent.orchestrator.complete_with_tools")
def test_facts_reach_the_model_verbatim(mock_complete):
    """The exact value must appear, not a paraphrase."""
    mock_complete.return_value = make_message("ok")

    fact_store = InMemoryFactStore()
    fact_store.save(make_sheet())

    generate_reply(
        make_tenant(),
        "c1",
        "how much is the course?",
        InMemoryConversationStore(),
        fact_store,
    )

    sent = mock_complete.call_args[0][0]
    combined = " ".join(m["content"] for m in sent)
    assert "USD 1,450" in combined


@patch("app.agent.orchestrator.complete_with_tools")
def test_tenants_only_see_their_own_facts(mock_complete):
    """Tenant two must never receive tenant one's prices."""
    mock_complete.return_value = make_message("ok")

    fact_store = InMemoryFactStore()
    fact_store.save(make_sheet("t1"))

    generate_reply(
        make_tenant("t2"),
        "c1",
        "how much?",
        InMemoryConversationStore(),
        fact_store,
    )

    sent = mock_complete.call_args[0][0]
    combined = " ".join(m["content"] for m in sent)
    assert "USD 1,450" not in combined


@patch("app.agent.orchestrator.complete_with_tools")
def test_works_without_a_fact_store(mock_complete):
    mock_complete.return_value = make_message("ok")
    reply, _, _ = generate_reply(
        make_tenant(), "c1", "hi", InMemoryConversationStore()
    )
    assert reply == "ok"