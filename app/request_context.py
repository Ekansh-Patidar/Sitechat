"""Carries per-request identifiers into every log line.

Without this, logs are a flat stream: you can see that retrieval was slow,
but not which request it belonged to, or which tenant. With it, "show me
everything that happened during this conversation" becomes one query.

contextvars rather than a global: each request gets its own value. The
request id is set by RequestContextMiddleware; the tenant and conversation
by bound(), where the work runs (see its docstring for why).
"""

import contextlib
import contextvars
import uuid

request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tenant_id", default=""
)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]

conversation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "conversation_id", default=""
)


@contextlib.contextmanager
def bound(tenant: str, conversation: str = ""):
    """Tag every log line inside the block with this tenant and conversation.

    Set where the code runs, not in a dependency: FastAPI runs sync
    dependencies and background tasks in worker threads, each with its own
    copy of the context, so a value set in one is gone in the next.
    """
    tokens = (tenant_id.set(tenant), conversation_id.set(conversation))
    try:
        yield
    finally:
        tenant_id.reset(tokens[0])
        conversation_id.reset(tokens[1])


class RequestContextMiddleware:
    """Give each HTTP request a fresh request id, visible to everything it
    runs: the route, its worker thread, and the background tasks after it
    (all run inside this call, so they inherit the context set here)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = request_id.set(new_request_id())
        try:
            await self.app(scope, receive, send)
        finally:
            request_id.reset(token)
