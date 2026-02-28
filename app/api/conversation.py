from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.conversation import (
    ConversationDetailSchema,
    ConversationSummarySchema,
    MessageCreateSchema,
)
from app.services.authorization import AccessContext, authorize_action, require_access_context
from app.services.conversation_store import conversation_store

conversation_router = APIRouter()


@conversation_router.get("/", response_model=List[ConversationSummarySchema])
async def conversation_list(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:list")
    return conversation_store.list_conversations(tenant_id=access.tenant, user_id=access.subject)


@conversation_router.get("/{conversation_id}", response_model=ConversationDetailSchema)
async def get_dialogue(conversation_id: str, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:get", resource_id=conversation_id)
    conversation = conversation_store.get_conversation(
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return conversation


@conversation_router.post("/{conversation_id}", response_model=ConversationDetailSchema)
async def create_dialogue(
    conversation_id: str,
    payload: MessageCreateSchema,
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="conversation:create", resource_id=conversation_id)
    return conversation_store.append_message(
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
        message=payload.message,
    )
