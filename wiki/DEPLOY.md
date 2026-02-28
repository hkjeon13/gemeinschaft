# Deployment Guide (Docker Compose)

This document describes production-style deployment for this project with:
- `frontend` (static UI + `/api` reverse proxy, port `10015`)
- `app` (FastAPI)
- `postgres` (separate Docker service)
- RS256 JWT keys and auth user data mounted from `./secrets`

## 0) From Zero (copy/paste)

Run this sequence on a fresh host:

```bash
# 0) project
git clone <repo-url> gemeinschaft
cd gemeinschaft
cp .env.example .env
mkdir -p secrets

# 1) JWT keys (RS256)
python app/scripts/generate_rsa_jwt_keys.py \
  --kid k2026_02 \
  --private-out secrets/jwt_private_keys.json \
  --public-out secrets/jwt_public_keys.json

# 2) bcrypt hash (copy output)
python - <<'PY'
import bcrypt
print(bcrypt.hashpw(b'psyche-pass', bcrypt.gensalt()).decode())
PY

# 3) auth user file (paste real hash from step 2)
cat > secrets/auth_users.json <<'JSON'
{
  "psyche": {
    "password_hash": "$2b$12$REPLACE_WITH_REAL_BCRYPT_HASH",
    "role": "admin",
    "tenant": "default",
    "scopes": ["conversation:read", "conversation:write"]
  }
}
JSON

# 4) set env values
sed -i 's/^JWT_ACTIVE_KID=.*/JWT_ACTIVE_KID=k2026_02/' .env
grep -q '^AUTH_USERS_FILE=' .env || echo 'AUTH_USERS_FILE=/run/secrets/auth_users.json' >> .env
grep -q '^POSTGRES_PASSWORD=' .env || echo 'POSTGRES_PASSWORD=change-me-now' >> .env

# 5) validate + deploy
docker compose config
docker compose up -d --build
docker compose ps
```

Then verify:

```bash
curl -s http://localhost:10015/api/auth/.well-known/jwks.json
```

Open browser and verify via UI:
- `http://localhost:10015/` (console login)
- `http://localhost:10015/admin` (admin dashboard)

## 1) Prerequisites

- Docker Engine + Docker Compose v2
- Ports available:
  - `10015` for frontend (or your custom `FRONTEND_PORT`)
  - `8000` for app (or your custom `APP_PORT`)
  - `5432` for postgres (or your custom `POSTGRES_PORT`)
- Local files:
  - `.env`
  - `secrets/jwt_private_keys.json`
  - `secrets/jwt_public_keys.json`
  - `secrets/auth_users.json`

## 2) Prepare env file

```bash
cp .env.example .env
```

Edit `.env` and set at least:
- `POSTGRES_PASSWORD` (strong value)
- `JWT_ACTIVE_KID` (must exist in `secrets/jwt_private_keys.json`)
- `AUTH_USERS_FILE=/run/secrets/auth_users.json`
- `AUTH_COOKIE_SECURE=true` (production HTTPS). For local HTTP testing only, set `AUTH_COOKIE_SECURE=false`.
- `AUTH_REQUIRE_CSRF=true`
- `AUTH_REQUIRE_DPOP=true`
- `AUTH_ALLOWED_ORIGINS=https://dataset.fin-ally.net`

Optional but recommended:
- `JWT_ISSUER`, `JWT_AUDIENCE`
- `AUTH_LOGIN_MAX_ATTEMPTS`, `AUTH_LOGIN_BLOCK_SECONDS`
- `AUTHZ_POLICIES_JSON` (if you want custom authorization policy)

## 3) Prepare secrets

### 3.1 Generate JWT RSA key files

```bash
python app/scripts/generate_rsa_jwt_keys.py \
  --kid k2026_02 \
  --private-out secrets/jwt_private_keys.json \
  --public-out secrets/jwt_public_keys.json
```

This creates JSON like:
- `secrets/jwt_private_keys.json`: `{ "k2026_02": "-----BEGIN PRIVATE KEY-----..." }`
- `secrets/jwt_public_keys.json`: `{ "k2026_02": "-----BEGIN PUBLIC KEY-----..." }`

### 3.2 Create auth users file

