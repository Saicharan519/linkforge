"""Who is the client? The ONLY place in the app that reads X-Forwarded-For (ARCHITECTURE §6.3).

Anyone can send `X-Forwarded-For: 1.2.3.4` themselves. If we believed it blindly, an attacker could
pick a fresh "IP" per request and walk straight past the rate limiter. So the header is trusted
only as far as we know our own proxies:

- Every proxy that forwards a request APPENDS the address it received the connection from.
  A request that passed through one proxy looks like `<whatever the client claimed>, <real client>`.
- `TRUSTED_PROXY_COUNT` is how many proxies of ours sit in front of the API (local: 0, kind with
  Traefik: 1). The real client is the entry that many positions from the RIGHT, because everything
  to the left of it could have been written by the client.
- With 0 trusted proxies the header is ignored and the TCP peer address is used.

Whenever the header cannot be trusted (too few entries for the configured proxy count, or the
entry is not an IP address) we fall back to the TCP peer. That is the safe direction: all such
requests share the proxy's own bucket instead of letting the caller pick one.
"""

import ipaddress

from fastapi import Request


def client_ip(request: Request, trusted_proxy_count: int) -> str:
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_count <= 0:
        return peer

    # A request can carry the header more than once; HTTP says that equals one comma-joined list.
    joined = ",".join(request.headers.getlist("x-forwarded-for"))
    entries = [part.strip() for part in joined.split(",") if part.strip()]
    if len(entries) < trusted_proxy_count:
        return peer

    candidate = entries[-trusted_proxy_count]
    try:
        # Normalising also makes "::1" and "0:0:0:0:0:0:0:1" the same bucket.
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer
