from typing import Optional

from pydantic import BaseModel, Field


class LoginRequestSchema(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RefreshTokenRequestSchema(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=1)


class AuthSessionResponseSchema(BaseModel):
    token_type: str
    access_expires_in: int
    refresh_expires_in: int
