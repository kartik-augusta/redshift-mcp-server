"""
Validated, fail-closed configuration for the Redshift MCP Server.

All tunables are read from environment variables.  Required variables that are
missing or invalid cause an immediate ``SystemExit`` so the server never
silently starts in an unsafe state.

Cognito is supported as a standard OIDC provider — point ``OIDC_ISSUER`` at
``https://cognito-idp.<region>.amazonaws.com/<pool-id>`` and set
``OIDC_AUDIENCE`` to the Cognito app-client ID.  No Cognito-specific code is
needed.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require(name: str) -> str:
    """Return an env var or abort startup with a clear message."""
    val = os.environ.get(name)
    if not val:
        print(f"❌ FATAL: required environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def _optional(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _optional_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"❌ FATAL: {name}={raw!r} is not a valid integer.", file=sys.stderr)
        sys.exit(1)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"❌ FATAL: {name}={raw!r} is not a valid float.", file=sys.stderr)
        sys.exit(1)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def _csv(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """Immutable, validated server configuration."""

    # ── Transport ────────────────────────────────────────────────────────
    transport: str = "stdio"            # "stdio" | "http"
    host: str = "0.0.0.0"
    port: int = 8001
    mcp_public_url: str = ""            # Required when transport=http

    # ── Authentication ───────────────────────────────────────────────────
    auth_mode: str = "none"             # "oidc" | "api-key" | "none"
    mcp_api_key: str | None = None      # Used only when auth_mode is api-key

    # ── OIDC (generic — works with Cognito, Entra ID, etc.) ─────────────
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_uri: str = ""             # Auto-discovered from issuer if empty
    oidc_required_scopes: list[str] = field(default_factory=list)
    oidc_group_claim: str = "groups"    # Claim name for group membership
    oidc_roles_claim: str = "roles"     # Claim name for app role assignments

    # ── Authorization ────────────────────────────────────────────────────
    authz_policy_path: str = ""         # Path to policy YAML

    # ── CORS ─────────────────────────────────────────────────────────────
    cors_origins: list[str] = field(default_factory=list)

    # ── Schema access control ────────────────────────────────────────────
    allowed_schemas: list[str] = field(default_factory=lambda: [
        "gold_capsaai", "gold_capsaai_cefi", "gold_capsaai_cspp",
        "report_capsaai", "report_insight",
    ])
    default_schema: str = "gold_capsaai"

    # ── Query limits ─────────────────────────────────────────────────────
    max_rows: int = 500
    max_export_rows: int = 5000
    max_response_bytes: int = 10 * 1024 * 1024  # 10 MB

    # ── Database connection ──────────────────────────────────────────────
    rs_host: str = ""
    rs_port: int = 5439
    rs_db: str = ""
    rs_user: str = ""
    rs_pass: str = ""
    db_secret_arn: str = ""             # Secrets Manager ARN (alternative to rs_pass)
    db_pool_min: int = 2
    db_pool_max: int = 10
    db_statement_timeout_ms: int = 30000  # 30 s

    # ── Resilience ───────────────────────────────────────────────────────
    max_retries: int = 3
    connect_timeout: int = 15

    # ── SSH tunnel ───────────────────────────────────────────────────────
    ssh_tunnel_enabled: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key_file: str = ""
    ssh_password: str = ""
    ssh_keepalive: float = 20.0
    local_port: int = 5439
    port_scan_range: int = 10

    # ── Observability ────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "text"            # "text" | "json"

    # ── Shutdown ─────────────────────────────────────────────────────────
    shutdown_grace_period_s: int = 30


def _validate(s: Settings) -> None:
    """Validate cross-field constraints.  Calls ``sys.exit(1)`` on failure."""
    errors: list[str] = []

    # Transport
    if s.transport not in ("stdio", "http"):
        errors.append(f"TRANSPORT must be 'stdio' or 'http', got '{s.transport}'.")

    # Fail-closed: HTTP requires authentication
    if s.transport == "http":
        if s.auth_mode == "none":
            errors.append(
                "TRANSPORT=http requires AUTH_MODE=oidc or AUTH_MODE=api-key. "
                "The server will not start an unauthenticated HTTP endpoint."
            )
        if not s.mcp_public_url:
            errors.append("MCP_PUBLIC_URL is required when TRANSPORT=http.")
        elif "<" in s.mcp_public_url:
            errors.append(
                f"MCP_PUBLIC_URL contains a placeholder: '{s.mcp_public_url}'. "
                "Set it to the actual public URL."
            )

    # Auth mode
    if s.auth_mode not in ("oidc", "api-key", "none"):
        errors.append(f"AUTH_MODE must be 'oidc', 'api-key', or 'none', got '{s.auth_mode}'.")

    if s.auth_mode == "api-key" and not s.mcp_api_key:
        errors.append("MCP_API_KEY is required when AUTH_MODE=api-key.")
    if s.auth_mode != "api-key" and s.mcp_api_key:
        errors.append("MCP_API_KEY can only be set when AUTH_MODE=api-key.")

    # OIDC
    if s.auth_mode == "oidc":
        if not s.oidc_issuer:
            errors.append("OIDC_ISSUER is required when AUTH_MODE=oidc.")
        if not s.oidc_audience:
            errors.append("OIDC_AUDIENCE is required when AUTH_MODE=oidc.")
        if not s.authz_policy_path:
            errors.append("AUTHZ_POLICY_PATH is required when AUTH_MODE=oidc.")

    if s.transport == "http" and not s.authz_policy_path:
        errors.append("AUTHZ_POLICY_PATH is required when TRANSPORT=http.")

    # Database
    if not s.rs_host:
        errors.append("RS_HOST is required.")
    if not s.rs_db:
        errors.append("RS_DB is required.")
    if not s.rs_pass and not s.db_secret_arn:
        # rs_user may have a default in some setups, but password is mandatory
        errors.append("Either RS_PASS or DB_SECRET_ARN is required for database credentials.")

    # Authorization policy
    if s.authz_policy_path:
        if not os.path.isfile(s.authz_policy_path):
            errors.append(
                f"AUTHZ_POLICY_PATH='{s.authz_policy_path}' does not exist or is not a file."
            )

    if errors:
        print("❌ FATAL: configuration validation failed:", file=sys.stderr)
        for e in errors:
            print(f"   • {e}", file=sys.stderr)
        sys.exit(1)


def load_settings() -> Settings:
    """Build, validate, and return the application settings from env vars."""
    s = Settings(
        # Transport
        transport=_optional_str("TRANSPORT", "stdio").lower(),
        host=_optional_str("HOST", "0.0.0.0"),
        port=_int("PORT", 8001),
        mcp_public_url=_optional_str("MCP_PUBLIC_URL", ""),

        # Auth
        auth_mode=_optional_str("AUTH_MODE", "none").lower(),
        mcp_api_key=_optional("MCP_API_KEY"),

        # OIDC
        oidc_issuer=_optional_str("OIDC_ISSUER", ""),
        oidc_audience=_optional_str("OIDC_AUDIENCE", ""),
        oidc_jwks_uri=_optional_str("OIDC_JWKS_URI", ""),
        oidc_required_scopes=[
            s.strip() for s in _optional_str("OIDC_REQUIRED_SCOPES", "").split(",") if s.strip()
        ],
        oidc_group_claim=_optional_str("OIDC_GROUP_CLAIM", "groups"),
        oidc_roles_claim=_optional_str("OIDC_ROLES_CLAIM", "roles"),

        # Authz
        authz_policy_path=_optional_str("AUTHZ_POLICY_PATH", ""),

        # CORS
        cors_origins=[
            s.strip() for s in _optional_str("CORS_ORIGINS", "").split(",") if s.strip()
        ],

        # Schema access
        allowed_schemas=_csv(
            "ALLOWED_SCHEMAS",
            "gold_capsaai,gold_capsaai_cefi,gold_capsaai_cspp,report_capsaai,report_insight",
        ),
        default_schema=_optional_str("DEFAULT_SCHEMA", "gold_capsaai").strip().lower(),

        # Query limits
        max_rows=_int("MAX_ROWS", 500),
        max_export_rows=_int("MAX_EXPORT_ROWS", 5000),
        max_response_bytes=_int("MAX_RESPONSE_BYTES", 10 * 1024 * 1024),

        # Database
        rs_host=_optional_str("RS_HOST", ""),
        rs_port=_int("RS_PORT", 5439),
        rs_db=_optional_str("RS_DB", ""),
        rs_user=_optional_str("RS_USER", ""),
        rs_pass=_optional_str("RS_PASS", ""),
        db_secret_arn=_optional_str("DB_SECRET_ARN", ""),
        db_pool_min=_int("DB_POOL_MIN", 2),
        db_pool_max=_int("DB_POOL_MAX", 10),
        db_statement_timeout_ms=_int("DB_STATEMENT_TIMEOUT_MS", 30000),

        # Resilience
        max_retries=_int("MAX_RETRIES", 3),
        connect_timeout=_int("CONNECT_TIMEOUT", 15),

        # SSH tunnel
        ssh_tunnel_enabled=_bool("SSH_TUNNEL"),
        ssh_host=_optional_str("SSH_HOST", ""),
        ssh_port=_int("SSH_PORT", 22),
        ssh_user=_optional_str("SSH_USER", ""),
        ssh_key_file=_optional_str("SSH_KEY_FILE", ""),
        ssh_password=_optional_str("SSH_PASSWORD", ""),
        ssh_keepalive=_float("SSH_KEEPALIVE", 20.0),
        local_port=_int("LOCAL_PORT", 5439),
        port_scan_range=_int("PORT_SCAN_RANGE", 10),

        # Observability
        log_level=_optional_str("LOG_LEVEL", "INFO").upper(),
        log_format=_optional_str("LOG_FORMAT", "text").lower(),

        # Shutdown
        shutdown_grace_period_s=_int("SHUTDOWN_GRACE_PERIOD_S", 30),
    )

    _validate(s)
    return s
