# Authentication Flow Documentation

## Overview

This document describes the authentication flow implemented in Sirchmunk.

## Authentication Methods

### 1. API Key Authentication

The primary authentication method for API access.

```yaml
type: api_key
header: X-API-Key
required: true
```

### 2. OAuth 2.0 Flow

For user-facing applications requiring delegated access.

#### Authorization Code Flow

1. Redirect user to authorization endpoint
2. User grants permission
3. Receive authorization code
4. Exchange code for access token
5. Use access token for API requests

```
GET /oauth/authorize?client_id=XXX&redirect_uri=YYY&response_type=code
```

### 3. JWT Token Authentication

For service-to-service communication.

```json
{
  "alg": "RS256",
  "typ": "JWT"
}
```

## Token Management

### Refresh Token Flow

When access token expires (typically after 1 hour):

1. Send refresh token to token endpoint
2. Receive new access token
3. Update stored credentials
4. Retry original request

### Token Storage

- **Client-side**: Secure HTTP-only cookies or encrypted localStorage
- **Server-side**: Redis cache with TTL matching token expiration

## Security Considerations

1. Never log full tokens
2. Use HTTPS for all token transmissions
3. Implement rate limiting on auth endpoints
4. Support token revocation
5. Rotate signing keys regularly

## Error Codes

| Code | Description |
|------|-------------|
| 401  | Unauthorized - Invalid or missing token |
| 403  | Forbidden - Token valid but insufficient permissions |
| 40101| Token expired |
| 40102| Invalid token format |
| 40103| Token revoked |

## Implementation

See `src/auth/token_manager.py` for the core implementation.
