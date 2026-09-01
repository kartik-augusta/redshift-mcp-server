# config.py — single source of truth for all tunables
"""
Centralised configuration for the Redshift MCP server.
All tunables are read from environment variables with sensible defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Schema access control ────────────────────────────────────────────────────
ALLOWED_SCHEMAS: list[str] = [
    s.strip().lower()
    for s in os.environ.get(
        "ALLOWED_SCHEMAS",
        "gold_capsaai,gold_capsaai_cefi,gold_capsaai_cspp,report_capsaai",
    ).split(",")
    if s.strip()
]
DEFAULT_SCHEMA: str = os.environ.get("DEFAULT_SCHEMA", ALLOWED_SCHEMAS[0])

# ── Query limits ─────────────────────────────────────────────────────────────
MAX_ROWS: int = int(os.environ.get("MAX_ROWS", "500"))
MAX_EXPORT_ROWS: int = int(os.environ.get("MAX_EXPORT_ROWS", "5000"))

# ── Redshift connection ──────────────────────────────────────────────────────
RS_HOST: str = os.environ["RS_HOST"]
RS_PORT: int = int(os.environ.get("RS_PORT", "5439"))
RS_DB: str = os.environ["RS_DB"]
RS_USER: str = os.environ["RS_USER"]
RS_PASS: str = os.environ["RS_PASS"]

# ── Resilience ───────────────────────────────────────────────────────────────
MAX_RETRIES: int = int(os.environ.get("MAX_RETRIES", "3"))
CONNECT_TIMEOUT: int = int(os.environ.get("CONNECT_TIMEOUT", "15"))
SSH_KEEPALIVE: float = float(os.environ.get("SSH_KEEPALIVE", "20"))
PORT_SCAN_RANGE: int = int(os.environ.get("PORT_SCAN_RANGE", "10"))

# ── SSH tunnel ───────────────────────────────────────────────────────────────
SSH_TUNNEL_ENABLED: bool = os.environ.get("SSH_TUNNEL", "false").lower() == "true"
SSH_HOST: str | None = os.environ.get("SSH_HOST")
SSH_PORT: int = int(os.environ.get("SSH_PORT", "22"))
SSH_USER: str | None = os.environ.get("SSH_USER")
SSH_KEY_FILE: str | None = os.environ.get("SSH_KEY_FILE")
SSH_PASSWORD: str | None = os.environ.get("SSH_PASSWORD")
LOCAL_PORT: int = int(os.environ.get("LOCAL_PORT", "5439"))

# ── API key authentication ───────────────────────────────────────────────────
MCP_API_KEY: str | None = os.environ.get("MCP_API_KEY")

# ── AWS Cognito OIDC authentication ─────────────────────────────────────────
COGNITO_USER_POOL_ID: str | None = os.environ.get("COGNITO_USER_POOL_ID")
COGNITO_CLIENT_ID: str | None = os.environ.get("COGNITO_CLIENT_ID")
COGNITO_REGION: str = os.environ.get("COGNITO_REGION", "us-east-2")
MCP_PUBLIC_URL: str = os.environ.get("MCP_PUBLIC_URL", "https://<YOUR_IP_OR_DOMAIN>/mcp")
