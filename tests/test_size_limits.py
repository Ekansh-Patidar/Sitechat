"""A conversation stays writable (Firestore's 1 MiB document limit) and
model-supplied visitor details stay short (backlog 93)."""

import json

from app.agent.orchestrator import VISITOR_DETAIL_LIMIT, _execute_tool
from app.api.schemas import ChatRequest
from app.conversations.models import REPLY_LIMIT
from app.conversations.store import MAX_MESSAGES, MAX_QUESTIONS, InMemoryConversationStore
from app.leads.store import InMemoryLeadStore
from app.tenants.models import Tenant

LONGEST_MESSAGE = next(
    m.max_length for m in ChatRequest.model_fields["message"].metadata
    if getattr(m, "max_length", None)
)


def test_a_scripted_visitor_cannot_grow_a_conversation_past_firestores_limit():
    store = InMemoryConversationStore()
    question = "Q" * LONGEST_MESSAGE
    reply = "R" * 3000  # longer than replies get (reply_max_tokens)
    for _ in range(MAX_QUESTIONS * 3):
        store.append_turn("t1", "c1", question, reply, is_question=True)

    convo = store.get("t1", "c1")
    assert len(convo.questions) == MAX_QUESTIONS
    assert len(convo.messages) == MAX_MESSAGES
    assert convo.messages[-1].content == reply  # the newest kept
    size = len(json.dumps(convo.model_dump(mode="json")).encode())
    assert size < 1024 * 1024 * 0.9, size


def test_questions_past_the_cap_are_not_recorded_but_turns_still_are():
    store = InMemoryConversationStore()
    for i in range(MAX_QUESTIONS + 5):
        store.append_turn("t1", "c1", f"question {i}?", "answer", is_question=True)
    convo = store.get("t1", "c1")
    assert convo.questions[-1].text == f"question {MAX_QUESTIONS - 1}?"
    assert convo.messages[-2].content == f"question {MAX_QUESTIONS + 4}?"
    assert all(len(q.reply) <= REPLY_LIMIT for q in convo.questions)


def test_visitor_details_from_the_model_are_capped():
    leads = InMemoryLeadStore()
    tenant = Tenant(tenant_id="t1", site_key="k", display_name="School")
    arguments = json.dumps({"name": "  Sam  ", "country": "X" * 5000, "course_interest": ""})
    _execute_tool("record_visitor_details", arguments, tenant, "c1", leads, "hi")

    lead = leads.get("t1", "c1")
    assert lead.name == "Sam"
    assert len(lead.country) == VISITOR_DETAIL_LIMIT
    assert not lead.course_interest  # empty strings are ignored, not stored
