# Copyright (c) ModelScope Contributors. All rights reserved.
"""
Token Manager for Authentication

Handles JWT token generation, validation, and refresh.
"""

import os
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class TokenClaims:
    """JWT token claims."""
    user_id: str
    email: str
    roles: list[str]
    permissions: list[str]
    exp: datetime
    iat: datetime
    iss: str = "sirchmunk-auth"
    aud: str = "sirchmunk-api"


class TokenManager:
    """
    Manages authentication tokens for Sirchmunk services.

    Features:
    - JWT token generation and validation
    - Automatic token refresh
    - Secure token storage
    - Role-based access control
    """

    def __init__(
        self,
        secret_key: Optional[str] = None,
        access_token_expiry_hours: int = 1,
        refresh_token_expiry_days: int = 30
    ):
        """
        Initialize TokenManager.

        Args:
            secret_key: Signing key (falls back to TOKEN_SECRET env var)
            access_token_expiry_hours: Access token validity period
            refresh_token_expiry_days: Refresh token validity period
        """
        self._secret_key = secret_key or os.getenv("TOKEN_SECRET")
        if not self._secret_key:
            raise ValueError("Secret key must be provided or set TOKEN_SECRET env var")

        self.access_token_expiry = timedelta(hours=access_token_expiry_hours)
        self.refresh_token_expiry = timedelta(days=refresh_token_expiry_days)

        # Token cache
        self._token_cache: Dict[str, Dict[str, Any]] = {}

        logger.info("TokenManager initialized")

    def generate_access_token(self, claims: TokenClaims) -> str:
        """
        Generate a new access token.

        Args:
            claims: Token claims to encode

        Returns:
            Encoded JWT token string
        """
        import jwt

        payload = {
            "user_id": claims.user_id,
            "email": claims.email,
            "roles": claims.roles,
            "permissions": claims.permissions,
            "exp": claims.exp,
            "iat": claims.iat,
            "iss": claims.iss,
            "aud": claims.aud,
        }

        token = jwt.encode(payload, self._secret_key, algorithm="HS256")
        logger.debug(f"Generated access token for user {claims.user_id}")
        return token

    def validate_token(self, token: str) -> Optional[TokenClaims]:
        """
        Validate and decode a token.

        Args:
            token: JWT token string

        Returns:
            TokenClaims if valid, None otherwise
        """
        import jwt

        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=["HS256"],
                options={"require": ["exp", "iat", "user_id"]}
            )

            return TokenClaims(
                user_id=payload["user_id"],
                email=payload["email"],
                roles=payload.get("roles", []),
                permissions=payload.get("permissions", []),
                exp=datetime.fromtimestamp(payload["exp"]),
                iat=datetime.fromtimestamp(payload["iat"]),
                iss=payload.get("iss", "sirchmunk-auth"),
                aud=payload.get("aud", "sirchmunk-api"),
            )
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None

    def refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """
        Refresh an access token using a refresh token.

        Args:
            refresh_token: Valid refresh token

        Returns:
            New access token if successful, None otherwise
        """
        claims = self.validate_token(refresh_token)
        if not claims:
            return None

        # Generate new access token with same claims but new expiry
        new_claims = TokenClaims(
            user_id=claims.user_id,
            email=claims.email,
            roles=claims.roles,
            permissions=claims.permissions,
            exp=datetime.utcnow() + self.access_token_expiry,
            iat=datetime.utcnow(),
        )

        return self.generate_access_token(new_claims)

    def has_permission(self, token: str, required_permission: str) -> bool:
        """
        Check if token has required permission.

        Args:
            token: JWT token
            required_permission: Permission to check

        Returns:
            True if permission granted
        """
        claims = self.validate_token(token)
        if not claims:
            return False

        # Check for admin role (grants all permissions)
        if "admin" in claims.roles:
            return True

        return required_permission in claims.permissions

    def has_role(self, token: str, required_role: str) -> bool:
        """
        Check if token has required role.

        Args:
            token: JWT token
            required_role: Role to check

        Returns:
            True if role granted
        """
        claims = self.validate_token(token)
        if not claims:
            return False

        return required_role in claims.roles or "admin" in claims.roles


# Default instance for module-level access
_default_manager: Optional[TokenManager] = None


def get_token_manager() -> TokenManager:
    """Get or create the default TokenManager instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = TokenManager()
    return _default_manager


def validate_token(token: str) -> Optional[TokenClaims]:
    """Validate a token using the default manager."""
    return get_token_manager().validate_token(token)
