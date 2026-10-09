"""
Unit tests for src.auth.oidc_verifier — generic OIDC JWT verification.

Uses locally-generated RSA keys to test token verification without network calls.
"""

import json
import time
import pytest
import pytest_asyncio

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jwt.algorithms import RSAAlgorithm

from src.auth.oidc_verifier import OIDCVerifier, Principal


# ---------------------------------------------------------------------------
# Fixtures: RSA key pair + JWKS
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def rsa_public_key(rsa_private_key):
    return rsa_private_key.public_key()


@pytest.fixture(scope="module")
def jwk_dict(rsa_public_key):
    """JWK dict (public key) suitable for a JWKS endpoint."""
    pem = rsa_public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_public_key))
    jwk["kid"] = "test-kid-1"
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return jwk


@pytest.fixture
def verifier(jwk_dict):
    """OIDCVerifier with pre-loaded JWKS (no network fetch)."""
    v = OIDCVerifier(
        issuer="https://issuer.example.com",
        audience="test-client-id",
        group_claim="groups",
    )
    # Inject keys directly to avoid network calls
    v._jwks_keys = [jwk_dict]
    v._jwks_fetched_at = time.time()
    return v


def _make_token(
    private_key,
    kid: str = "test-kid-1",
    alg: str = "RS256",
    iss: str = "https://issuer.example.com",
    aud: str = "test-client-id",
    sub: str = "user-123",
    email: str = "user@example.com",
    groups: list | None = None,
    roles: list | None = None,
    exp_offset: int = 3600,
    nbf_offset: int = 0,
    extra_claims: dict | None = None,
    token_use: str | None = None,
) -> str:
    """Create a signed JWT for testing."""
    now = int(time.time())
    payload = {
        "iss": iss,
        "sub": sub,
        "email": email,
        "iat": now,
        "exp": now + exp_offset,
        "nbf": now + nbf_offset,
    }
    if aud is not None:
        payload["aud"] = aud
    if groups is not None:
        payload["groups"] = groups
    if roles is not None:
        payload["roles"] = roles
    if token_use is not None:
        payload["token_use"] = token_use
    if extra_claims:
        payload.update(extra_claims)

    headers = {"kid": kid, "alg": alg}
    return pyjwt.encode(payload, private_key, algorithm=alg, headers=headers)


# ---------------------------------------------------------------------------
# Tests: valid tokens
# ---------------------------------------------------------------------------


class TestValidTokens:

    @pytest.mark.asyncio
    async def test_valid_id_token(self, verifier, rsa_private_key):
        token = _make_token(rsa_private_key, groups=["analysts"])
        result = await verifier.verify_token(token)
        assert result is not None
        assert isinstance(result, Principal)
        assert result.sub == "user-123"
        assert result.email == "user@example.com"
        assert result.groups == ["analysts"]

    @pytest.mark.asyncio
    async def test_valid_access_token_cognito_style(self, verifier, rsa_private_key):
        """Cognito access tokens use client_id instead of aud."""
        token = _make_token(
            rsa_private_key,
            aud=None,
            token_use="access",
            extra_claims={"client_id": "test-client-id"},
        )
        result = await verifier.verify_token(token)
        assert result is not None
        assert result.sub == "user-123"

    @pytest.mark.asyncio
    async def test_multiple_groups(self, verifier, rsa_private_key):
        token = _make_token(rsa_private_key, groups=["analysts", "admins", "viewers"])
        result = await verifier.verify_token(token)
        assert result is not None
        assert set(result.groups) == {"analysts", "admins", "viewers"}

    @pytest.mark.asyncio
    async def test_no_groups_claim(self, verifier, rsa_private_key):
        token = _make_token(rsa_private_key)
        result = await verifier.verify_token(token)
        assert result is not None
        assert result.groups == []

    @pytest.mark.asyncio
    async def test_roles_extracted(self, verifier, rsa_private_key):
        """App roles from the 'roles' claim should be extracted into principal.roles."""
        token = _make_token(rsa_private_key, roles=["capsa-analysts", "capsa-admins"])
        result = await verifier.verify_token(token)
        assert result is not None
        assert set(result.roles) == {"capsa-analysts", "capsa-admins"}

    @pytest.mark.asyncio
    async def test_no_roles_claim(self, verifier, rsa_private_key):
        """When no roles claim is present, principal.roles should be empty."""
        token = _make_token(rsa_private_key)
        result = await verifier.verify_token(token)
        assert result is not None
        assert result.roles == []

    @pytest.mark.asyncio
    async def test_groups_and_roles_together(self, verifier, rsa_private_key):
        """Both groups and roles should be extracted independently."""
        token = _make_token(
            rsa_private_key,
            groups=["group-a"],
            roles=["role-b"],
        )
        result = await verifier.verify_token(token)
        assert result is not None
        assert result.groups == ["group-a"]
        assert result.roles == ["role-b"]


