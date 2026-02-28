from typing import Any, Dict, List, Optional

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


class AdminChatModelSchema(BaseModel):
    model_id: str
    provider: str
    client_type: str
    model: str
    display_name: str
    description: str
    parameters: Dict[str, Any]
    has_api_key: bool
    is_active: bool
    is_default: bool
    created_at: str
    updated_at: str


class AdminChatModelCreateSchema(BaseModel):
    model_id: str = Field(..., min_length=1)
    provider: str = Field(default="openai", min_length=1)
    client_type: str = Field(default="openai", min_length=1)
    model: str = Field(..., min_length=1)
    display_name: Optional[str] = Field(default=None, min_length=1)
    description: str = Field(default="")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    api_key: Optional[str] = Field(default=None, min_length=1)
    is_active: bool = Field(default=True)
    is_default: bool = Field(default=False)


class AdminChatModelUpdateSchema(BaseModel):
    provider: Optional[str] = Field(default=None, min_length=1)
    client_type: Optional[str] = Field(default=None, min_length=1)
    model: Optional[str] = Field(default=None, min_length=1)
    display_name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    api_key: Optional[str] = Field(default=None, min_length=1)
    clear_api_key: Optional[bool] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
