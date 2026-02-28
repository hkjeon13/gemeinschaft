from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class MessageContentInputSchema(BaseModel):
    type: Literal["text"] = "text"
    text: str = Field(..., min_length=1)


class MessageSpeakerInputSchema(BaseModel):
    type: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None


class MessageInputSchema(BaseModel):
    id: Optional[str] = None
    role: Literal["user", "assistant", "system"] = "user"
    speaker: Optional[MessageSpeakerInputSchema] = None
    content: List[MessageContentInputSchema] = Field(default_factory=list)
    timestamp: Optional[str] = None


class MessageCreateSchema(BaseModel):
    message: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    messages: Optional[List[MessageInputSchema]] = None
    model_id: Optional[str] = Field(default=None, min_length=1)


class MessageSchema(BaseModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    message: str
    created_at: str
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    model_display_name: Optional[str] = None
    provider: Optional[str] = None


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
