"""Tests for the lead digest: one email once a visitor with contact details
goes quiet, even if they never asked to be contacted.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.orchestrator import after_reply, generate_reply, send_due_digests
from app.config import get_settings
from app.conversations.store import InMemoryConversationStore
from app.leads.pending import ItemKind, PendingItem
from app.leads.store import InMemoryLeadStore
from app.tenants.models import Tenant
from app.tenants.store import InMemoryTenantStore


@pytest.fixture(autouse=True)
def resend_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", SecretStr("re_test"))


def make_tenant():
    return Tenant(tenant_id="t1", site_key="pk_t1", display_name="School",
                  team_email="team@example.com")


def tenants():
    store = InMemoryTenantStore()
    store.save(make_tenant())
    return store


def reply(text="Sure."):
    message = MagicMock()
    message.content = text
    message.tool_calls = None
    return message


def chat(messages, store, lead_store):
    """Turns as the API runs them, with the model and email patched."""
    tenant = make_tenant()
    for message in messages:
        text, _, stored = generate_reply(tenant, "c1", message, store, lead_store=lead_store)
        after_reply(tenant, "c1", stored, text, store, lead_store, get_settings())


def later(minutes):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


@pytest.fixture
def patched():
    with patch("app.agent.orchestrator.complete_with_tools", return_value=reply()), \
         patch("app.agent.orchestrator.complete", return_value='{"summary": "S."}'), \
         patch("app.agent.orchestrator.send_lead_email", return_value=True) as send:
        yield send


def test_questions_with_an_email_are_flagged_and_sent_once_quiet(patched):
    """The visitor gives an email but never asks to be contacted."""
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "sam@example.com", "How many halls are there?"], store, lead_store)

    assert store.get("t1", "c1").digest_due
    patched.assert_not_called()  # no contact request, so nothing yet

    # Still active: the sweep waits.
    assert send_due_digests(tenants(), store, lead_store, now=later(5))["waiting"] == 1
    patched.assert_not_called()

    # Quiet for 15 minutes: one email with both questions.
    counts = send_due_digests(tenants(), store, lead_store, now=later(16))
    assert counts["sent"] == 1
    _, _, _, questions = patched.call_args[0]
    assert [q.text for q in questions] == ["Is there an exam?", "How many halls are there?"]

    conversation = store.get("t1", "c1")
    assert conversation.questions_emailed == 2
    assert not conversation.digest_due

    # The next sweep sends nothing again.
    assert send_due_digests(tenants(), store, lead_store, now=later(30))["due"] == 0
    assert patched.call_count == 1


def test_no_email_address_means_no_digest(patched):
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "How many halls?"], store, lead_store)

    assert not store.get("t1", "c1").digest_due
    assert send_due_digests(tenants(), store, lead_store, now=later(60))["due"] == 0
    patched.assert_not_called()


def test_new_questions_after_the_digest_flag_again(patched):
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "sam@example.com"], store, lead_store)
    send_due_digests(tenants(), store, lead_store, now=later(16))

    chat(["Do you have Wi-Fi?"], store, lead_store)
    assert store.get("t1", "c1").digest_due
    send_due_digests(tenants(), store, lead_store, now=later(32))

    second = patched.call_args_list[1][0][3]
    assert [q.text for q in second] == ["Do you have Wi-Fi?"]


def test_a_waiting_contact_request_goes_with_the_digest(patched):
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "sam@example.com"], store, lead_store)
    lead_store.add_item("t1", "c1", PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="call me"))

    # The in-memory store hands out the live lead, so look at send time.
    kinds_sent = []
    patched.side_effect = lambda lead, *rest: (
        kinds_sent.extend(i.kind for i in lead.unsent_items) or True
    )
    send_due_digests(tenants(), store, lead_store, now=later(16))

    assert ItemKind.CONTACT_REQUEST in kinds_sent
    assert all(item.is_sent for item in lead_store.get("t1", "c1").items)


@pytest.mark.parametrize("retry, flag_after", [(False, False), (True, True)])
def test_failed_send_follows_the_retry_setting(patched, monkeypatch, retry, flag_after):
    monkeypatch.setattr(get_settings(), "retry_failed_lead_emails", retry)
    patched.return_value = False
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "sam@example.com"], store, lead_store)

    assert send_due_digests(tenants(), store, lead_store, now=later(16))["failed"] == 1
    conversation = store.get("t1", "c1")
    assert conversation.digest_due is flag_after
    assert conversation.questions_emailed == 0  # never marked on failure


def test_at_most_a_batch_per_sweep(patched, monkeypatch):
    monkeypatch.setattr(get_settings(), "lead_digest_batch", 2)
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    tenant = make_tenant()
    for i in range(5):
        cid = f"c{i}"
        store.append_turn("t1", cid, "is there an exam?", "No idea.", is_question=True)
        store.set_digest_due("t1", cid, True)
        lead_store.upsert_fields("t1", cid, {"email": f"v{i}@example.com"})

    assert send_due_digests(tenants(), store, lead_store, now=later(16))["sent"] == 2
    assert send_due_digests(tenants(), store, lead_store, now=later(21))["sent"] == 2
    assert send_due_digests(tenants(), store, lead_store, now=later(26))["sent"] == 1
    assert patched.call_count == 5
    del tenant


# --- the endpoint ------------------------------------------------------------


def make_app():
    from app.main import create_app
    from app.documents.store import InMemoryChunkStore
    from app.facts.store import InMemoryFactStore
    from app.programmes.store import InMemoryProgrammeStore
    from app.usage.store import InMemoryUsageStore
    from app.feedback.store import InMemoryFeedbackStore

    return create_app(
        tenant_store=tenants(),
        conversation_store=InMemoryConversationStore(),
        fact_store=InMemoryFactStore(),
        vector_store=MagicMock(),
        chunk_store=InMemoryChunkStore(),
        lead_store=InMemoryLeadStore(),
        programme_store=InMemoryProgrammeStore(),
        usage_store=InMemoryUsageStore(),
        feedback_store=InMemoryFeedbackStore(),
    )


INVOKER = "lead-digest@project.iam.gserviceaccount.com"


def post(headers=None):
    with TestClient(make_app()) as client:
        return client.post("/internal/lead-digest", headers=headers or {})


def test_endpoint_is_off_until_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "lead_digest_invoker", "")
    assert post({"Authorization": "Bearer x"}).status_code == 404


def test_endpoint_needs_a_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "lead_digest_invoker", INVOKER)
    assert post().status_code == 401


def test_endpoint_refuses_an_invalid_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "lead_digest_invoker", INVOKER)
    with patch("app.api.internal._verify_identity_token", side_effect=ValueError("bad")):
        assert post({"Authorization": "Bearer forged"}).status_code == 401


def test_endpoint_refuses_another_account(monkeypatch):
    monkeypatch.setattr(get_settings(), "lead_digest_invoker", INVOKER)
    claims = {"email": "someone@else.iam.gserviceaccount.com", "email_verified": True}
    with patch("app.api.internal._verify_identity_token", return_value=claims):
        assert post({"Authorization": "Bearer t"}).status_code == 403


def test_endpoint_runs_the_sweep_for_the_scheduler(monkeypatch):
    monkeypatch.setattr(get_settings(), "lead_digest_invoker", INVOKER)
    claims = {"email": INVOKER, "email_verified": True}
    with patch("app.api.internal._verify_identity_token", return_value=claims) as verify:
        response = post({"Authorization": "Bearer good"})
    assert response.status_code == 200
    assert response.json()["sent"] == 0
    # The same call runs the facts sync; this tenant has no sheet.
    assert response.json()["facts"] == {"synced": 0, "held": 0, "failed": 0}
    assert verify.call_args[0] == ("good", "sitechat-lead-digest")


# --- the daily cap (backlog 82) -------------------------------------------------


class Tomorrow(datetime):
    """datetime, a day ahead: the cap's day has turned."""

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) + timedelta(days=1)


