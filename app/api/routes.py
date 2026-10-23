"""Public API routes.

HTTP concerns only: tenant resolution, rate limits, mapping model failures
to a 503 and scheduling background work. What a reply is made of lives in
app/agent/orchestrator.py.
"""

import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    status,
)

from app.agent.llm import LLMError
from app.agent.orchestrator import (
    after_reply,
    generate_reply,
    request_confirmation,
    request_is_waiting,
)
from app.api.dependencies import cached_tenant, get_tenant
from app.api.schemas import ChatRequest, ChatResponse, FeedbackRequest, WidgetConfig
from app import request_context
from app.config import get_settings
from app.feedback.models import Feedback
from app.rate_limit import limiter, site_key
from app.tenants.models import Tenant

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/config", response_model=WidgetConfig)
@limiter.limit(lambda: get_settings().rate_limit_config_per_visitor)
def get_config(request: Request, tenant: Tenant = Depends(get_tenant)) -> WidgetConfig:
    """Public settings the widget needs to render itself."""
    return WidgetConfig(
        display_name=tenant.display_name,
        greeting=tenant.greeting,
        suggested_questions=tenant.suggested_questions,
        accent_colour=tenant.accent_colour,
        privacy_url=str(tenant.privacy_url) if tenant.privacy_url else None,
    )


def _tenant_limit(key: str) -> str:
    """This tenant's own limit if set, else the default from config.py.

    slowapi passes the rate-limit key ("site:<site key>"). get_tenant has
    already run for this request, so the tenant is in its cache.
    """
    tenant = cached_tenant(key.removeprefix("site:"))
    if tenant is not None and tenant.rate_limit:
        return tenant.rate_limit
    return get_settings().rate_limit_per_tenant


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(lambda: get_settings().rate_limit_per_visitor)
@limiter.limit(_tenant_limit, key_func=site_key)
def chat(
    request: Request,
    body: ChatRequest,
    background: BackgroundTasks,
    tenant: Tenant = Depends(get_tenant),
) -> ChatResponse:
    conversation_id = body.conversation_id or str(uuid.uuid4())
    reply_id = uuid.uuid4().hex[:16]
    store = request.app.state.conversation_store
    fact_store = request.app.state.fact_store

    # Lasts for this worker thread, which runs the whole reply.
    request_context.tenant_id.set(tenant.tenant_id)
    request_context.conversation_id.set(conversation_id)
    logger.info("Chat request")

    lead_store = request.app.state.lead_store
    # Before the turn, so the card shows only on the turn that placed it.
    was_waiting = request_is_waiting(lead_store, tenant.tenant_id, conversation_id)

    try:
        reply, _, stored_message = generate_reply(
            tenant,
            conversation_id,
            body.message,
            store,
            fact_store,
            request.app.state.vector_store,
            request.app.state.chunk_store,
            request.app.state.lead_store,
            programme_store=getattr(request.app.state, "programme_store", None),
            usage_store=getattr(request.app.state, "usage_store", None),
        )
    except LLMError as exc:
        # The model failed. Return the visitor-safe message with a 503 so
        # monitoring can tell this apart from a normal reply.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.user_message,
        ) from exc

    # Storing the turn, summarizing and emailing the team do not affect
    # this reply, so they run after it has been sent.
    background.add_task(
        after_reply,
        tenant,
        conversation_id,
        stored_message,
        reply,
        store,
        request.app.state.lead_store,
        get_settings(),
        reply_id,
    )

    return ChatResponse(
        reply=reply,
        conversation_id=conversation_id,
        confirmation=request_confirmation(
            tenant, lead_store, conversation_id, was_waiting
        ),
        reply_id=reply_id,
    )


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(lambda: get_settings().rate_limit_feedback_per_visitor)
def feedback(
    request: Request,
    body: FeedbackRequest,
    tenant: Tenant = Depends(get_tenant),
) -> None:
    """A visitor's thumbs up or down on a reply. It changes nothing the bot
    does; it is for reviewing replies (scripts/feedback.py). The reason is
    kept only with a thumbs down."""
    store = getattr(request.app.state, "feedback_store", None)
    if store is None:
        return
    store.save(Feedback(
        tenant_id=tenant.tenant_id,
        conversation_id=body.conversation_id,
        reply_id=body.reply_id,
        rating=body.rating,
        reason=body.reason if body.rating == "down" else None,
    ))