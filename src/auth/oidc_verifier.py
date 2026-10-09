"""
Generic OIDC JWT token verifier.

Works with any OIDC-compliant provider — AWS Cognito, Microsoft Entra ID,
Okta, Auth0, etc.  Provider-specific behaviour is driven entirely by
configuration (issuer, audience, JWKS URI, group claim name, roles claim name).

Cognito example::

    OIDC_ISSUER=https://cognito-idp.us-east-2.amazonaws.com/<pool-id>
    OIDC_AUDIENCE=<app-client-id>
    OIDC_GROUP_CLAIM=cognito:groups

Entra ID example (with App Roles)::

    OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
    OIDC_AUDIENCE=<application-id>
    OIDC_ROLES_CLAIM=roles
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

logger = logging.getLogger("redshift_mcp.auth.oidc")

# Algorithms we accept — explicitly list to block ``alg: none`` attacks.
_ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512")

# How long to cache JWKS in memory (seconds).
_JWKS_TTL = 3600  # 1 hour


@dataclass(frozen=True)
class Principal:
    """Verified caller identity extracted from a valid OIDC token."""

    sub: str
    email: str
    groups: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    issuer: str = ""
    raw_claims: dict[str, Any] = field(default_factory=dict, repr=False)


class OIDCVerifier:
    """Verify JWTs from any OIDC-compliant identity provider.

    Parameters
    ----------
    issuer
        The expected ``iss`` claim, e.g. ``https://cognito-idp.us-east-2.amazonaws.com/<pool>``.
    audience
        The expected ``aud`` claim (Cognito app-client ID, Entra application ID, …).
    jwks_uri
        Explicit JWKS endpoint.  When empty, auto-discovered from
        ``{issuer}/.well-known/openid-configuration``.
    required_scopes
        Scopes that must be present in the token's ``scope``/``scp`` claim.
    group_claim
        Name of the claim carrying group membership (``groups``, ``cognito:groups``, …).
    roles_claim
        Name of the claim carrying app role assignments (``roles`` for Entra ID).
    """

    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_uri: str = "",
        required_scopes: list[str] | None = None,
        group_claim: str = "groups",
        roles_claim: str = "roles",
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self._explicit_jwks_uri = jwks_uri
        self.required_scopes = required_scopes or []
        self.group_claim = group_claim
        self.roles_claim = roles_claim

        # In-memory JWKS cache — never persisted to disk.
        self._jwks_keys: list[dict[str, Any]] = []
        self._jwks_fetched_at: float = 0.0
        self._resolved_jwks_uri: str = ""

    # ------------------------------------------------------------------
    # JWKS management
    # ------------------------------------------------------------------

    async def _resolve_jwks_uri(self) -> str:
        """Return the JWKS URI, discovering it from the issuer if needed."""
        if self._explicit_jwks_uri:
            return self._explicit_jwks_uri

        if self._resolved_jwks_uri:
            return self._resolved_jwks_uri

        discovery_url = f"{self.issuer}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(discovery_url)
                resp.raise_for_status()
                data = resp.json()
                uri = data.get("jwks_uri", "")
                if not uri:
                    raise ValueError("No jwks_uri in discovery document")
                self._resolved_jwks_uri = uri
                logger.info("Discovered JWKS URI: %s", uri)
                return uri
        except Exception:
            # Fallback: standard Cognito / generic path
            fallback = f"{self.issuer}/.well-known/jwks.json"
            logger.warning(
                "OIDC discovery failed at %s; falling back to %s",
                discovery_url,
                fallback,
            )
            self._resolved_jwks_uri = fallback
            return fallback

    async def _fetch_jwks(self, force: bool = False) -> list[dict[str, Any]]:
        """Fetch JWKS keys, using the in-memory cache when fresh."""
        now = time.time()
        if not force and self._jwks_keys and (now - self._jwks_fetched_at) < _JWKS_TTL:
            return self._jwks_keys

        uri = await self._resolve_jwks_uri()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(uri)
                resp.raise_for_status()
                data = resp.json()
                keys = data.get("keys", [])
                if not keys:
                    logger.warning("JWKS response from %s contains no keys", uri)
                    return self._jwks_keys  # return stale cache if available
                self._jwks_keys = keys
                self._jwks_fetched_at = now
                logger.debug("Fetched %d JWKS keys from %s", len(keys), uri)
                return keys
        except Exception as exc:
            logger.warning("Failed to fetch JWKS from %s: %s", uri, exc)
            return self._jwks_keys  # return stale cache

    async def _get_public_key(self, kid: str, alg: str):
        """Locate the public key for *kid*, refreshing on cache miss."""
        if alg not in _ALLOWED_ALGORITHMS:
            raise jwt.InvalidAlgorithmError(f"Algorithm {alg!r} is not allowed")

        keys = await self._fetch_jwks()
        key_dict = next((k for k in keys if k.get("kid") == kid), None)

        # Unknown kid → try one forced refresh (key rotation).
        if key_dict is None:
            logger.info("Key kid=%s not in cache; refreshing JWKS", kid)
            keys = await self._fetch_jwks(force=True)
            key_dict = next((k for k in keys if k.get("kid") == kid), None)

        if key_dict is None:
            raise jwt.InvalidKeyError(f"No JWKS key found for kid={kid}")

        return RSAAlgorithm.from_jwk(json.dumps(key_dict))

    # ------------------------------------------------------------------
    # Token verification
    # ------------------------------------------------------------------

    async def verify_token(self, token: str) -> Principal | None:
        """Verify *token* and return a ``Principal`` on success, else ``None``.

        Checks:
        * Signature (RSA, allowed algorithms only)
        * Issuer (``iss``)
        * Audience (``aud``) — or ``client_id`` for Cognito access tokens
        * Expiry (``exp``) and not-before (``nbf``)
        * Required scopes
        """
        if not token:
            return None

        try:
            # -- Read header (unverified) to find kid + alg ----------------
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")
            alg = unverified_header.get("alg", "RS256")

            if not kid:
                logger.warning("JWT has no kid in header")
                return None
            if alg not in _ALLOWED_ALGORITHMS:
                logger.warning("JWT uses disallowed algorithm: %s", alg)
                return None

            # -- Resolve public key ----------------------------------------
            public_key = await self._get_public_key(kid, alg)

            # -- Peek at unverified claims to decide aud strategy ----------
            unverified = jwt.decode(
                token, options={"verify_signature": False, "verify_exp": False}
            )
            token_use = unverified.get("token_use", "")

            decode_opts: dict[str, Any] = {
                "algorithms": list(_ALLOWED_ALGORITHMS),
                "issuer": self.issuer,
                "options": {
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                },
            }

            # Cognito access tokens carry ``client_id`` instead of ``aud``.
            if token_use == "access":
                decode_opts["options"]["verify_aud"] = False
            else:
                decode_opts["audience"] = self.audience

            # -- Decode & verify -------------------------------------------
            claims = jwt.decode(token, public_key, **decode_opts)

            # Validate client_id on Cognito-style access tokens.
            if token_use == "access":
                cid = claims.get("client_id", "")
                if cid != self.audience:
                    logger.warning(
                        "Access token client_id mismatch: got %s, expected %s",
                        cid,
                        self.audience,
                    )
                    return None

            # -- Check required scopes ------------------------------------
            if self.required_scopes:
                token_scopes = set()
                # Both OIDC ``scope`` and Entra ``scp`` are commonly space-separated.
                raw_scope = claims.get("scope", "")
                if isinstance(raw_scope, str):
                    token_scopes.update(raw_scope.split())
                scp = claims.get("scp", [])
                if isinstance(scp, list):
                    token_scopes.update(scp)
                elif isinstance(scp, str):
                    token_scopes.update(scp.split())
                missing = set(self.required_scopes) - token_scopes
                if missing:
                    logger.warning("Token missing required scopes: %s", missing)
                    return None

            # -- Build principal -------------------------------------------
            groups_raw = claims.get(self.group_claim, [])
            groups = groups_raw if isinstance(groups_raw, list) else []

            roles_raw = claims.get(self.roles_claim, [])
            roles = roles_raw if isinstance(roles_raw, list) else []
            if not claims.get("sub"):
                logger.warning("JWT has no subject claim")
                return None

            return Principal(
                sub=claims.get("sub", ""),
                email=(
                    claims.get("email", "")
                    or claims.get("cognito:username", "")
                    or claims.get("preferred_username", "")
                    or claims.get("upn", "")
                    or claims.get("sub", "")
                ),
                groups=[group for group in groups if isinstance(group, str)],
                roles=[role for role in roles if isinstance(role, str)],
                issuer=self.issuer,
                raw_claims=claims,
            )

        except jwt.ExpiredSignatureError:
            logger.warning("JWT has expired")
            return None
        except jwt.InvalidAlgorithmError as exc:
            logger.warning("JWT algorithm rejected: %s", exc)
            return None
        except jwt.InvalidTokenError as exc:
            logger.warning("JWT verification failed: %s", exc)
            return None
        except Exception:
            logger.exception("Unexpected error during JWT verification")
            return None
