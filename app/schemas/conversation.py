from pydantic import BaseModel


class ConversationListSchema(BaseModel):
    pass


class DialogueSchema(BaseModel):
    conversation_id: str
