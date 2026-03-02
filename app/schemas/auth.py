from typing import Optional

from pydantic import BaseModel, Field


class LoginRequestSchema(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RegisterRequestSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8, max_length=256)
    email: str = Field(..., min_length=3, max_length=254)


class RegisterResponseSchema(BaseModel):
    message: str
    verification_required: bool


class VerifyEmailRequestSchema(BaseModel):
    token: str = Field(..., min_length=1, max_length=2048)


class VerifyEmailResponseSchema(BaseModel):
    message: str


class ResendVerificationRequestSchema(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class ResendVerificationResponseSchema(BaseModel):
    message: str


class RefreshTokenRequestSchema(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=1)


class AuthSessionResponseSchema(BaseModel):
    token_type: str
    access_expires_in: int
    refresh_expires_in: int
    csrf_token: str


class AuthMeResponseSchema(BaseModel):
    sub: str
    role: Optional[str] = None
    tenant: str
    scope: str
    iss: Optional[str] = None
    aud: Optional[str] = None
    typ: Optional[str] = None
    exp: int
    name: str = ""
    email: Optional[str] = None
    email_verified: bool = False
    profile_image_data_url: Optional[str] = None


class AuthProfileUpdateRequestSchema(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    profile_image_data_url: Optional[str] = Field(default=None, min_length=1)
    clear_profile_image: bool = Field(default=False)