Generate bcrypt hash example:

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'psyche-pass', bcrypt.gensalt()).decode())"
```

Create `secrets/auth_users.json`:

```json
{
  "psyche": {
    "password_hash": "$2b$12$replace_with_real_hash",
    "role": "admin",
    "tenant": "default",
    "scopes": ["conversation:read", "conversation:write"]
  }
}
```

## 4) Preflight validation

```bash
docker compose config
```

If this fails, fix `.env` or missing files before continuing.

## 5) Deploy

```bash
docker compose up -d --build
```

Check status:

```bash
docker compose ps
```

## 6) Post-deploy verification

### 6.1 Health/basic API checks

```bash
curl -s http://localhost:10015/api/auth/.well-known/jwks.json
```

### 6.2 Login and JWT check

Because CSRF + DPoP are enabled by default, browser UI is the recommended test path:
- `http://localhost:10015/`
- `http://localhost:10015/admin`

If you need raw curl checks temporarily, disable DPoP in `.env` for that session:
- `AUTH_REQUIRE_DPOP=false`
- then redeploy and run curl tests.

### 6.3 Authorization check (scope enforced)

Conversation endpoints require valid JWT and correct scope:
- `GET /api/conversation/list` -> needs `conversation:read`
- `GET /api/conversation/{id}` -> needs `conversation:read`
- `POST /api/conversation/{id}` -> needs `conversation:write`

### 6.4 Admin check (role enforced)

Admin user management endpoints require `role=admin`:
- `GET /api/admin/users`
- `POST /api/admin/users`
- `PATCH /api/admin/users/{username}`
- `DELETE /api/admin/users/{username}`

Admin UI is available at:
- `https://dataset.fin-ally.net/admin`

## 7) Edge Nginx integration (`dataset.fin-ally.net`)

Your edge Nginx can keep routing all traffic to `127.0.0.1:10015`:
- `/` serves frontend
- `/api/*` is forwarded by frontend container to the `app` service

Example edge snippet:

```nginx
location / {
    proxy_pass http://127.0.0.1:10015/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_redirect off;
}
```

## 8) Logs and audit events

Follow logs:

```bash
docker compose logs -f app
```

Security events are written to logger `security.audit` as JSON, including:
- `login_failed`, `login_succeeded`, `login_rate_limited`
- `token_issued`, `token_missing`, `token_validation_failed`
- `refresh_token_rotated`, `refresh_token_reuse`, `refresh_token_inactive`
- `authorization_denied`

Forward these logs to your central logging/SIEM in production.

## 9) Rolling update

When app code or env changes:

```bash
docker compose up -d --build
```

## 10) JWT key rotation (no downtime pattern)

1. Generate new key pair with new `kid`.
2. Add new key to both key JSON files, keep old key too.
3. Set `.env` `JWT_ACTIVE_KID` to new `kid`.
4. Redeploy: `docker compose up -d --build`.
5. Keep old key for at least `JWT_REFRESH_TOKEN_EXPIRES_DAYS`.
6. Remove old key and redeploy again.

## 11) Rollback

If deploy fails after key switch:
1. Restore previous `.env` and key files (including previous `JWT_ACTIVE_KID`).
2. Redeploy:

```bash
docker compose up -d --build
```

3. Verify `/auth/me` and `/conversation/*` authorization paths.

## 12) Troubleshooting

### 12.1 `version is obsolete` warning

### 12.2 Figma E2E (cross-origin embed)

For Figma-hosted E2E, browser requests are cross-origin. Use these settings:

- `AUTH_COOKIE_SAMESITE=none`
- `AUTH_COOKIE_SECURE=true`
- `AUTH_ALLOWED_ORIGINS=*` (temporary non-production)
- `AUTH_ALLOWED_ORIGIN_REGEX=^https://([a-z0-9-]+\.)?figma\.(site|com)$`

Client notes:

- Send requests with credentials `include`.
- Keep DPoP keypair stable across page reloads.
- Use `csrf_token` returned by `/auth/login` and `/auth/refresh` for state-changing requests if cookie read is restricted.

If you see:
- `the attribute version is obsolete`

It is a Docker Compose v2 warning only. Deployment still works.  
You can remove top-level `version: "3.9"` from `docker-compose.yml` to silence it.

### 12.3 `AUTH user 'psyche' has invalid bcrypt hash`

Cause:
- `secrets/auth_users.json` contains a non-bcrypt value (example placeholder or broken string).

Fix:
1. Regenerate bcrypt hash.
2. Replace `password_hash` with the full generated value.
3. Restart app:

```bash
docker compose up -d --build app
docker compose logs -f app
```

### 12.4 JWT key parse/validation errors

Cause:
- `secrets/jwt_private_keys.json` or `secrets/jwt_public_keys.json` still contains template `...` values.
- `JWT_ACTIVE_KID` does not match a key id in `jwt_private_keys.json`.

Fix:
1. Regenerate keys with `generate_rsa_jwt_keys.py`.
2. Set `.env` `JWT_ACTIVE_KID` to an existing kid.
3. Redeploy.