# ---------------------------------------------------------------------------
# Tests: rejected tokens
# ---------------------------------------------------------------------------


class TestRejectedTokens:

    @pytest.mark.asyncio
    async def test_expired_token(self, verifier, rsa_private_key):
        token = _make_token(rsa_private_key, exp_offset=-100)
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_issuer(self, verifier, rsa_private_key):
        token = _make_token(rsa_private_key, iss="https://evil.example.com")
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_audience(self, verifier, rsa_private_key):
        token = _make_token(rsa_private_key, aud="wrong-client-id")
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_kid(self, verifier, rsa_private_key):
        token = _make_token(rsa_private_key, kid="unknown-kid")
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_signature(self, verifier):
        """Token signed with a different key should be rejected."""
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _make_token(other_key)
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_alg_none_rejected(self, verifier, rsa_private_key):
        """Tokens with alg=none must be rejected to prevent bypass attacks."""
        now = int(time.time())
        payload = {
            "iss": "https://issuer.example.com",
            "aud": "test-client-id",
            "sub": "attacker",
            "exp": now + 3600,
            "iat": now,
        }
        # Manually craft a token with alg: none
        header = json.dumps({"alg": "none", "kid": "test-kid-1", "typ": "JWT"})
        import base64
        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        token = f"{b64url(header.encode())}.{b64url(json.dumps(payload).encode())}."
        result = await verifier.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_token(self, verifier):
        result = await verifier.verify_token("")
        assert result is None

    @pytest.mark.asyncio
    async def test_garbage_token(self, verifier):
        result = await verifier.verify_token("not.a.jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_access_token_wrong_client_id(self, verifier, rsa_private_key):
        token = _make_token(
            rsa_private_key,
            aud=None,
            token_use="access",
            extra_claims={"client_id": "wrong-client-id"},
        )
        result = await verifier.verify_token(token)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: scope validation
# ---------------------------------------------------------------------------


class TestScopeValidation:

    @pytest.mark.asyncio
    async def test_required_scopes_present(self, jwk_dict, rsa_private_key):
        v = OIDCVerifier(
            issuer="https://issuer.example.com",
            audience="test-client-id",
            required_scopes=["openid", "profile"],
        )
        v._jwks_keys = [jwk_dict]
        v._jwks_fetched_at = time.time()

        token = _make_token(
            rsa_private_key,
            extra_claims={"scope": "openid profile email"},
        )
        result = await v.verify_token(token)
        assert result is not None

    @pytest.mark.asyncio
    async def test_required_scopes_missing(self, jwk_dict, rsa_private_key):
        v = OIDCVerifier(
            issuer="https://issuer.example.com",
            audience="test-client-id",
            required_scopes=["openid", "admin"],
        )
        v._jwks_keys = [jwk_dict]
        v._jwks_fetched_at = time.time()

        token = _make_token(
            rsa_private_key,
            extra_claims={"scope": "openid profile"},
        )
        result = await v.verify_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_entra_scp_claim(self, jwk_dict, rsa_private_key):
        """Entra ID scopes are accepted from the space-separated 'scp' claim."""
        v = OIDCVerifier(
            issuer="https://issuer.example.com",
            audience="test-client-id",
            required_scopes=["read"],
        )
        v._jwks_keys = [jwk_dict]
        v._jwks_fetched_at = time.time()

        token = _make_token(
            rsa_private_key,
            extra_claims={"scp": "read write"},
        )
        result = await v.verify_token(token)
        assert result is not None
