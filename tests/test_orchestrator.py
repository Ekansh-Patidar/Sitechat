"""Tests for the orchestration layer.

The model call is mocked, so these run with no network and no API cost.
They check what we control: what goes into the prompt, what gets stored,
and that tenants stay separate.
"""

from unittest.mock import patch

import pytest

from app.agent.orchestrator import generate_reply
from app.conversations.store import InMemoryConversationStore
from app.tenants.models import Tenant

from unittest.mock import MagicMock
from app.agent.orchestrator import persist_turn
from app.config import get_settings

def reply_and_store(tenant, conversation_id, message, store):
    """Generate a reply and store it, as the API route does.

    generate_reply no longer writes: the route hands that to a background
    task so the visitor does not wait for it. Tests that check what was
    stored have to do the same.
    """
    reply, retrieved, stored = generate_reply(
        tenant, conversation_id, message, store
    )
    persist_turn(
        tenant.tenant_id, conversation_id, stored, reply, store, get_settings()
    )
    return reply


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


@patch("app.agent.orchestrator.complete_with_tools")
def test_reply_is_returned_and_stored(mock_complete):
    mock_complete.return_value = make_message("Hello!")
    store = InMemoryConversationStore()

    reply = reply_and_store(make_tenant(), "c1", "hi", store)

    assert reply == "Hello!"
    convo = store.get("t1", "c1")
    assert len(convo.messages) == 2

@patch("app.agent.orchestrator.complete_with_tools")
def test_history_is_sent_to_the_model(mock_complete):
    mock_complete.return_value = make_message("ok")
    store = InMemoryConversationStore()
    tenant = make_tenant()

    reply_and_store(tenant, "c1", "first", store)
    reply_and_store(tenant, "c1", "second", store)

    sent = mock_complete.call_args[0][0]
    contents = [m["content"] for m in sent]
    assert "first" in contents
    assert "second" in contents


@patch("app.agent.orchestrator.complete_with_tools")
def test_tenant_name_appears_in_system_prompt(mock_complete):
    mock_complete.return_value = make_message("ok")
    store = InMemoryConversationStore()

    generate_reply(make_tenant(), "c1", "hi", store)

    sent = mock_complete.call_args[0][0]
    assert sent[0]["role"] == "system"
    assert "Test School" in sent[0]["content"]


@patch("app.agent.orchestrator.complete_with_tools")
def test_conversations_are_isolated_between_tenants(mock_complete):
    """The same conversation id under two tenants must stay separate."""
    mock_complete.return_value = make_message("ok")
    store = InMemoryConversationStore()

    reply_and_store(make_tenant("t1"), "same-id", "tenant one message", store)
    reply_and_store(make_tenant("t2"), "same-id", "tenant two message", store)

    t1 = store.get("t1", "same-id")
    t2 = store.get("t2", "same-id")

    assert len(t1.messages) == 2
    assert len(t2.messages) == 2
    assert t1.messages[0].content == "tenant one message"
    assert t2.messages[0].content == "tenant two message"


@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.complete_with_tools")
def test_summarization_trims_history(mock_complete_tools, mock_complete):
    mock_complete_tools.return_value = make_message("ok")
    mock_complete.return_value = "summary text"
    store = InMemoryConversationStore()
    tenant = make_tenant()

    for i in range(10):
        reply_and_store(tenant, "c1", f"message {i}", store)

    convo = store.get("t1", "c1")
    assert len(convo.messages) <= 14
    assert convo.summary != ""

