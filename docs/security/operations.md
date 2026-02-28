# Security Operations (RS256, Rotation, Audit, AuthZ)

## 1) Key storage (file mount)
- Store JWT private keys in `secrets/jwt_private_keys.json`.
- Store auth users in `secrets/auth_users.json`.
- Mount `./secrets` read-only to `/run/secrets` in Docker Compose.
- Configure:
  - `JWT_PRIVATE_KEYS_FILE=/run/secrets/jwt_private_keys.json`
  - `JWT_PUBLIC_KEYS_FILE=/run/secrets/jwt_public_keys.json` (optional)
  - `AUTH_USERS_FILE=/run/secrets/auth_users.json`

## 2) Rotation runbook (fixed procedure)
1. Create new RSA key pair with new `kid`.
2. Add new key to `jwt_private_keys.json` and `jwt_public_keys.json` while keeping old key.
3. Set `JWT_ACTIVE_KID` to new `kid` and deploy.
4. Keep both keys for at least refresh TTL (`JWT_REFRESH_TOKEN_EXPIRES_DAYS`).
5. Remove old key and deploy again.

## 3) Security audit visibility
Structured JSON security events are emitted to logger `security.audit`.
Key events:
- `login_failed`, `login_succeeded`, `login_rate_limited`
- `token_issued`, `token_missing`, `token_validation_failed`
- `refresh_token_rotated`, `refresh_token_reuse`, `refresh_token_inactive`
- `authorization_denied`

Forward these logs to your central logging/SIEM pipeline.

## 3.1) Cookie session transport (recommended)
- Access/refresh tokens are set as HttpOnly cookies by `/auth/login` and `/auth/refresh`.
- Recommended defaults:
  - `AUTH_COOKIE_SECURE=true`
  - `AUTH_COOKIE_SAMESITE=lax` (or `strict` for tighter isolation)
  - `AUTH_ACCESS_COOKIE_NAME=access_token`
  - `AUTH_REFRESH_COOKIE_NAME=refresh_token`

## 3.2) CSRF + DPoP hardening
- CSRF:
  - `AUTH_REQUIRE_CSRF=true`
  - Send `X-CSRF-Token` header equal to `csrf_token` cookie for state-changing requests.
  - For cross-origin embedded clients (e.g. Figma), use the `csrf_token` returned by `/auth/login` or `/auth/refresh` when cookie reads are restricted.
  - Validate request `Origin` against `AUTH_ALLOWED_ORIGINS` (or same-host fallback).
  - Optional regex allowlist: `AUTH_ALLOWED_ORIGIN_REGEX`.
- DPoP:
  - `AUTH_REQUIRE_DPOP=true`
  - Require `DPoP` proof JWT (`ES256`) on login, refresh, logout, and protected API calls.
  - Access/refresh tokens are sender-constrained with `cnf.jkt`.

## 4) Scope/Tenant/Resource policy
- JWT claim `tenant` is required.
- JWT claim `scope` is enforced (space-delimited scopes).
- Default conversation policies:
  - `conversation:list` => `conversation:read`
  - `conversation:get` => `conversation:read`
  - `conversation:create` => `conversation:write`

Override policy with `AUTHZ_POLICIES_JSON`.
