"""Small deployment guard for privileged social/media routes.

Until ``OPERATOR_TOKEN`` is configured, ``require_local_operator`` provides a
best-effort network guard for the routes it is attached to. It never replaces
``require_operator``, which is the single token gate wired into the API router.
"""

import hmac
import ipaddress

from fastapi import HTTPException, Request

from .config import settings
from .logging import get_logger

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

security_log = get_logger("security")

# Routes that must stay reachable without an operator token: liveness probes
# and the two inbound flows controlled by another party (Meta webhook and
# OAuth redirects). Everything else on the router requires the token —
# new routes are protected by default (fail closed).
OPERATOR_EXEMPT_PATHS = {
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    "/api/threads/webhook",
    "/api/threads/oauth/callback",
}


def _is_exempt_path(path: str) -> bool:
    if path in OPERATOR_EXEMPT_PATHS:
        return True
    return path.startswith("/api/social/oauth/") and path.endswith("/callback")


def require_operator(request: Request) -> None:
    """Require ``X-Operator-Token`` for every non-exempt /api route.

    The token is compared with ``hmac.compare_digest`` (constant time) and is
    never logged. When the token is not configured, development answers
    without it (with a one-time warning) so a fresh checkout keeps working;
    any other environment fails closed with 503 until the operator sets
    ``OPERATOR_TOKEN``.
    """
    if _is_exempt_path(request.url.path):
        return
    expected = (settings.operator_token or "").strip()
    provided = request.headers.get("X-Operator-Token", "")
    if expected:
        if provided and hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            return
        raise HTTPException(status_code=401, detail="Missing or invalid operator token")
    if settings.app_env == "development":
        security_log.warning(
            "operator_token_missing",
            env=settings.app_env,
            path=request.url.path,
            hint="Requests are unauthenticated; set OPERATOR_TOKEN in .env",
        )
        return
    raise HTTPException(status_code=503, detail="operator token is not configured")


def require_local_operator(request: Request) -> None:
    """Reject privileged operations when the deployment is not local-only.

    ``social_local_only`` is deliberately enabled by default. ``testclient``
    is accepted so deterministic API tests can exercise guarded routes without
    opening a real network boundary.
    """

    if not settings.social_local_only:
        return
    client_host = request.client.host if request.client else ""
    is_private_container_peer = False
    try:
        address = ipaddress.ip_address(client_host)
        is_private_container_peer = any(address in network for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        ))
    except ValueError:
        pass
    if client_host not in LOOPBACK_HOSTS and not is_private_container_peer:
        raise HTTPException(status_code=403, detail="Privileged social operations are local-only")


def require_manual_token_import() -> None:
    if not settings.allow_manual_token_import:
        raise HTTPException(status_code=403, detail="Manual token import is disabled")
