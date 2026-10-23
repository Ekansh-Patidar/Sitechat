"""A daily token budget per tenant: a cap on cost, not on request count.

Rate limits count requests, but one long conversation costs far more than
twenty short ones (OWASP LLM06, Unbounded Consumption, 2026). This counts
the tokens each reply actually used and, once a tenant's budget for the day
(UTC) is spent, stops answering until the day turns.

Reads are cached for a minute per instance, with this instance's own use
added locally, so a chat costs one Firestore write and rarely a read. Other
instances' use shows up within the minute; the budget can be overshot by
about that much, which is fine for a cost ceiling.

Fail open: if the counter cannot be read or written, chats go ahead. A
cost ceiling must never be the reason the bot is down.
"""

import logging
import time
from datetime import datetime, timezone

from app.config import get_settings
from app.tenants.models import Tenant
from app.usage.store import UsageStore

logger = logging.getLogger(__name__)

CACHE_SECONDS = 60

# (tenant_id, day) -> [tokens known, monotonic time read]
_cache: dict[tuple[str, str], list] = {}
_warned: set[tuple[str, str]] = set()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def budget_for(tenant: Tenant) -> int:
    """Tokens a day for this tenant; 0 means no budget."""
    return tenant.daily_token_budget or get_settings().daily_token_budget


def _used(store: UsageStore, tenant_id: str, day: str) -> int:
    entry = _cache.get((tenant_id, day))
    now = time.monotonic()
    if entry is None or now - entry[1] >= CACHE_SECONDS:
        entry = [store.tokens_on(tenant_id, day), now]
        _cache[(tenant_id, day)] = entry
    return entry[0]


def over_budget(store: UsageStore | None, tenant: Tenant) -> bool:
    budget = budget_for(tenant)
    if store is None or budget <= 0:
        return False
    day = _today()
    try:
        used = _used(store, tenant.tenant_id, day)
    except Exception:
        logger.exception("Could not read token usage; answering anyway")
        return False
    if used < budget:
        return False
    if (tenant.tenant_id, day) not in _warned:
        _warned.add((tenant.tenant_id, day))
        # An error, so Sentry reports it: the business needs to know.
        logger.error(
            "Tenant %s spent its daily token budget (%d of %d); not answering "
            "until 00:00 UTC", tenant.tenant_id, used, budget,
        )
    return True


def record(store: UsageStore | None, tenant: Tenant, tokens: int) -> None:
    if store is None or tokens <= 0:
        return
    day = _today()
    entry = _cache.get((tenant.tenant_id, day))
    if entry is not None:
        entry[0] += tokens
    try:
        store.add_tokens(tenant.tenant_id, day, tokens)
    except Exception:
        logger.exception("Could not record token usage")
