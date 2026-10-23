"""Security tests for the public API.

Each test checks one protection. These are the tests that matter most in
this project: a failure here means one customer's key works on another
site, or an unknown caller gets through.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tenants.models import Tenant
from app.tenants.store import InMemoryTenantStore
import app.api.dependencies as deps
from unittest.mock import patch
from app.conversations.store import InMemoryConversationStore
from app.documents.store import InMemoryChunkStore
from app.facts.store import InMemoryFactStore
from app.leads.store import InMemoryLeadStore
from app.programmes.store import InMemoryProgrammeStore
from app.usage.store import InMemoryUsageStore
from app.feedback.store import InMemoryFeedbackStore
from unittest.mock import MagicMock
from starlette.requests import Request

from app.config import get_settings
from app.rate_limit import client_ip, limiter


def make_message(content: str = "ok"):
    """A model reply with no tool calls."""
    message = MagicMock()
    message.content = content
    message.tool_calls = None
    return message

class NoOpVectorStore:
    """A vector store that never embeds or returns anything.

    These tests cover the security layer, not retrieval, so a store that
    makes no API calls keeps them fast, free and runnable without keys.
    """

    def upsert_chunks(self, tenant_id, chunks):
        pass

    def search(self, tenant_id, query_vector, top_k):
        return []

    def delete_document(self, tenant_id, document_id):
        pass

@pytest.fixture
def client():
    store = InMemoryTenantStore()
    store.save(
        Tenant(
            tenant_id="t1",
            site_key="pk_valid",
            display_name="Test School",
            allowed_domains=["example.com"],
            greeting="Hello there",
            suggested_questions=["What are the dates?"],
        )
    )
    store.save(
        Tenant(
            tenant_id="t2",
            site_key="pk_inactive",
            display_name="Closed School",
            allowed_domains=["closed.com"],
            is_active=False,
        )
    )

    app = create_app(
        tenant_store=store,
        conversation_store=InMemoryConversationStore(),
        fact_store=InMemoryFactStore(),
        vector_store=NoOpVectorStore(),
        chunk_store=InMemoryChunkStore(),
        lead_store=InMemoryLeadStore(),
        programme_store=InMemoryProgrammeStore(),
        usage_store=InMemoryUsageStore(),
        feedback_store=InMemoryFeedbackStore(),
    )

    # Each test starts with an empty tenant cache and fresh rate counters.
    deps._cache.clear()
    limiter.reset()

    with patch(
        "app.agent.orchestrator.complete_with_tools",
        return_value=make_message("stub reply"),
    ), patch("app.retrieval.pipeline.embed_query", return_value=[0.0] * 1536):
        with TestClient(app) as c:
            yield c


def post_chat(client, site_key=None, origin=None, message="hi"):
    headers = {}
    if site_key:
        headers["X-Site-Key"] = site_key
    if origin:
        headers["Origin"] = origin
    return client.post("/chat", json={"message": message}, headers=headers)


def test_valid_request_succeeds(client):
    r = post_chat(client, site_key="pk_valid", origin="https://example.com")
    assert r.status_code == 200
    assert "conversation_id" in r.json()
    # Sources are logged, not sent to the visitor.
    assert "citations" not in r.json()


def test_missing_site_key_is_rejected(client):
    r = post_chat(client)
    assert r.status_code == 422


def test_unknown_site_key_is_rejected(client):
    r = post_chat(client, site_key="pk_nope", origin="https://example.com")
    assert r.status_code == 401


def test_wrong_origin_is_rejected(client):
    """The core protection: a stolen key is useless on another site."""
    r = post_chat(client, site_key="pk_valid", origin="https://attacker.com")
    assert r.status_code == 403


def test_subdomain_of_allowed_domain_is_accepted(client):
    r = post_chat(client, site_key="pk_valid", origin="https://www.example.com")
    assert r.status_code == 200


def test_lookalike_domain_is_rejected(client):
    """notexample.com must not pass a check for example.com."""
    r = post_chat(client, site_key="pk_valid", origin="https://notexample.com")
    assert r.status_code == 403


def test_inactive_tenant_is_rejected(client):
    r = post_chat(client, site_key="pk_inactive", origin="https://closed.com")
    assert r.status_code == 403


def test_empty_message_is_rejected(client):
    r = post_chat(client, site_key="pk_valid", origin="https://example.com", message="")
    assert r.status_code == 422


def test_oversized_message_is_rejected(client):
    r = post_chat(
        client,
        site_key="pk_valid",
        origin="https://example.com",
        message="x" * 3000,
    )
    assert r.status_code == 422


def test_config_returns_only_public_fields(client):
    r = client.get("/config", headers={"X-Site-Key": "pk_valid", "Origin": "https://example.com"})
    assert r.status_code == 200
    data = r.json()
    assert data["greeting"] == "Hello there"
    # Internal fields must never be exposed.
    assert "allowed_domains" not in data
    assert "tenant_id" not in data
    assert data["privacy_url"] is None


def test_config_includes_the_privacy_link_when_set():
    from app.api.routes import get_config

    tenant = Tenant(
        tenant_id="t9",
        site_key="pk_9",
        display_name="School",
        privacy_url="https://school.example/privacy",
    )
    # The handler itself, without the rate-limit wrapper (which needs a request).
    assert get_config.__wrapped__(None, tenant).privacy_url == "https://school.example/privacy"

# --- Rate limiting ---------------------------------------------------------


def request_with(xff=None, host="169.254.169.126"):
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "headers": headers, "client": (host, 0)})


@pytest.mark.parametrize(
    "hops, xff, expected",
    [
        # Direct *.run.app traffic: Google appends the visitor's address.
        (1, "203.0.113.7", "203.0.113.7"),
        # A visitor-supplied entry comes first and must be ignored.
        (1, "1.2.3.4,203.0.113.7", "203.0.113.7"),
        (1, "1.2.3.4, 203.0.113.7", "203.0.113.7"),
        # Behind a load balancer, the visitor is second from the end.
        (2, "1.2.3.4,203.0.113.7,198.51.100.1", "203.0.113.7"),
        # IPv6 visitors are grouped by /64.
        (1, "2406:7400:12b:ca2b:a94e:e487:5595:c9f3", "2406:7400:12b:ca2b::/64"),
        # No header (local development) falls back to the connection.
        (1, None, "169.254.169.126"),
        # Header shorter than the hop count is not trusted.
        (2, "203.0.113.7", "169.254.169.126"),
        # hops=0 ignores the header entirely.
        (0, "203.0.113.7", "169.254.169.126"),
    ],
)
def test_client_ip(monkeypatch, hops, xff, expected):
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", hops)
    assert client_ip(request_with(xff)) == expected


def test_ipv6_addresses_in_one_slash_64_share_a_key():
    a = client_ip(request_with("2406:7400:12b:ca2b:a94e:e487:5595:c9f3"))
    b = client_ip(request_with("2406:7400:12b:ca2b:10b6:cd8b:cd95:d8db"))
    assert a == b


def post_chat_from(client, ip, site_key="pk_valid"):
    return client.post(
        "/chat",
        json={"message": "hi"},
        headers={
            "X-Site-Key": site_key,
            "Origin": "https://example.com",
            "X-Forwarded-For": ip,
        },
    )


def test_visitor_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_visitor", "2/minute")
    assert post_chat_from(client, "203.0.113.7").status_code == 200
    assert post_chat_from(client, "203.0.113.7").status_code == 200
    assert post_chat_from(client, "203.0.113.7").status_code == 429
    # A different visitor has their own count.
    assert post_chat_from(client, "203.0.113.8").status_code == 200


def test_spoofed_forwarded_for_does_not_reset_the_visitor_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_visitor", "1/minute")
    assert post_chat_from(client, "1.1.1.1,203.0.113.7").status_code == 200
    assert post_chat_from(client, "2.2.2.2,203.0.113.7").status_code == 429


def test_tenant_limit_returns_429_across_visitors(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_tenant", "3/hour")
    for i in range(3):
        assert post_chat_from(client, f"203.0.113.{i}").status_code == 200
    assert post_chat_from(client, "203.0.113.99").status_code == 429


# --- bad keys cannot run up Firestore reads (backlog 88) ---------------------


def count_lookups(client, monkeypatch):
    store = client.app.state.tenant_store
    real, calls = store.get_by_site_key, []
    def counted(key):
        calls.append(key)
        return real(key)
    monkeypatch.setattr(store, "get_by_site_key", counted)
    return calls


def test_an_unknown_key_is_looked_up_once_per_cache_period(client, monkeypatch):
    lookups = count_lookups(client, monkeypatch)
    for _ in range(3):
        assert post_chat(client, site_key="pk_made_up").status_code == 401
    assert lookups == ["pk_made_up"]


def test_an_oversized_key_is_refused_without_a_lookup(client, monkeypatch):
    lookups = count_lookups(client, monkeypatch)
    assert post_chat(client, site_key="pk_" + "x" * 200).status_code == 401
    assert lookups == [] and not deps._cache


def test_made_up_keys_cannot_fill_the_cache(client, monkeypatch):
    monkeypatch.setattr(deps, "MAX_CACHED_KEYS", 3)
    monkeypatch.setattr(get_settings(), "rate_limit_rejected_per_visitor", "100/minute")
    for i in range(10):
        post_chat(client, site_key=f"pk_random_{i}")
    assert len(deps._cache) <= 3


def test_too_many_refusals_are_refused_before_the_lookup(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_rejected_per_visitor", "3/minute")
    lookups = count_lookups(client, monkeypatch)
    for i in range(3):
        assert post_chat_from(client, "203.0.113.7", site_key=f"pk_bad_{i}").status_code == 401
    assert post_chat_from(client, "203.0.113.7", site_key="pk_bad_x").status_code == 429
    # Even with a good key, until the minute is up; no lookup made.
    assert post_chat_from(client, "203.0.113.7").status_code == 429
    assert len(lookups) == 3
    # Someone else is unaffected.
    assert post_chat_from(client, "203.0.113.8").status_code == 200


def test_a_blocked_domain_counts_as_a_refusal(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_rejected_per_visitor", "2/minute")
    headers = {"X-Site-Key": "pk_valid", "Origin": "https://evil.com", "X-Forwarded-For": "203.0.113.9"}
    codes = [client.get("/config", headers=headers).status_code for _ in range(3)]
    assert codes == [403, 403, 429]


def test_config_is_rate_limited_per_visitor(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_config_per_visitor", "2/minute")
    headers = {"X-Site-Key": "pk_valid", "Origin": "https://example.com", "X-Forwarded-For": "203.0.113.5"}
    codes = [client.get("/config", headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# --- conversation_id validation --------------------------------------------


def post_chat_with_id(client, conversation_id):
    return client.post(
        "/chat",
        json={"message": "hi", "conversation_id": conversation_id},
        headers={"X-Site-Key": "pk_valid", "Origin": "https://example.com"},
    )


def test_server_issued_conversation_id_is_accepted(client):
    first = post_chat(client, site_key="pk_valid", origin="https://example.com")
    issued = first.json()["conversation_id"]
    r = post_chat_with_id(client, issued)
    assert r.status_code == 200
    assert r.json()["conversation_id"] == issued


@pytest.mark.parametrize(
    "conversation_id",
    [
        "a/b",          # would split the Firestore path
        "a__b",         # the tenant/conversation separator
        "../etc",
        "",
        "x" * 65,
        "has space",
    ],
)
def test_malformed_conversation_id_is_rejected(client, conversation_id):
    assert post_chat_with_id(client, conversation_id).status_code == 422


def test_tenant_rate_limit_overrides_the_default():
    store = InMemoryTenantStore()
    store.save(Tenant(
        tenant_id="small", site_key="pk_small", display_name="Small",
        allowed_domains=["example.com"], rate_limit="2/hour",
    ))
    store.save(Tenant(
        tenant_id="default", site_key="pk_default", display_name="Default",
        allowed_domains=["example.com"],
    ))
    app = create_app(
        tenant_store=store,
        conversation_store=InMemoryConversationStore(),
        fact_store=InMemoryFactStore(),
        vector_store=NoOpVectorStore(),
        chunk_store=InMemoryChunkStore(),
        lead_store=InMemoryLeadStore(),
        programme_store=InMemoryProgrammeStore(),
        usage_store=InMemoryUsageStore(),
        feedback_store=InMemoryFeedbackStore(),
    )
    deps._cache.clear()
    limiter.reset()

    with patch(
        "app.agent.orchestrator.complete_with_tools",
        return_value=make_message("ok"),
    ), TestClient(app) as c:
        def chat(key, ip):
            return post_chat_from(c, ip, site_key=key).status_code

        # Different visitors, so only the tenant limit applies.
        assert [chat("pk_small", f"203.0.113.{i}") for i in range(3)] == [200, 200, 429]
        assert [chat("pk_default", f"198.51.100.{i}") for i in range(3)] == [200, 200, 200]


def test_invalid_tenant_rate_limit_is_rejected():
    with pytest.raises(ValueError):
        Tenant(tenant_id="x", site_key="pk_x", display_name="X", rate_limit="lots")


# --- API docs (backlog 89) ----------------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_off_in_production(monkeypatch, path):
    monkeypatch.setattr(get_settings(), "environment", "production")
    # Not entered, so startup (which would build real cloud clients) never runs.
    assert TestClient(create_app()).get(path).status_code == 404


def test_api_docs_are_on_in_development(client):
    assert client.get("/openapi.json").status_code == 200
