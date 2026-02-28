from typing import List, Optional

from pydantic import BaseModel, Field


class AdminUserSchema(BaseModel):
    username: str
    role: str
    tenant: str
    scopes: List[str]


class AdminUserCreateSchema(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    role: str = Field(default="user", min_length=1)
    tenant: str = Field(default="default", min_length=1)
    scopes: List[str] = Field(default_factory=list)


class AdminUserUpdateSchema(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8)
    role: Optional[str] = Field(default=None, min_length=1)
    tenant: Optional[str] = Field(default=None, min_length=1)
    scopes: Optional[List[str]] = None
