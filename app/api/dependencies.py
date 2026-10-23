"""Shared request logic.

FastAPI calls these before the route handler. They resolve the tenant and
reject anything that fails a check, so route handlers can assume the
request is already valid.
"""

import logging
import time
from typing import NoReturn

from fastapi import Header, HTTPException, Request, status
from limits import parse

from app.config import get_settings
from app.rate_limit import client_ip, limiter
from app.tenants.models import Tenant
from app.tenants.store import TenantStore

logger = logging.getLogger(__name__)

# site_key -> (tenant, or None for a key that matched none, time cached).
# Unknown keys are cached too, so repeating one costs one Firestore read a
# minute, not one per request.
_cache: dict[str, tuple[Tenant | None, float]] = {}

# Real keys are short ("pk_pyramid_yogshala_001"). Longer ones are refused without
# a lookup, and the cache is cleared if made-up keys ever fill it, so a
# flood of them cannot grow memory without bound.
MAX_SITE_KEY_LENGTH = 64
MAX_CACHED_KEYS = 10_000


def _cached_lookup(store: TenantStore, site_key: str) -> Tenant | None:
    ttl = get_settings().tenant_cache_seconds
    now = time.monotonic()

    entry = _cache.get(site_key)
    if entry and (now - entry[1]) < ttl:
        return entry[0]

    tenant = store.get_by_site_key(site_key)
    if len(_cache) >= MAX_CACHED_KEYS:
        _cache.clear()
    _cache[site_key] = (tenant, now)
    return tenant


def _rejected_limit():
    return parse(get_settings().rate_limit_rejected_per_visitor)


def _reject(request: Request, code: int, detail: str) -> NoReturn:
    """Refuse the request, counting it against the visitor."""
    limiter.limiter.hit(_rejected_limit(), "rejected", client_ip(request))
    raise HTTPException(status_code=code, detail=detail)


def cached_tenant(site_key: str) -> Tenant | None:
    """The tenant get_tenant last resolved for this key, if any.

    For code that runs after get_tenant in the same request, such as the
    per-tenant rate limit, so it needs no second lookup.
    """
    entry = _cache.get(site_key)
    return entry[0] if entry else None


def _host_from(url: str) -> str:
    """Pull the hostname out of an Origin or Referer header."""
    without_scheme = url.split("://", 1)[-1]
    host_and_port = without_scheme.split("/", 1)[0]
    return host_and_port.split(":", 1)[0].lower()


def _origin_allowed(tenant: Tenant, origin: str | None) -> bool:
    if not origin:
        # No Origin header means a same-origin or non-browser request.
        # We allow it here; the rate limit is what protects us.
        return True

    host = _host_from(origin)
    for allowed in tenant.allowed_domains:
        allowed = allowed.lower()
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def get_tenant(
    request: Request,
    x_site_key: str = Header(...),
    origin: str | None = Header(default=None),
) -> Tenant:
    """Resolve and validate the tenant for this request.

    Runs before the route's own rate limits, so it counts its refusals
    itself: a visitor with too many is refused before the lookup.
    """
    store: TenantStore = request.app.state.tenant_store

    if not limiter.limiter.test(_rejected_limit(), "rejected", client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )

    tenant = None
    if len(x_site_key) <= MAX_SITE_KEY_LENGTH:
        tenant = _cached_lookup(store, x_site_key)
    if tenant is None:
        logger.warning("Unknown site key")
        _reject(request, status.HTTP_401_UNAUTHORIZED, "Invalid site key")

    if not tenant.is_active:
        _reject(request, status.HTTP_403_FORBIDDEN,
                "This chat is not currently available")

    if not _origin_allowed(tenant, origin):
        logger.warning(
            "Blocked origin %s for tenant %s", origin, tenant.tenant_id
        )
        _reject(request, status.HTTP_403_FORBIDDEN,
                "This domain is not allowed to use this site key")

    return tenant