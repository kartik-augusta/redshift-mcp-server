"""
Unit tests for src.settings — configuration validation and fail-closed behaviour.

These tests manipulate ``os.environ`` and call ``load_settings()`` directly.
They do NOT start the server or connect to any database.
"""

import os
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid env for a stdio server (no auth required).
_STDIO_BASE = {
    "TRANSPORT": "stdio",
    "RS_HOST": "test-cluster.redshift.amazonaws.com",
    "RS_DB": "testdb",
    "RS_USER": "readonly",
    "RS_PASS": "secret",
}

# Minimal valid env for an HTTP server with OIDC.
_HTTP_OIDC_BASE = {
    **_STDIO_BASE,
    "TRANSPORT": "http",
    "AUTH_MODE": "oidc",
    "OIDC_ISSUER": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_abc123",
    "OIDC_AUDIENCE": "my-client-id",
    "AUTHZ_POLICY_PATH": "policy.yaml.example",
    "MCP_PUBLIC_URL": "https://mcp.example.com",
}

# Minimal valid env for an HTTP server with API key only.
_HTTP_APIKEY_BASE = {
    **_STDIO_BASE,
    "TRANSPORT": "http",
    "AUTH_MODE": "api-key",
    "MCP_API_KEY": "dev-key-12345",
    "AUTHZ_POLICY_PATH": "policy.yaml.example",
    "MCP_PUBLIC_URL": "https://mcp.example.com",
}


def _load_settings_with_env(env: dict):
    """Import settings fresh with a controlled environment."""
    # We need to reimport settings each time because load_settings reads env
    # at call time.
    with patch.dict(os.environ, env, clear=True), patch("dotenv.load_dotenv"):
        # Re-import to get fresh load
        import importlib
        import src.settings as mod
        importlib.reload(mod)
        return mod.load_settings()


# ---------------------------------------------------------------------------
# Tests: stdio (local dev)
# ---------------------------------------------------------------------------


class TestStdioStartup:
    """stdio mode should start without auth."""

    def test_stdio_no_auth_succeeds(self):
        s = _load_settings_with_env(_STDIO_BASE)
        assert s.transport == "stdio"
        assert s.auth_mode == "none"

    def test_stdio_with_oidc_succeeds(self):
        env = {**_STDIO_BASE, "AUTH_MODE": "oidc",
               "OIDC_ISSUER": "https://issuer.example.com",
               "OIDC_AUDIENCE": "aud",
               "AUTHZ_POLICY_PATH": "policy.yaml.example"}
        s = _load_settings_with_env(env)
        assert s.auth_mode == "oidc"


# ---------------------------------------------------------------------------
# Tests: HTTP fail-closed
# ---------------------------------------------------------------------------


class TestHttpFailClosed:
    """HTTP mode must refuse to start without authentication."""

    def test_http_no_auth_exits(self):
        env = {**_STDIO_BASE, "TRANSPORT": "http", "MCP_PUBLIC_URL": "https://mcp.example.com"}
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_http_oidc_succeeds(self):
        s = _load_settings_with_env(_HTTP_OIDC_BASE)
        assert s.transport == "http"
        assert s.auth_mode == "oidc"

    def test_http_apikey_succeeds(self):
        s = _load_settings_with_env(_HTTP_APIKEY_BASE)
        assert s.transport == "http"
        assert s.auth_mode == "api-key"
        assert s.mcp_api_key == "dev-key-12345"

    def test_http_api_key_mode_without_key_exits(self):
        env = {**_HTTP_APIKEY_BASE}
        del env["MCP_API_KEY"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_oidc_requires_policy(self):
        env = {**_HTTP_OIDC_BASE}
        del env["AUTHZ_POLICY_PATH"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_http_requires_public_url(self):
        env = {**_HTTP_OIDC_BASE}
        del env["MCP_PUBLIC_URL"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_http_placeholder_url_exits(self):
        env = {**_HTTP_OIDC_BASE, "MCP_PUBLIC_URL": "https://<YOUR_DOMAIN>/mcp"}
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)




# ---------------------------------------------------------------------------
# Tests: OIDC config validation
# ---------------------------------------------------------------------------


class TestOIDCValidation:
    """OIDC auth requires issuer and audience."""

    def test_oidc_no_issuer_exits(self):
        env = {**_HTTP_OIDC_BASE}
        del env["OIDC_ISSUER"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_oidc_no_audience_exits(self):
        env = {**_HTTP_OIDC_BASE}
        del env["OIDC_AUDIENCE"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_oidc_group_claim_default(self):
        s = _load_settings_with_env(_HTTP_OIDC_BASE)
        assert s.oidc_group_claim == "groups"

    def test_oidc_custom_group_claim(self):
        env = {**_HTTP_OIDC_BASE, "OIDC_GROUP_CLAIM": "cognito:groups"}
        s = _load_settings_with_env(env)
        assert s.oidc_group_claim == "cognito:groups"




# ---------------------------------------------------------------------------
# Tests: database config validation
# ---------------------------------------------------------------------------


class TestDatabaseValidation:
    """Database host, name, and credentials are required."""

    def test_missing_rs_host_exits(self):
        env = {**_STDIO_BASE}
        del env["RS_HOST"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_missing_rs_db_exits(self):
        env = {**_STDIO_BASE}
        del env["RS_DB"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_missing_password_and_secret_exits(self):
        env = {**_STDIO_BASE}
        del env["RS_PASS"]
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_secret_arn_instead_of_password_succeeds(self):
        env = {**_STDIO_BASE, "DB_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:123:secret:mcp"}
        del env["RS_PASS"]
        s = _load_settings_with_env(env)
        assert s.db_secret_arn.startswith("arn:")
        assert s.rs_pass == ""


# ---------------------------------------------------------------------------
# Tests: schema and limit defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    """Check that defaults are sane."""

    def test_default_schemas(self):
        s = _load_settings_with_env(_STDIO_BASE)
        assert "gold_capsaai" in s.allowed_schemas
        assert len(s.allowed_schemas) == 5

    def test_custom_schemas(self):
        env = {**_STDIO_BASE, "ALLOWED_SCHEMAS": "schema_a,schema_b"}
        s = _load_settings_with_env(env)
        assert s.allowed_schemas == ["schema_a", "schema_b"]

    def test_max_rows_default(self):
        s = _load_settings_with_env(_STDIO_BASE)
        assert s.max_rows == 500

    def test_invalid_int_exits(self):
        env = {**_STDIO_BASE, "MAX_ROWS": "not-a-number"}
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_pool_defaults(self):
        s = _load_settings_with_env(_STDIO_BASE)
        assert s.db_pool_min == 2
        assert s.db_pool_max == 10
        assert s.db_statement_timeout_ms == 30000

    def test_invalid_auth_mode_exits(self):
        env = {**_STDIO_BASE, "AUTH_MODE": "magic"}
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)

    def test_invalid_transport_exits(self):
        env = {**_STDIO_BASE, "TRANSPORT": "grpc"}
        with pytest.raises(SystemExit):
            _load_settings_with_env(env)
