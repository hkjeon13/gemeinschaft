from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from app.schemas.auth import (
    AuthSessionResponseSchema,
    LoginRequestSchema,
    RegisterRequestSchema,
    RegisterResponseSchema,
    ResendVerificationRequestSchema,
    ResendVerificationResponseSchema,
    RefreshTokenRequestSchema,
    VerifyEmailRequestSchema,
    VerifyEmailResponseSchema,
)
from app.services.auth import (
    JwtContext,
    attach_auth_cookies,
    authenticate_user,
    clear_auth_cookies,
    create_token_pair,
    decode_and_validate_jwt,
    dpop_jkt_from_claims,
    ensure_user_can_login,
    ensure_login_not_rate_limited,
    get_jwks_document,
    invalidate_refresh_token,
    login_rate_limit_key,
    register_auth_user,
    refresh_cookie_name,
    register_login_failure,
    register_login_success,
    resend_verification_email,
    rotate_token_pair_from_refresh_token,
    require_jwt,
    verify_email_token,
)
from app.services.request_security import enforce_origin_for_state_change, validate_dpop_proof

auth_router = APIRouter()


def _session_response(token_pair: dict, csrf_token: str) -> AuthSessionResponseSchema:
    return AuthSessionResponseSchema(
        token_type=token_pair["token_type"],
        access_expires_in=token_pair["access_expires_in"],
        refresh_expires_in=token_pair["refresh_expires_in"],
        csrf_token=csrf_token,
    )


@auth_router.post("/login", response_model=AuthSessionResponseSchema)
async def login(payload: LoginRequestSchema, request: Request, response: Response):
    enforce_origin_for_state_change(request)
    rate_key = login_rate_limit_key(request=request, username=payload.username)
    ensure_login_not_rate_limited(rate_key)

    user = authenticate_user(payload.username, payload.password)
    if not user:
        register_login_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    ensure_user_can_login(user)

    register_login_success(rate_key)
    token_pair = create_token_pair(
        subject=user.username,
        role=user.role,
        tenant=user.tenant,
        scopes=user.scopes,
        dpop_jkt=validate_dpop_proof(request),
    )
    csrf_token = attach_auth_cookies(response, token_pair)
    return _session_response(token_pair, csrf_token)


@auth_router.post("/register", response_model=RegisterResponseSchema, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequestSchema, request: Request):
    enforce_origin_for_state_change(request)
    user = register_auth_user(
        name=payload.name,
        username=payload.username,
        password=payload.password,
        email=payload.email,
        request=request,
    )
    if user.email_verified:
        return RegisterResponseSchema(
            message="회원가입이 완료되었습니다. 바로 로그인할 수 있습니다.",
            verification_required=False,
        )
    return RegisterResponseSchema(
        message="회원가입이 완료되었습니다. 이메일 인증 링크를 확인해주세요.",
        verification_required=True,
    )


@auth_router.post("/verify-email", response_model=VerifyEmailResponseSchema)
async def verify_email(payload: VerifyEmailRequestSchema):
    verify_email_token(payload.token)
    return VerifyEmailResponseSchema(message="이메일 인증이 완료되었습니다. 로그인할 수 있습니다.")


@auth_router.get("/verify-email", response_class=HTMLResponse, name="verify_email_get")
async def verify_email_get(token: str):
    try:
        verify_email_token(token)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "이메일 인증에 실패했습니다."
        html = f"""
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>이메일 인증 실패</title>
  </head>
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px;">
    <h1 style="font-size: 22px; margin: 0 0 8px 0;">이메일 인증 실패</h1>
    <p style="color: #374151;">{detail}</p>
    <p style="color: #6b7280;">다시 회원가입하거나 인증 메일 재전송을 요청해 주세요.</p>
  </body>
</html>
"""
        return HTMLResponse(content=html, status_code=exc.status_code)

    html = """
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>이메일 인증 완료</title>
  </head>
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px;">
    <h1 style="font-size: 22px; margin: 0 0 8px 0;">이메일 인증 완료</h1>
    <p style="color: #374151;">이제 로그인해서 서비스를 이용할 수 있습니다.</p>
  </body>
</html>
"""
    return HTMLResponse(content=html, status_code=status.HTTP_200_OK)


@auth_router.post("/resend-verification", response_model=ResendVerificationResponseSchema)
async def resend_verification(payload: ResendVerificationRequestSchema, request: Request):
    enforce_origin_for_state_change(request)
    resend_verification_email(email=payload.email, request=request)
    return ResendVerificationResponseSchema(
        message="가입된 이메일이라면 인증 메일을 다시 전송했습니다.",
    )


@auth_router.post("/refresh", response_model=AuthSessionResponseSchema)
async def refresh(
    request: Request,
    response: Response,
    payload: Optional[RefreshTokenRequestSchema] = None,
):
    enforce_origin_for_state_change(request)
    refresh_token = payload.refresh_token if payload and payload.refresh_token else request.cookies.get(refresh_cookie_name())
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    refresh_claims = decode_and_validate_jwt(token=refresh_token, expected_token_type="refresh")
    validate_dpop_proof(request, expected_jkt=dpop_jkt_from_claims(refresh_claims))

    token_pair = rotate_token_pair_from_refresh_token(refresh_token)
    csrf_token = attach_auth_cookies(response, token_pair)
    return _session_response(token_pair, csrf_token)


@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    enforce_origin_for_state_change(request)
    refresh_token = request.cookies.get(refresh_cookie_name())
    if refresh_token:
        try:
            refresh_claims = decode_and_validate_jwt(token=refresh_token, expected_token_type="refresh")
            validate_dpop_proof(request, expected_jkt=dpop_jkt_from_claims(refresh_claims))
            invalidate_refresh_token(refresh_token)
        except HTTPException:
            pass

    clear_auth_cookies(response)
    return {"ok": True}


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
