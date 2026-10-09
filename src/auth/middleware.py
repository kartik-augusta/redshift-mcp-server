"""
ASGI authentication middleware.

Supports two authentication methods:

1. **OIDC bearer tokens** (``Authorization: Bearer <JWT>``) — validated by the
   generic :class:`~src.auth.oidc_verifier.OIDCVerifier`.  Works with Cognito,
   Entra ID, or any OIDC provider.
2. **API key** (``x-api-key`` header) — a static key for local development.
   Attaches a synthetic principal for audit logging.

Also serves RFC 9728 Protected Resource Metadata at
``/.well-known/oauth-protected-resource``.

Routes that bypass authentication:
* ``/healthz``  — liveness probe
* ``/readyz``   — readiness probe
* ``OPTIONS``   — CORS preflight
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.auth.context import set_request_context, clear_request_context
from src.auth.oidc_verifier import OIDCVerifier, Principal

logger = logging.getLogger("redshift_mcp.auth.middleware")

# Paths that never require authentication.
_PUBLIC_PATHS = frozenset({"/healthz", "/readyz"})


class AuthMiddleware:
    """ASGI middleware for OIDC + API-key authentication.

    Parameters
    ----------
    app
        The wrapped ASGI application.
    verifier
        An ``OIDCVerifier`` instance (may be ``None`` if OIDC is not configured).
    api_key
        A static API key for dev usage (may be ``None``).
    public_url
        The external base URL of the server, used in metadata responses.
    scopes_supported
        Delegated API scopes accepted by the OIDC verifier.
    cors_origins
        Allowed CORS origins.  Empty list = no CORS headers.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: OIDCVerifier | None = None,
        api_key: str | None = None,
        public_url: str = "",
        scopes_supported: list[str] | None = None,
        cors_origins: list[str] | None = None,
    ) -> None:
        self.app = app
        self.verifier = verifier
        self.api_key = api_key
        self.public_url = public_url.rstrip("/")
        self.scopes_supported = list(scopes_supported or [])
        self.cors_origins = set(cors_origins or [])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope)
        path = request.url.path
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))

        # ── Public paths (health, metadata) ──────────────────────────────
        if path in _PUBLIC_PATHS:
            set_request_context(request_id=request_id)
            try:
                return await self.app(scope, receive, send)
            finally:
                clear_request_context()

        # ── RFC 9728: Protected Resource Metadata ────────────────────────
        if path.endswith("/.well-known/oauth-protected-resource") and request.method == "GET":
            auth_servers: list[str] = []
            if self.verifier:
                auth_servers.append(self.verifier.issuer)
            prm: dict[str, Any] = {
                "resource": self.public_url,
                "authorization_servers": auth_servers,
                "scopes_supported": self.scopes_supported,
            }
            response = JSONResponse(prm, status_code=200)
            return await response(scope, receive, send)

        # ── CORS preflight ───────────────────────────────────────────────
        if request.method == "OPTIONS":
            return await self._handle_cors_preflight(scope, receive, send, request)

        # ── No auth configured → fail closed ─────────────────────────────
        if not self.api_key and not self.verifier:
            logger.error("No authentication configured — rejecting request")
            response = JSONResponse(
                {"error": "Server misconfiguration — no authentication method available"},
                status_code=500,
            )
            return await response(scope, receive, send)

        # ── 1. Try API key (dev fallback) ────────────────────────────────
        api_key_header = request.headers.get("x-api-key", "").strip()
        if api_key_header and self.api_key and api_key_header == self.api_key:
            principal = Principal(
                sub="api-key-user",
                email="api-key-user@local",
                groups=["api-key-users"],
                issuer="local",
            )
            logger.info(
                "Authenticated via API key | request_id=%s",
                request_id,
            )
            set_request_context(principal=principal, request_id=request_id)
            try:
                return await self.app(scope, receive, send)
            finally:
                clear_request_context()

        # ── 2. Try OIDC bearer token ─────────────────────────────────────
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and self.verifier:
            token = auth_header[7:].strip()
            principal = await self.verifier.verify_token(token)
            if principal is not None:
                logger.info(
                    "Authenticated via OIDC | sub=%s email=%s groups=%s roles=%s request_id=%s",
                    principal.sub,
                    principal.email,
                    principal.groups,
                    principal.roles,
                    request_id,
                )
                set_request_context(principal=principal, request_id=request_id)
                try:
                    return await self.app(scope, receive, send)
                finally:
                    clear_request_context()
            else:
                logger.warning(
                    "OIDC token verification failed | request_id=%s", request_id
                )

        # ── Unauthenticated ──────────────────────────────────────────────
        logger.warning("Unauthenticated request rejected | path=%s request_id=%s", path, request_id)
        www_auth = self._build_www_authenticate()
        response = JSONResponse(
            {"error": "Unauthorized — invalid or missing token / API key"},
            status_code=401,
            headers={"WWW-Authenticate": www_auth},
        )
        return await response(scope, receive, send)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_www_authenticate(self) -> str:
        """Build the WWW-Authenticate header value."""
        if self.verifier and self.public_url:
            prm_url = f"{self.public_url}/.well-known/oauth-protected-resource"
            return f'Bearer realm="redshift-mcp", resource_metadata="{prm_url}"'
        return 'Bearer realm="redshift-mcp"'

    async def _handle_cors_preflight(
        self, scope: Scope, receive: Receive, send: Send, request: Request
    ) -> None:
        """Return CORS preflight response if origin is allowed."""
        origin = request.headers.get("origin", "")
        if not self.cors_origins or origin not in self.cors_origins:
            # No CORS configured or origin not allowed — pass through
            return await self.app(scope, receive, send)

        response = JSONResponse(
            content=None,
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, x-api-key, x-request-id",
                "Access-Control-Max-Age": "86400",
            },
        )
        return await response(scope, receive, send)
