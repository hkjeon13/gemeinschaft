from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.schemas.auth import (
    LoginRequestSchema,
    RefreshTokenRequestSchema,
    TokenPairResponseSchema,
)
from app.services.auth import (
    JwtContext,
    authenticate_user,
    create_token_pair,
    ensure_login_not_rate_limited,
    get_jwks_document,
    login_rate_limit_key,
    register_login_failure,
    register_login_success,
    rotate_token_pair_from_refresh_token,
    require_jwt,
)

auth_router = APIRouter()


@auth_router.post("/login", response_model=TokenPairResponseSchema)
async def login(payload: LoginRequestSchema, request: Request):
    rate_key = login_rate_limit_key(request=request, username=payload.username)
    ensure_login_not_rate_limited(rate_key)

    user = authenticate_user(payload.username, payload.password)
    if not user:
        register_login_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    register_login_success(rate_key)
    return TokenPairResponseSchema(
        **create_token_pair(
            subject=user.username,
            role=user.role,
            tenant=user.tenant,
            scopes=user.scopes,
        )
    )


@auth_router.post("/refresh", response_model=TokenPairResponseSchema)
async def refresh(payload: RefreshTokenRequestSchema):
    return TokenPairResponseSchema(**rotate_token_pair_from_refresh_token(payload.refresh_token))


@auth_router.get("/.well-known/jwks.json")
async def jwks():
    return get_jwks_document()


@auth_router.get("/me")
async def me(jwt_ctx: JwtContext = Depends(require_jwt)):
    claims = jwt_ctx.claims
    return {
        "sub": claims.get("sub"),
        "role": claims.get("role"),
        "tenant": claims.get("tenant"),
        "scope": claims.get("scope"),
        "iss": claims.get("iss"),
        "aud": claims.get("aud"),
        "typ": claims.get("typ"),
        "exp": claims.get("exp"),
    }