@patch("app.agent.orchestrator.complete")
@patch("app.agent.orchestrator.complete_with_tools")
def test_turn_appended_during_summarization_is_kept(
    mock_complete_tools, mock_complete
):
    """Summarizing takes seconds; a turn stored meanwhile must survive."""
    mock_complete_tools.return_value = make_message("ok")
    store = InMemoryConversationStore()
    settings = get_settings()

    # Fill history to the trigger without summarizing.
    for i in range(settings.summarize_trigger // 2):
        store.append_turn("t1", "c1", f"message {i}", "ok")

    def summary_while_a_turn_arrives(prompt):
        store.append_turn("t1", "c1", "arrived mid-summary", "late reply")
        return "summary text"

    mock_complete.side_effect = summary_while_a_turn_arrives

    persist_turn("t1", "c1", "trigger", "ok", store, settings)

    contents = [m.content for m in store.get("t1", "c1").messages]
    assert "arrived mid-summary" in contents
    assert "late reply" in contents
    assert contents[-2:] == ["arrived mid-summary", "late reply"]
    assert store.get("t1", "c1").summary == "summary text"


def test_second_summary_of_the_same_messages_changes_nothing():
    """Two summaries racing over one history: only the first applies."""
    store = InMemoryConversationStore()
    for i in range(4):
        store.append_turn("t1", "c1", f"message {i}", "ok")
    summarized = list(store.get("t1", "c1").messages[:4])

    assert store.apply_summary("t1", "c1", "first", summarized) is True
    assert store.apply_summary("t1", "c1", "second", summarized) is False

    convo = store.get("t1", "c1")
    assert convo.summary == "first"
    assert [m.content for m in convo.messages] == [
        "message 2", "ok", "message 3", "ok"
    ]


class FailingVectorStore:
    """Pinecone being down."""

    def search(self, tenant_id, query_vector, top_k):
        raise ConnectionError("pinecone unreachable")


@patch("app.retrieval.pipeline.embed_query", return_value=[0.0] * 1536)
@patch("app.agent.orchestrator.complete_with_tools")
def test_vector_store_outage_still_gets_a_reply(mock_complete, _, caplog):
    mock_complete.return_value = make_message("I don't have details on that.")

    reply, retrieved, _ = generate_reply(
        make_tenant(), "c1", "what are the course dates?",
        InMemoryConversationStore(), vector_store=FailingVectorStore(),
    )

    assert reply == "I don't have details on that."
    assert retrieved == []
    assert "Retrieval failed for c1" in caplog.text
    sent = " ".join(m["content"] for m in mock_complete.call_args[0][0])
    assert "Relevant extracts" not in sent


@patch("app.retrieval.pipeline.embed_query", side_effect=RuntimeError("openai down"))
@patch("app.agent.orchestrator.complete_with_tools")
def test_embedding_outage_still_gets_a_reply(mock_complete, _):
    mock_complete.return_value = make_message("ok")

    reply, retrieved, _ = generate_reply(
        make_tenant(), "c1", "what are the course dates?",
        InMemoryConversationStore(), vector_store=FailingVectorStore(),
    )

    assert reply == "ok"
    assert retrieved == []


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("You need a 200-hour certificate [2]. As you have one, yes.",
         "You need a 200-hour certificate. As you have one, yes."),
        ("Fees are listed [1, 3] below.", "Fees are listed below."),
        ("A 28-day course.", "A 28-day course."),
    ],
)
def test_citation_markers_are_removed(raw, clean):
    from app.agent.orchestrator import _strip_citation_markers

    assert _strip_citation_markers(raw) == clean


# --- programme focus ---------------------------------------------------------

from app.programmes.models import Programme, ProgrammeCatalogue
from app.programmes.store import InMemoryProgrammeStore


class RecordingVectorStore:
    def __init__(self):
        self.searches = []

    def search(self, tenant_id, query_vector, top_k, programmes=None):
        self.searches.append(programmes)
        return []


def programme_store():
    store = InMemoryProgrammeStore()
    store.save(ProgrammeCatalogue(
        tenant_id="t1",
        programmes=[Programme(programme_id="300", name="300-Hour TTC",
                              aliases=["300 hour"], sections=["web | 300-Hour"])],
    ))
    return store


@patch("app.retrieval.pipeline.embed_query", return_value=[0.0])
@patch("app.agent.orchestrator.complete_with_tools")
def test_focus_carries_over_to_a_follow_up(mock_complete, mock_embed):
    import app.programmes.store as programme_cache

    programme_cache._cache.clear()
    mock_complete.return_value = make_message("ok")
    store, vectors, programmes = InMemoryConversationStore(), RecordingVectorStore(), programme_store()

    for message in ["Tell me about the 300-hour course", "How much does it cost?"]:
        reply, _, stored = generate_reply(
            make_tenant(), "c1", message, store,
            vector_store=vectors, programme_store=programmes,
        )
        persist_turn("t1", "c1", stored, reply, store, get_settings())

    assert store.get("t1", "c1").focus == ["300"]
    # Each turn searches the focused programme as well as everything.
    assert vectors.searches.count(["300"]) == 2
    # The search text is the visitor's own message; the focus is applied
    # as a filter, so it cannot pull the general results towards itself.
    assert mock_embed.call_args_list[-1][0][0] == "How much does it cost?"


@patch("app.retrieval.pipeline.embed_query", return_value=[0.0])
@patch("app.agent.orchestrator.complete_with_tools")
def test_without_a_catalogue_there_is_no_focus(mock_complete, _):
    mock_complete.return_value = make_message("ok")
    vectors = RecordingVectorStore()

    generate_reply(make_tenant(), "c1", "Tell me about the 300-hour course",
                   InMemoryConversationStore(), vector_store=vectors)

    assert vectors.searches == [None]
