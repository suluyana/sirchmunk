# Authentication Middleware
# Handles token validation and user authentication

import logging
from typing import Optional, Callable
from functools import wraps

from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sirchmunk.auth.token_manager import TokenManager, TokenClaims

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


class AuthMiddleware:
    """
    Middleware for handling authentication.

    Features:
    - Token validation from Authorization header
    - User claims extraction
    - Role-based access control
    - Permission checking
    """

    def __init__(self, token_manager: Optional[TokenManager] = None):
        """
        Initialize AuthMiddleware.

        Args:
            token_manager: TokenManager instance for validation
        """
        self.token_manager = token_manager or TokenManager()

    async def __call__(
        self,
        request: Request,
        call_next: Callable
    ):
        """
        Process request with authentication.

        Args:
            request: FastAPI request object
            call_next: Next middleware/handler

        Returns:
            Response from next handler
        """
        # Skip auth for public endpoints
        if self._is_public_endpoint(request.url.path):
            return await call_next(request)

        # Extract and validate token
        try:
            credentials = await security(request)
            if not credentials:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing authentication credentials"
                )

            claims = self.token_manager.validate_token(credentials.credentials)
            if not claims:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired token"
                )

            # Attach claims to request state
            request.state.user_claims = claims
            request.state.user_id = claims.user_id

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service unavailable"
            )

        return await call_next(request)

    def _is_public_endpoint(self, path: str) -> bool:
        """
        Check if endpoint is public (no auth required).

        Args:
            path: Request path

        Returns:
            True if public endpoint
        """
        public_paths = [
            "/health",
            "/docs",
            "/openapi.json",
            "/api/v1/auth/login",
            "/api/v1/auth/register",
        ]
        return any(path.startswith(p) for p in public_paths)


def require_role(role: str):
    """
    Decorator to require specific role.

    Args:
        role: Required role name

    Returns:
        Decorator function
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            claims: Optional[TokenClaims] = getattr(request.state, "user_claims", None)

            if not claims:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            if role not in claims.roles and "admin" not in claims.roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Role '{role}' required"
                )

            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_permission(permission: str):
    """
    Decorator to require specific permission.

    Args:
        permission: Required permission name

    Returns:
        Decorator function
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            claims: Optional[TokenClaims] = getattr(request.state, "user_claims", None)

            if not claims:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            if not claims.permissions or (
                permission not in claims.permissions and "admin" not in claims.roles
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission '{permission}' required"
                )

            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


def get_current_user(request: Request) -> Optional[TokenClaims]:
    """
    Get current user claims from request.

    Args:
        request: FastAPI request

    Returns:
        TokenClaims if authenticated, None otherwise
    """
    return getattr(request.state, "user_claims", None)


def get_current_user_id(request: Request) -> Optional[str]:
    """
    Get current user ID from request.

    Args:
        request: FastAPI request

    Returns:
        User ID if authenticated, None otherwise
    """
    claims = get_current_user(request)
    return claims.user_id if claims else None
