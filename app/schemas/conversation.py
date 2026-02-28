from typing import List

from pydantic import BaseModel, Field


class MessageCreateSchema(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class MessageSchema(BaseModel):
    message_id: str
    message: str
    created_at: str


class ConversationSummarySchema(BaseModel):
    conversation_id: str
    message_count: int
    updated_at: str


class ConversationDetailSchema(BaseModel):
    conversation_id: str
    user_id: str
    messages: List[MessageSchema]
    updated_at: str
