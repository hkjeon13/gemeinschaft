from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class MessageContentInputSchema(BaseModel):
    type: Literal["text", "input_text", "output_text", "image_url", "input_image", "output_image"]
    text: Optional[str] = Field(default=None, min_length=1)
    image_url: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_payload(self):
        block_type = self.type
        text = self.text
        image_url = self.image_url

        if block_type in {"text", "input_text", "output_text"}:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("text is required for text content blocks.")
        elif block_type in {"image_url", "input_image", "output_image"}:
            if not isinstance(image_url, str) or not image_url.strip():
                raise ValueError("image_url is required for image content blocks.")
        return self


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
    content: List[MessageContentInputSchema] = Field(default_factory=list)
    created_at: str
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    model_display_name: Optional[str] = None
    provider: Optional[str] = None


class ConversationSummarySchema(BaseModel):
    conversation_id: str
    title: str
    message_count: int
    updated_at: str
    has_unread: bool = False


class ConversationVisibilitySchema(BaseModel):
    conversation_id: str
    visible: bool


class ConversationDetailSchema(BaseModel):
    conversation_id: str
    tenant_id: str
    user_id: str
    title: str
    messages: List[MessageSchema]
    updated_at: str


class ConversationTitleUpdateSchema(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class ConversationTitleSchema(BaseModel):
    conversation_id: str
    title: str


class ConversationModelOptionSchema(BaseModel):
    model_id: str
    provider: str
    openai_api: str
    model: str
    display_name: str
    description: str
    is_global_default: bool
    is_user_default: bool


class UserDefaultModelSchema(BaseModel):
    model_id: str
    display_name: str
    source: Literal["user", "global"]


class UserDefaultModelUpdateSchema(BaseModel):
    model_id: str = Field(..., min_length=1)


class ConversationAssignedModelSchema(BaseModel):
    model_id: str
    provider: str
    openai_api: str
    model: str
    display_name: str
    description: str


class ConversationAssignedModelListSchema(BaseModel):
    conversation_id: str
    models: List[ConversationAssignedModelSchema]


class ConversationAssignedModelUpdateSchema(BaseModel):
    model_id: str = Field(..., min_length=1)
