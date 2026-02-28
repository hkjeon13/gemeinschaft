import json
from typing import Any, AsyncIterator, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.schemas.conversation import (
    ConversationDetailSchema,
    ConversationSummarySchema,
    MessageCreateSchema,
    MessageInputSchema,
)
from app.services.async_openai_chat_model import AsyncOpenAIChatModel
from app.services.authorization import AccessContext, authorize_action, require_access_context
from app.services.chat_model_registry import ResolvedChatModel, resolve_chat_model
from app.services.conversation_store import conversation_store

conversation_router = APIRouter()


def _chat_model(selected: ResolvedChatModel) -> AsyncOpenAIChatModel:
    temperature: float = 0.7
    max_tokens: int | None = None
    extra_options: dict[str, Any] = {}

    raw_temperature = selected.parameters.get("temperature")
    if raw_temperature is not None:
        if not isinstance(raw_temperature, (int, float)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Model parameter 'temperature' must be a number.")
        temperature = float(raw_temperature)

    raw_max_tokens = selected.parameters.get("max_tokens")
    if raw_max_tokens is not None:
        if not isinstance(raw_max_tokens, int):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Model parameter 'max_tokens' must be an integer.")
        max_tokens = raw_max_tokens

    for key, value in selected.parameters.items():
        if key in {"temperature", "max_tokens"}:
            continue
        extra_options[key] = value

    try:
        return AsyncOpenAIChatModel(
            model=selected.model,
            api_key=selected.api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            request_options=extra_options,
            client_options=selected.client_options,
            openai_api=selected.openai_api,
            chat_create_options=selected.chat_create_options,
            responses_create_options=selected.responses_create_options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


def _conversation_to_openai_messages(conversation: dict[str, Any], max_messages: int = 20) -> list[dict[str, str]]:
    messages = conversation.get("messages", [])
    if not isinstance(messages, list):
        return []

    recent = messages[-max_messages:]
    converted: list[dict[str, str]] = []
    for item in recent:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "user")).strip().lower()
        content = str(item.get("message", "")).strip()
        if role not in {"user", "assistant", "system"}:
            role = "user"
        if not content:
            continue
        converted.append({"role": role, "content": content})
    return converted


def _message_input_to_text(item: MessageInputSchema) -> str:
    pieces: list[str] = []
    for part in item.content:
        if part.type != "text":
            continue
        text = part.text.strip()
        if text:
            pieces.append(text)
    return "\n".join(pieces).strip()


def _resolve_user_input(payload: MessageCreateSchema) -> str:
    if payload.messages:
        for item in reversed(payload.messages):
            if item.role != "user":
                continue
            text = _message_input_to_text(item)
            if text:
                return text

        for item in reversed(payload.messages):
            text = _message_input_to_text(item)
            if text:
                return text

    if payload.message:
        text = payload.message.strip()
        if text:
            return text

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="A non-empty user text is required in `messages[].content[].text` or `message`.",
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_assistant_reply(
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    model_id: str,
    model_name: str,
    model_display_name: str,
    provider: str,
    chat_model: AsyncOpenAIChatModel,
    messages: list[dict[str, str]],
) -> AsyncIterator[str]:
    chunks: list[str] = []
    try:
        async for delta in chat_model.stream_messages(messages):
            chunks.append(delta)
            yield _sse("delta", {"text": delta})
    except Exception as exc:
        yield _sse("error", {"detail": str(exc)})
        return

    full_text = "".join(chunks).strip()
    if full_text:
        await run_in_threadpool(
            conversation_store.append_message,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            message=full_text,
            role="assistant",
            model_id=model_id,
            model_name=model_name,
            model_display_name=model_display_name,
            provider=provider,
        )
    yield _sse(
        "done",
        {
            "conversation_id": conversation_id,
            "model_id": model_id,
            "model_name": model_name,
            "model_display_name": model_display_name,
            "provider": provider,
        },
    )


@conversation_router.get("/list", response_model=List[ConversationSummarySchema])
async def conversation_list(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:list")
    return await run_in_threadpool(
        conversation_store.list_conversations,
        tenant_id=access.tenant,
        user_id=access.subject,
    )


@conversation_router.get("/{conversation_id}", response_model=ConversationDetailSchema)
async def get_dialogue(conversation_id: str, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:get", resource_id=conversation_id)
    conversation = await run_in_threadpool(
        conversation_store.get_conversation,
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
    stream: bool = Query(default=False),
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="conversation:create", resource_id=conversation_id)
    user_message = _resolve_user_input(payload)
    conversation = await run_in_threadpool(
        conversation_store.append_message,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
        message=user_message,
        role="user",
    )
    selected_model = await run_in_threadpool(resolve_chat_model, payload.model_id)
    if selected_model.provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {selected_model.provider}",
        )
    model_client = _chat_model(selected_model)

    messages = _conversation_to_openai_messages(conversation)
    if not messages:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to build chat messages.")

    if stream:
        return StreamingResponse(
            _stream_assistant_reply(
                tenant_id=access.tenant,
                user_id=access.subject,
                conversation_id=conversation_id,
                model_id=selected_model.model_id,
                model_name=selected_model.model,
                model_display_name=selected_model.display_name,
                provider=selected_model.provider,
                chat_model=model_client,
                messages=messages,
            ),
            media_type="text/event-stream",
        )

    try:
        assistant_reply = await model_client.generate_messages(messages)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate assistant response: {exc}",
        )

    if assistant_reply.strip():
        conversation = await run_in_threadpool(
            conversation_store.append_message,
            tenant_id=access.tenant,
            user_id=access.subject,
            conversation_id=conversation_id,
            message=assistant_reply,
            role="assistant",
            model_id=selected_model.model_id,
            model_name=selected_model.model,
            model_display_name=selected_model.display_name,
            provider=selected_model.provider,
        )
    return conversation
