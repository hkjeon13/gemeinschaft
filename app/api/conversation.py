from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.conversation import (
    ConversationDetailSchema,
    ConversationSummarySchema,
    MessageCreateSchema,
)
from app.services.auth import require_access_subject
from app.services.conversation_store import conversation_store

conversation_router = APIRouter()


@conversation_router.get("/", response_model=List[ConversationSummarySchema])
async def conversation_list(subject: str = Depends(require_access_subject)):
    return conversation_store.list_conversations(user_id=subject)


@conversation_router.get("/{conversation_id}", response_model=ConversationDetailSchema)
async def get_dialogue(conversation_id: str, subject: str = Depends(require_access_subject)):
    conversation = conversation_store.get_conversation(user_id=subject, conversation_id=conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return conversation


@conversation_router.post("/{conversation_id}", response_model=ConversationDetailSchema)
async def create_dialogue(
    conversation_id: str,
    payload: MessageCreateSchema,
    subject: str = Depends(require_access_subject),
):
    return conversation_store.append_message(
        user_id=subject,
        conversation_id=conversation_id,
        message=payload.message,
    )
