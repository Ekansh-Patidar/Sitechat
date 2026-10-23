"""The shared rate limiter.

Kept in its own module so routes can use it without importing app.main,
which would create a cycle: main imports routes, routes imports main.

Counters live in each process's memory (no storage_uri), so every Cloud Run
instance counts separately. See the rate limit settings in app/config.py.
"""

import ipaddress

from fastapi import Request
from slowapi import Limiter

from app.config import get_settings


def _bucket(ip: str) -> str:
    """Group addresses that belong to one visitor.

    An IPv6 subscriber is usually given a whole /64 and can rotate through
    it freely, so keying on the full address would let one visitor step
    around the limit. IPv4 addresses are used as they are.
    """
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def client_ip(request: Request) -> str:
    """The visitor's address, read from X-Forwarded-For.

    request.client.host is useless on Cloud Run: it is the front end's
    internal address, the same for every visitor. Each trusted proxy appends
    the address it saw to X-Forwarded-For, so the client is the entry
    `trusted_proxy_hops` from the end. Entries before that are whatever the
    client sent and must never be trusted.
    """
    hops = get_settings().trusted_proxy_hops
    header = request.headers.get("x-forwarded-for", "")
    entries = [e.strip() for e in header.split(",") if e.strip()]

    if hops > 0 and len(entries) >= hops:
        return _bucket(entries[-hops])

    # No proxy in front (local development), or a header too short to
    # have come through the proxies we expect.
    return _bucket(request.client.host if request.client else "unknown")


def site_key(request: Request) -> str:
    """Key for the per-tenant limit. get_tenant has already validated it."""
    return "site:" + request.headers.get("x-site-key", "")


limiter = Limiter(key_func=client_ip)
