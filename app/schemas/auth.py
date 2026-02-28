from pydantic import BaseModel, Field


class LoginRequestSchema(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class RefreshTokenRequestSchema(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class TokenPairResponseSchema(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    access_expires_in: int
    refresh_expires_in: int
