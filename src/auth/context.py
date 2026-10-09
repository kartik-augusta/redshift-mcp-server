"""
Request context — propagates authenticated principal and request metadata
through the call stack using ``contextvars``.

Usage in tool handlers::

    from src.auth.context import get_current_principal, get_request_id

    principal = get_current_principal()   # Principal | None
    request_id = get_request_id()         # str (UUID)
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Optional

from src.auth.oidc_verifier import Principal

# Context variables — set per-request in the auth middleware.
_principal_var: contextvars.ContextVar[Optional[Principal]] = contextvars.ContextVar(
    "principal", default=None
)
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


# ------------------------------------------------------------------
# Setters (called by middleware)
# ------------------------------------------------------------------

def set_request_context(
    principal: Principal | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Set the request context for the current async task / thread."""
    _principal_var.set(principal)
    _request_id_var.set(request_id or str(uuid.uuid4()))
    _correlation_id_var.set(correlation_id or "")


def clear_request_context() -> None:
    """Clear the request context."""
    _principal_var.set(None)
    _request_id_var.set("")
    _correlation_id_var.set("")


# ------------------------------------------------------------------
# Getters (called by tool handlers, logging, etc.)
# ------------------------------------------------------------------

def get_current_principal() -> Principal | None:
    """Return the authenticated principal for the current request, or ``None``."""
    return _principal_var.get()


def get_request_id() -> str:
    """Return the unique request ID for the current request."""
    return _request_id_var.get()


def get_correlation_id() -> str:
    """Return the correlation ID passed by the caller, if any."""
    return _correlation_id_var.get()