def contact_request(store, lead_store, cid):
    """A visitor with an email asks the team to get in touch."""
    store.append_turn("t1", cid, "Is there an exam?", "I don't know.", is_question=True)
    lead_store.upsert_fields("t1", cid, {"email": f"{cid}@example.com"})
    lead_store.add_item("t1", cid, PendingItem(kind=ItemKind.CONTACT_REQUEST, detail="call me"))


def test_cap_holds_emails_over_the_daily_limit_and_sends_them_next_day(patched, monkeypatch):
    from app.agent.orchestrator import notify_team_if_due

    monkeypatch.setattr(get_settings(), "lead_emails_per_day", 2)
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    for cid in ("c1", "c2", "c3"):
        contact_request(store, lead_store, cid)

    with patch("app.agent.orchestrator.complete", return_value='{"summary": "S."}') as model:
        for cid in ("c1", "c2", "c3"):
            notify_team_if_due(make_tenant(), cid, store, lead_store)
        assert patched.call_count == 2
        assert model.call_count == 2  # no summary is written for a held email

    # Held, not lost: still waiting, and flagged for the digest.
    held = lead_store.get("t1", "c3")
    assert held.unsent_items and store.get("t1", "c3").digest_due

    # Later the same day the sweep keeps holding it.
    assert send_due_digests(tenants(), store, lead_store, now=later(16))["held"] == 1
    assert patched.call_count == 2

    # Once the day turns, it goes, contact request and all.
    with patch("app.agent.orchestrator.datetime", Tomorrow):
        counts = send_due_digests(tenants(), store, lead_store, now=later(16))
    assert counts["sent"] == 1
    assert patched.call_args[0][0].conversation_id == "c3"
    assert not lead_store.get("t1", "c3").unsent_items
    assert not store.get("t1", "c3").digest_due


