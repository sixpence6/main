from __future__ import annotations

import ipaddress

from starlette.requests import Request


def _trusted_proxy(settings, peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for raw in settings.trusted_proxy_cidrs:
        try:
            if address in ipaddress.ip_network(raw, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """Return the effective client address without trusting arbitrary headers.

    X-Real-IP is accepted only when the immediate peer is explicitly listed in
    TRUSTED_PROXY_CIDRS. The frontend proxy overwrites that header with the
    socket peer address; a direct browser cannot forge it. Any malformed value
    falls back to the actual peer address instead of failing open.
    """
    peer = request.client.host if request.client else "unknown"
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not peer or not _trusted_proxy(settings, peer):
        return peer or "unknown"
    raw = request.headers.get("x-real-ip")
    if not raw:
        return peer
    candidate = raw.strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return peer
    return candidate
