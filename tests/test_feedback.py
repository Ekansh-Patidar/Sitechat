"""Thumbs up/down on replies (backlog 71): the API and the review script."""

from datetime import datetime, timedelta, timezone

import pytest

from scripts.feedback import collect
from tests.test_failure_injection import make_client, reply
from unittest.mock import patch

HEADERS = {"X-Site-Key": "pk_valid", "Origin": "https://example.com"}


@pytest.fixture
def client():
    client, stores = make_client()
    with client, patch("app.agent.orchestrator.complete_with_tools",
                       return_value=reply("Our 200-hour course costs USD 1,450.")):
        yield client, stores


def chat(client, message="What does the 200-hour course cost?"):
    response = client.post("/chat", json={"message": message, "conversation_id": "c1"},
                           headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def rate(client, reply_id, rating, reason=None, conversation_id="c1", headers=HEADERS):
    body = {"conversation_id": conversation_id, "reply_id": reply_id, "rating": rating}
    if reason:
        body["reason"] = reason
    return client.post("/feedback", json=body, headers=headers)


def saved(stores):
    return stores["feedback_store"].since("t1", datetime(2000, 1, 1, tzinfo=timezone.utc))


def test_each_reply_has_its_own_id(client):
    client, _ = client
    first, second = chat(client)["reply_id"], chat(client)["reply_id"]
    assert len(first) == 16 and first != second


def test_a_rating_is_stored_and_a_second_tap_replaces_it(client):
    client, stores = client
    reply_id = chat(client)["reply_id"]
    assert rate(client, reply_id, "up").status_code == 204
    assert rate(client, reply_id, "down", "wrong").status_code == 204

    (f,) = saved(stores)
    assert (f.tenant_id, f.conversation_id, f.reply_id) == ("t1", "c1", reply_id)
    assert (f.rating, f.reason) == ("down", "wrong")


def test_a_reason_is_kept_only_with_a_thumbs_down(client):
    client, stores = client
    reply_id = chat(client)["reply_id"]
    rate(client, reply_id, "up", "wrong")
    assert saved(stores)[0].reason is None


@pytest.mark.parametrize("body", [
    {"conversation_id": "c1", "reply_id": "not-an-id", "rating": "up"},
    {"conversation_id": "c1", "reply_id": "0123456789abcdef", "rating": "meh"},
    {"conversation_id": "c1", "reply_id": "0123456789abcdef", "rating": "down", "reason": "rude"},
    {"conversation_id": "a/b", "reply_id": "0123456789abcdef", "rating": "up"},
])
def test_malformed_feedback_is_refused(client, body):
    client, stores = client
    assert client.post("/feedback", json=body, headers=HEADERS).status_code == 422
    assert not saved(stores)


def test_feedback_needs_a_valid_site_key_and_domain(client):
    client, stores = client
    reply_id = chat(client)["reply_id"]
    assert rate(client, reply_id, "up", headers={**HEADERS, "X-Site-Key": "pk_nope"}).status_code == 401
    assert rate(client, reply_id, "up", headers={**HEADERS, "Origin": "https://evil.com"}).status_code == 403
    assert not saved(stores)


def test_feedback_is_rate_limited(client, monkeypatch):
    from app.config import get_settings
    client, _ = client
    monkeypatch.setattr(get_settings(), "rate_limit_feedback_per_visitor", "2/minute")
    reply_id = chat(client)["reply_id"]
    codes = [rate(client, reply_id, "up").status_code for _ in range(3)]
    assert codes == [204, 204, 429]


def test_the_review_lists_thumbs_down_with_the_question_and_reply(client):
    client, stores = client
    good = chat(client, "Hi there")["reply_id"]
    bad = chat(client, "What does the 200-hour course cost?")["reply_id"]
    rate(client, good, "up")
    rate(client, bad, "down", "not_answered")

    since = datetime.now(timezone.utc) - timedelta(days=1)
    rated = collect(stores["feedback_store"], stores["conversation_store"], "t1", since)
    down = [r for r in rated if r.feedback.rating == "down"]
    assert len(rated) == 2 and len(down) == 1
    assert down[0].question == "What does the 200-hour course cost?"
    assert down[0].reply == "Our 200-hour course costs USD 1,450."


def test_the_review_finds_replies_after_old_messages_are_summarised(client):
    """Messages give way to the summary; questions keep the reply id."""
    client, stores = client
    reply_id = chat(client, "Is there an exam at the end?")["reply_id"]
    rate(client, reply_id, "down", "wrong")
    convo = stores["conversation_store"].get("t1", "c1")
    convo.messages.clear()  # as if summarised

    since = datetime.now(timezone.utc) - timedelta(days=1)
    (r,) = collect(stores["feedback_store"], stores["conversation_store"], "t1", since)
    assert r.question == "Is there an exam at the end?"