def test_failed_sends_do_not_count_towards_the_cap(patched, monkeypatch):
    from app.agent.orchestrator import notify_team_if_due

    monkeypatch.setattr(get_settings(), "lead_emails_per_day", 1)
    patched.return_value = False
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    contact_request(store, lead_store, "c1")
    notify_team_if_due(make_tenant(), "c1", store, lead_store)

    today = datetime.now(timezone.utc).date().isoformat()
    assert lead_store.emails_sent_on("t1", today) == 0


def test_no_cap_when_set_to_zero(patched, monkeypatch):
    from app.agent.orchestrator import notify_team_if_due

    monkeypatch.setattr(get_settings(), "lead_emails_per_day", 0)
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    for i in range(5):
        contact_request(store, lead_store, f"c{i}")
        notify_team_if_due(make_tenant(), f"c{i}", store, lead_store)
    assert patched.call_count == 5


# --- overlapping sweeps (backlog 92) -----------------------------------------


def test_a_sweep_started_during_another_sends_nothing_twice(patched):
    """A manual run while the scheduled one is mid-email: the second sweep
    finds the conversation claimed and skips it."""
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "sam@example.com"], store, lead_store)

    overlapping = {}

    def send_while_another_sweep_runs(*args):
        if not overlapping:
            overlapping["counts"] = send_due_digests(tenants(), store, lead_store, now=later(16))
        return True

    patched.side_effect = send_while_another_sweep_runs
    first = send_due_digests(tenants(), store, lead_store, now=later(16))

    assert first["sent"] == 1
    assert overlapping["counts"]["busy"] == 1 and overlapping["counts"]["sent"] == 0
    assert patched.call_count == 1
    convo = store.get("t1", "c1")
    assert not convo.digest_due and convo.digest_claimed_at is None


def test_a_claim_left_by_a_dead_sweep_expires(patched):
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "sam@example.com"], store, lead_store)
    lease = timedelta(minutes=get_settings().lead_digest_claim_minutes)
    # A sweep claimed it at later(16) and died without sending.
    assert store.claim_for_digest("t1", "c1", later(16), lease)

    assert send_due_digests(tenants(), store, lead_store, now=later(20))["busy"] == 1
    patched.assert_not_called()
    assert send_due_digests(tenants(), store, lead_store, now=later(27))["sent"] == 1


def test_a_sweep_that_raises_releases_its_claim(patched):
    store, lead_store = InMemoryConversationStore(), InMemoryLeadStore()
    chat(["Is there an exam?", "sam@example.com"], store, lead_store)
    patched.side_effect = RuntimeError("resend down hard")

    with pytest.raises(RuntimeError):
        send_due_digests(tenants(), store, lead_store, now=later(16))
    convo = store.get("t1", "c1")
    assert convo.digest_due and convo.digest_claimed_at is None  # next sweep retries
