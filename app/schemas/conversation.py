from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class MessageCreateSchema(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    model_id: Optional[str] = Field(default=None, min_length=1)


class MessageSchema(BaseModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    message: str
    created_at: str


class ConversationSummarySchema(BaseModel):
    conversation_id: str
    message_count: int
    updated_at: str


class ConversationDetailSchema(BaseModel):
    conversation_id: str
    tenant_id: str
    user_id: str
    messages: List[MessageSchema]
    updated_at: str
