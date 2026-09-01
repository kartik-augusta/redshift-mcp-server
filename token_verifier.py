"""
AWS Cognito JWT Token Verifier for Redshift MCP Server.

Validates OIDC tokens issued by AWS Cognito User Pools using RS256 signature verification,
issuer check, audience/client_id check, and token expiration.
"""

import json
import logging
import os
import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

logger = logging.getLogger("redshift_mcp.auth")


class CognitoTokenVerifier:
    """
    Validates AWS Cognito JWTs (both Access Tokens and ID Tokens).
    Fetches and caches JWKS from Cognito's well-known endpoint or loads from a local fallback file.
    """

    def __init__(
        self,
        user_pool_id: str,
        client_id: str,
        region: str = "us-east-2",
        jwks_file: str | None = None,
    ):
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.region = region
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.jwks_uri = f"{self.issuer}/.well-known/jwks.json"
        self.jwks_file = jwks_file or os.path.join(
            os.path.dirname(__file__), "cognito_jwks.json"
        )
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_time: float = 0.0

    async def get_jwks(self) -> dict[str, Any] | None:
        """
        Fetch and cache Cognito JWKS.
        Tries online fetch first; if cached or if network is unavailable, falls back to cache/file.
        """
        now = time.time()
        # Return in-memory cache if fresh (1 hour TTL)
        if self._jwks_cache and (now - self._jwks_cache_time) < 3600:
            return self._jwks_cache

        # Attempt to fetch from Cognito JWKS endpoint
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.jwks_uri)
                if resp.status_code == 200:
                    data = resp.json()
                    self._jwks_cache = data
                    self._jwks_cache_time = now
                    # Persist to local cache file for offline resilience
                    try:
                        with open(self.jwks_file, "w", encoding="utf-8") as f:
                            json.dump(data, f)
                    except Exception:
                        pass
                    return data
        except Exception as e:
            logger.debug("Could not fetch online JWKS from %s: %s", self.jwks_uri, e)

        # Fallback to in-memory cache if existing
        if self._jwks_cache:
            return self._jwks_cache

        # Fallback to local file if available
        if os.path.exists(self.jwks_file):
            try:
                with open(self.jwks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._jwks_cache = data
                    self._jwks_cache_time = now
                    logger.info("Loaded Cognito JWKS from local fallback file %s", self.jwks_file)
                    return data
            except Exception as e:
                logger.error("Failed to read local JWKS fallback file: %s", e)

        return None

    async def verify_token(self, token: str) -> dict[str, Any] | None:
        """
        Verify a Cognito JWT.
        Returns the decoded token claims dictionary if valid, or None if invalid.
        """
        if not token:
            return None

        try:
            # 1. Read unverified header to locate the key ID (kid) and algorithm
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")
            alg = unverified_header.get("alg", "RS256")

            if not kid or alg != "RS256":
                logger.warning("Invalid JWT header: kid=%s, alg=%s", kid, alg)
                return None

            # 2. Get JWKS keys
            jwks = await self.get_jwks()
            if not jwks or "keys" not in jwks:
                logger.warning("No Cognito JWKS keys available to verify token")
                return None

            key_dict = next((k for k in jwks["keys"] if k.get("kid") == kid), None)
            if not key_dict:
                logger.warning("JWKS key with kid=%s not found in Cognito keys", kid)
                return None

            public_key = RSAAlgorithm.from_jwk(json.dumps(key_dict))

            # 3. Decode and verify signature & claims
            # In Cognito:
            # - ID tokens contain 'aud' = client_id and 'token_use' = 'id'
            # - Access tokens contain 'client_id' = client_id and 'token_use' = 'access'
            unverified_claims = jwt.decode(
                token,
                options={"verify_signature": False, "verify_exp": False},
            )
            token_use = unverified_claims.get("token_use")

            decode_kwargs: dict[str, Any] = {
                "algorithms": ["RS256"],
                "issuer": self.issuer,
                "options": {
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                },
            }

            if token_use == "id":
                decode_kwargs["audience"] = self.client_id
            else:
                # For access tokens, audience check is disabled; client_id is validated below
                decode_kwargs["options"]["verify_aud"] = False

            payload = jwt.decode(token, public_key, **decode_kwargs)

            # Validate client_id on access token
            if token_use == "access":
                token_client_id = payload.get("client_id")
                if token_client_id and token_client_id != self.client_id:
                    logger.warning(
                        "Cognito access token client_id mismatch: got %s, expected %s",
                        token_client_id,
                        self.client_id,
                    )
                    return None

            return payload

        except jwt.ExpiredSignatureError:
            logger.warning("Cognito JWT token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning("Cognito JWT verification failed: %s", e)
            return None
        except Exception:
            logger.exception("Unexpected error during Cognito JWT verification")
            return None
