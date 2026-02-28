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

## 4) Scope/Tenant/Resource policy
- JWT claim `tenant` is required.
- JWT claim `scope` is enforced (space-delimited scopes).
- Default conversation policies:
  - `conversation:list` => `conversation:read`
  - `conversation:get` => `conversation:read`
  - `conversation:create` => `conversation:write`

Override policy with `AUTHZ_POLICIES_JSON`.
