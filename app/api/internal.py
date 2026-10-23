"""Endpoints called by Google Cloud, not by visitors.

POST /internal/lead-digest is called every 5 minutes by Cloud Scheduler.
It sends due lead digests, then syncs any tenant's facts that are a day
old from their Google Sheet (one free scheduler job for both).
The service is public (the widget needs it), so this endpoint checks that
the request carries a Google-signed identity token for the one service
account allowed to call it. Without settings.lead_digest_invoker it is off.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.agent.orchestrator import send_due_digests
from app.config import get_settings
from app.facts.sync import sync_stale_facts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal")


def _verify_identity_token(token: str, audience: str) -> dict:
    """Check a Google-signed OIDC token and return its claims."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(
        token, google_requests.Request(), audience=audience
    )


def require_scheduler(authorization: str | None) -> None:
    settings = get_settings()
    if not settings.lead_digest_invoker:
        # Off until configured: indistinguishable from no endpoint at all.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        claims = _verify_identity_token(
            authorization.removeprefix("Bearer "), settings.lead_digest_audience
        )
    except Exception:
        logger.warning("Lead digest: invalid identity token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from None

    if claims.get("email") != settings.lead_digest_invoker or not claims.get(
        "email_verified"
    ):
        logger.warning("Lead digest: token from %s refused", claims.get("email"))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/lead-digest")
def lead_digest(request: Request, authorization: str | None = Header(default=None)):
    require_scheduler(authorization)
    state = request.app.state
    counts = send_due_digests(
        state.tenant_store, state.conversation_store, state.lead_store
    )
    counts["facts"] = sync_stale_facts(state.tenant_store, state.fact_store)
    return counts
