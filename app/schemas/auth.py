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
