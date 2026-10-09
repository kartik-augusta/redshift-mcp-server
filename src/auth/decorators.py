"""
Tool-level authorization decorator.

Wraps MCP tool handlers to enforce role & group-based access control before execution.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, Any

from src.auth.context import get_current_principal, get_request_id
from src.auth.policy import AuthzPolicy, AuthzDecision

logger = logging.getLogger("redshift_mcp.auth.authz")

# Module-level policy reference — set at startup.
_policy: AuthzPolicy | None = None
_allow_unauthenticated = False


def set_policy(policy: AuthzPolicy, *, allow_unauthenticated: bool = False) -> None:
    """Install the policy and optionally allow unauthenticated local stdio."""
    global _policy, _allow_unauthenticated
    _policy = policy
    _allow_unauthenticated = allow_unauthenticated


def get_policy() -> AuthzPolicy:
    """Return the active policy, or a deny-all fallback."""
    if _policy is None:
        # Safety net — should never happen if startup is correct.
        return AuthzPolicy()  # empty rules = deny everything
    return _policy


def require_authz(tool_name: str, schema_param: str | None = None):
    """Decorator that checks authorization before running a tool handler.

    Parameters
    ----------
    tool_name
        The MCP tool name used for policy lookup.
    schema_param
        If set, the name of the keyword argument containing the schema to
        authorise against.  When ``None``, only tool-level authz is checked.

    Usage::

        @mcp.tool()
        @require_authz("query_data")
        def query_data(sql: str) -> list[dict]:
            ...

        @mcp.tool()
        @require_authz("list_tables", schema_param="schema")
        def list_tables(schema: str = "") -> list[str]:
            ...
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            principal = get_current_principal()
            request_id = get_request_id()
            policy = get_policy()

            # Local stdio has no HTTP middleware or caller token. This bypass
            # is enabled only by server startup for AUTH_MODE=none + stdio.
            if principal is None and _allow_unauthenticated:
                return fn(*args, **kwargs)

            # Resolve schema from kwargs if applicable
            schema: str | None = None
            if schema_param is not None:
                schema = kwargs.get(schema_param) or None

            decision: AuthzDecision = policy.is_allowed(principal, tool_name, schema)

            if decision.allowed:
                logger.info(
                    "AUTHZ ALLOW | tool=%s schema=%s principal=%s rule=%s request_id=%s",
                    tool_name,
                    schema,
                    principal.sub if principal else "none",
                    decision.matched_rule,
                    request_id,
                )
                return fn(*args, **kwargs)
            else:
                logger.warning(
                    "AUTHZ DENY | tool=%s schema=%s principal=%s reason=%s request_id=%s",
                    tool_name,
                    schema,
                    principal.sub if principal else "none",
                    decision.reason,
                    request_id,
                )
                raise PermissionError(
                    f"Access denied: {decision.reason}"
                )

        return wrapper
    return decorator
