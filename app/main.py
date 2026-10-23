"""The FastAPI application.

Startup wiring lives here: settings, logging, the tenant store, rate
limiting and CORS. Routes live in app/api/routes.py.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.logging_setup import setup_logging
from app.request_context import RequestContextMiddleware
from app.usage.store import FirestoreUsageStore
from app.feedback.store import FirestoreFeedbackStore
from app.tenants.store import FirestoreTenantStore
from app.conversations.store import FirestoreConversationStore
from app.facts.store import FirestoreFactStore
from app.retrieval.vector_store import PineconeVectorStore
from app.documents.store import FirestoreChunkStore
from app.leads.store import FirestoreLeadStore
from app.programmes.store import FirestoreProgrammeStore
from fastapi.staticfiles import StaticFiles
from app.api.internal import router as internal_router
from app.api.routes import router
from app.rate_limit import limiter

logger = logging.getLogger(__name__)




def sentry_options(settings) -> dict:
    """Conversations contain personal data, and Sentry must not receive it.

    send_default_pii=False alone is not enough: by default Sentry still
    attaches each stack frame's local variables (a chat error's locals hold
    the visitor's message, and sometimes their email) and the request body
    (the message itself). Both are turned off.
    """
    return dict(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment,
        # Sample a tenth of requests for performance traces. Full tracing
        # on every request is expensive and rarely tells you more.
        traces_sample_rate=0.1,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = get_settings()
    setup_logging(settings.log_level, as_json=settings.is_production)

    if settings.sentry_dsn.get_secret_value():
        import sentry_sdk

        sentry_sdk.init(**sentry_options(settings))
        logger.info("Sentry enabled")

    logger.info("Starting up in %s mode", settings.environment)

    # Only build real cloud clients if stores were not supplied.
    # Tests pass their own, so nothing touches Google credentials.
    if not hasattr(app.state, "tenant_store"):
        app.state.tenant_store = FirestoreTenantStore(settings.gcp_project_id)
    if not hasattr(app.state, "conversation_store"):
        app.state.conversation_store = FirestoreConversationStore(
            settings.gcp_project_id
        )
    if not hasattr(app.state, "fact_store"):
        app.state.fact_store = FirestoreFactStore(settings.gcp_project_id)

    if not hasattr(app.state, "vector_store"):
        app.state.vector_store = PineconeVectorStore()
    if not hasattr(app.state, "chunk_store"):
        app.state.chunk_store = FirestoreChunkStore(settings.gcp_project_id)
    if not hasattr(app.state, "lead_store"):
        app.state.lead_store = FirestoreLeadStore(settings.gcp_project_id)
    if not hasattr(app.state, "feedback_store"):
        app.state.feedback_store = FirestoreFeedbackStore(settings.gcp_project_id)
    if not hasattr(app.state, "usage_store"):
        app.state.usage_store = FirestoreUsageStore(settings.gcp_project_id)
    if not hasattr(app.state, "programme_store"):
        app.state.programme_store = FirestoreProgrammeStore(
            settings.gcp_project_id
        )

    yield

    logger.info("Shutting down")


def create_app(
    tenant_store=None,
    conversation_store=None,
    fact_store=None,
    vector_store=None,
    chunk_store=None,
    lead_store=None,
    programme_store=None,
    usage_store=None,
    feedback_store=None,
) -> FastAPI:
    # No /docs, /redoc or /openapi.json in production: they would publish
    # every route, /internal ones included, to anyone who asks.
    docs: dict[str, Any] = {} if not get_settings().is_production else dict(
        docs_url=None, redoc_url=None, openapi_url=None
    )
    app = FastAPI(title="Sitechat API", lifespan=lifespan, **docs)

    if tenant_store is not None:
        app.state.tenant_store = tenant_store
    if conversation_store is not None:
        app.state.conversation_store = conversation_store
    if fact_store is not None:
        app.state.fact_store = fact_store
    if vector_store is not None:
        app.state.vector_store = vector_store
    if chunk_store is not None:
        app.state.chunk_store = chunk_store
    if lead_store is not None:
        app.state.lead_store = lead_store
    if programme_store is not None:
        app.state.programme_store = programme_store
    if usage_store is not None:
        app.state.usage_store = usage_store
    if feedback_store is not None:
        app.state.feedback_store = feedback_store

    app.state.limiter = limiter
    # slowapi's handler takes its own exception type, narrower than
    # Starlette's annotation; it is only ever called with that type.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # The widget runs on customer websites, so the browser needs permission
    # to call us. Which domains are actually allowed is enforced per tenant
    # in get_tenant(); this layer only keeps the browser from blocking us.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Site-Key"],
    )
    # Added last, so it is outermost: every log line of a request, its
    # background tasks included, carries the request id.
    app.add_middleware(RequestContextMiddleware)

    @app.get("/health")
    def health_check():
        """Liveness: is the process running.

        Named /health rather than /healthz: the latter is intercepted by
        Cloud Run's frontend and never reaches the container.
        """
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(internal_router)
    app.mount("/widget", StaticFiles(directory="static"), name="widget")

    return app


app = create_app()