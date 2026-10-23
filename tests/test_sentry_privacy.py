"""Sentry receives no visitor data (backlog 104): no request bodies, no
local variables, with errors still reported.

Runs the real SDK with a transport that keeps events in memory, so nothing
is sent anywhere.
"""

import sentry_sdk
from pydantic import SecretStr

from app.config import get_settings
from app.main import sentry_options

VISITOR_MESSAGE = "my email is sam@example.com, what does the course cost?"


def test_an_error_report_carries_no_visitor_data(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "sentry_dsn", SecretStr("https://key@o0.ingest.sentry.io/0"))
    events = []

    options = sentry_options(settings)
    sentry_sdk.init(**options, transport=lambda event: events.append(event))
    try:
        def handle(message):
            raise RuntimeError("store unavailable")
        try:
            handle(VISITOR_MESSAGE)
        except RuntimeError:
            sentry_sdk.capture_exception()
        sentry_sdk.flush()
    finally:
        sentry_sdk.init()  # no DSN: Sentry off again for the other tests

    assert events, "the error should still be reported"
    assert "sam@example.com" not in str(events)
    assert "what does the course cost" not in str(events)
    assert options["max_request_body_size"] == "never"
