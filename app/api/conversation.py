import asyncio
import json
import logging
import os
import random
from typing import Any, AsyncIterator, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.schemas.conversation import (
    ConversationContinueSchema,
    ConversationDetailSchema,
    ConversationAssignedModelListSchema,
    ConversationAssignedModelSchema,
    ConversationAssignedModelUpdateSchema,
    ConversationModelOptionSchema,
    ConversationSummarySchema,
    ConversationTitleSchema,
    ConversationTitleUpdateSchema,
    ConversationVisibilitySchema,
    MessageCreateSchema,
    MessageInputSchema,
    UserDefaultModelSchema,
    UserDefaultModelUpdateSchema,
)
from app.services.async_openai_chat_model import AsyncOpenAIChatModel
from app.services.authorization import AccessContext, authorize_action, require_access_context
from app.services.chat_model_registry import (
    ResolvedChatModel,
    get_chat_model,
    list_chat_models,
    resolve_chat_model,
)
from app.services.conversation_prompt import render_conversation_developer_prompt
from app.services.conversation_store import conversation_store
from app.services.conversation_model_list_store import conversation_model_list_store
from app.services.user_model_preference_store import user_model_preference_store

conversation_router = APIRouter()
logger = logging.getLogger(__name__)
_STREAM_EVENT_QUEUE_MAXSIZE = 256
_TEXT_CONTENT_TYPES = {"text", "input_text", "output_text"}
_IMAGE_CONTENT_TYPES = {"image_url", "input_image", "output_image"}
_GREETING_LOOP_MARKERS = (
    "안녕",
    "반가",
    "도와드릴까요",
    "도와줄까요",
    "도움이 필요",
    "무엇을 도와",
    "how can i help",
    "how may i assist",
    "what can i help",
    "nice to meet",
    "glad to meet",
)
_TOPIC_SHIFT_SUGGESTIONS = (
    "오늘 식사/간식",
    "주말 계획",
    "요즘 즐겨보는 콘텐츠",
    "최근 날씨와 컨디션",
    "요즘 하고 있는 취미",
)


def _is_supported_conversation_model(provider: str, is_active: bool) -> bool:
    return is_active and provider == "openai"


def _effective_default_model(tenant_id: str, user_id: str) -> tuple[str, str, str]:
    """Return (model_id, display_name, source)."""
    preferred = user_model_preference_store.get_default_model_id(tenant_id=tenant_id, user_id=user_id)
    if preferred:
        preferred_record = get_chat_model(preferred)
        if preferred_record is not None and _is_supported_conversation_model(
            preferred_record.provider, preferred_record.is_active
        ):
            return preferred_record.model_id, preferred_record.display_name, "user"
        user_model_preference_store.clear_default_model_id(tenant_id=tenant_id, user_id=user_id)

    all_models = list_chat_models()
    for model in all_models:
        if model.is_default and _is_supported_conversation_model(model.provider, model.is_active):
            return model.model_id, model.display_name, "global"

    for model in all_models:
        if _is_supported_conversation_model(model.provider, model.is_active):
            return model.model_id, model.display_name, "global"

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="No active conversation model is configured.",
    )


def _conversation_model_ids_or_default(tenant_id: str, user_id: str, conversation_id: str) -> list[str]:
    raw_ids = conversation_model_list_store.get_model_ids(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    valid_ids: list[str] = []
    seen: set[str] = set()
    for candidate in raw_ids:
        model = get_chat_model(candidate)
        if model is None or not _is_supported_conversation_model(model.provider, model.is_active):
            continue
        if model.model_id in seen:
            continue
        seen.add(model.model_id)
        valid_ids.append(model.model_id)

    if not valid_ids:
        default_model_id, _, _ = _effective_default_model(tenant_id=tenant_id, user_id=user_id)
        valid_ids = [default_model_id]

    if valid_ids != raw_ids:
        conversation_model_list_store.set_model_ids(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            model_ids=valid_ids,
        )

    return valid_ids


def _normalize_model_id_list(raw_ids: list[str] | None) -> list[str]:
    if not raw_ids:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_ids:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _select_model_id(
    *,
    available_model_ids: list[str],
    requested_model_id: str | None,
    requested_model_ids: list[str] | None,
) -> str:
    if requested_model_id and requested_model_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use either model_id or model_ids, not both.",
        )

    if requested_model_id:
        normalized = requested_model_id.strip()
        if normalized not in available_model_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Requested model is not configured for this conversation.",
            )
        return normalized

    requested_candidates = _normalize_model_id_list(requested_model_ids)
    if requested_candidates:
        unavailable = [item for item in requested_candidates if item not in available_model_ids]
        if unavailable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested model_ids are not configured for this conversation: {', '.join(unavailable)}",
            )
        return random.choice(requested_candidates)

    if not available_model_ids:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No conversation models are configured.",
        )
    return random.choice(available_model_ids)


def _resolve_continue_model_candidates(
    *,
    available_model_ids: list[str],
    requested_model_id: str | None,
    requested_model_ids: list[str] | None,
) -> list[str]:
    if requested_model_id and requested_model_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use either model_id or model_ids, not both.",
        )

    if requested_model_id:
        normalized = requested_model_id.strip()
        if normalized not in available_model_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Requested model is not configured for this conversation.",
            )
        return [normalized]

    requested_candidates = _normalize_model_id_list(requested_model_ids)
    if requested_candidates:
        unavailable = [item for item in requested_candidates if item not in available_model_ids]
        if unavailable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested model_ids are not configured for this conversation: {', '.join(unavailable)}",
            )
        return requested_candidates

    return list(available_model_ids)


def _ensure_continue_min_participants(model_ids: list[str]) -> None:
    if len(model_ids) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Continue mode requires at least 2 models in the candidate list. "
                "Add one more model to this conversation."
            ),
        )


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be a number.",
        )
    if value < 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be >= 0.",
        )
    return value


def _resolve_continue_interval_seconds(
    *,
    min_interval_seconds: float | None,
    max_interval_seconds: float | None,
) -> float:
    resolved_min = min_interval_seconds
    resolved_max = max_interval_seconds
    if resolved_min is None:
        resolved_min = _env_float("CONVERSATION_CONTINUE_MIN_INTERVAL_SECONDS", 1.0)
    if resolved_max is None:
        resolved_max = _env_float("CONVERSATION_CONTINUE_MAX_INTERVAL_SECONDS", 10.0)
    if resolved_max < resolved_min:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="max_interval_seconds must be greater than or equal to min_interval_seconds.",
        )
    if resolved_max == resolved_min:
        return resolved_min
    return random.uniform(resolved_min, resolved_max)


def _count_assistant_turns(conversation: dict[str, Any], *, user_id: str) -> int:
    raw_messages = conversation.get("messages", [])
    if not isinstance(raw_messages, list):
        return 0

    count = 0
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model_id", "")).strip()
        if model_id:
            if model_id != user_id:
                count += 1
            continue
        role = str(item.get("role", "")).strip().lower()
        if role == "assistant":
            count += 1
    return count


def _conversation_model_list_response(
    conversation_id: str,
    model_ids: list[str],
) -> ConversationAssignedModelListSchema:
    models: list[ConversationAssignedModelSchema] = []
    for model_id in model_ids:
        model = get_chat_model(model_id)
        if model is None or not _is_supported_conversation_model(model.provider, model.is_active):
            continue
        models.append(
            ConversationAssignedModelSchema(
                model_id=model.model_id,
                provider=model.provider,
                openai_api=model.openai_api,
                model=model.model,
                display_name=model.display_name,
                description=model.description,
            )
        )
    return ConversationAssignedModelListSchema(conversation_id=conversation_id, models=models)


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
            api_keys=selected.api_keys,
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


def _normalize_content_blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        block_type = str(item.get("type", "")).strip().lower()
        if not block_type:
            continue
        if block_type in _TEXT_CONTENT_TYPES:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            normalized.append({"type": "input_text" if block_type == "text" else block_type, "text": text})
            continue
        if block_type in _IMAGE_CONTENT_TYPES:
            image_url = str(item.get("image_url", "")).strip()
            if not image_url:
                continue
            normalized.append({"type": "input_image" if block_type == "image_url" else block_type, "image_url": image_url})
    return normalized


def _content_to_preview_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in content:
        block_type = str(block.get("type", "")).strip().lower()
        if block_type not in _TEXT_CONTENT_TYPES:
            continue
        text = str(block.get("text", "")).strip()
        if text:
            parts.append(text)
    if parts:
        return "\n".join(parts).strip()
    for block in content:
        block_type = str(block.get("type", "")).strip().lower()
        if block_type in _IMAGE_CONTENT_TYPES:
            image_url = str(block.get("image_url", "")).strip()
            if image_url:
                return "[image]"
    return ""


def _conversation_to_openai_messages(
    conversation: dict[str, Any],
    *,
    selected_model_id: str,
    max_messages: int = 20,
) -> list[dict[str, Any]]:
    messages = conversation.get("messages", [])
    if not isinstance(messages, list):
        return []

    recent = messages[-max_messages:]
    converted: list[dict[str, Any]] = []
    for item in recent:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model_id", "")).strip()
        role = "assistant" if model_id and model_id == selected_model_id else "user"
        content_blocks = _normalize_content_blocks(item.get("content"))
        if content_blocks:
            converted.append({"role": role, "content": content_blocks})
            continue

        text = str(item.get("message", "")).strip()
        if text:
            converted.append({"role": role, "content": text})
    return converted


def _prepend_developer_prompt(
    messages: list[dict[str, Any]],
    *,
    selected_model_id: str,
    selected_model_display_name: str,
    user_id: str,
) -> list[dict[str, Any]]:
    prompt = render_conversation_developer_prompt(
        selected_model_id=selected_model_id,
        selected_model_display_name=selected_model_display_name,
        user_id=user_id,
    )
    if not prompt:
        return messages
    if _needs_topic_shift(messages):
        topic = random.choice(_TOPIC_SHIFT_SUGGESTIONS)
        prompt = (
            f"{prompt}\n\n"
            "Additional directive for this turn: recent turns are stuck in repetitive greeting/help-offer patterns. "
            "Do not greet again. Move the conversation forward naturally with a concrete small-talk pivot. "
            f"Suggested pivot topic: {topic}. "
            "Use one short statement + one specific follow-up question."
        )
    return [{"role": "developer", "content": prompt}, *messages]


def _extract_text_from_openai_message(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text", "")).strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _is_greeting_like(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _GREETING_LOOP_MARKERS)


def _needs_topic_shift(messages: list[dict[str, Any]]) -> bool:
    if not messages:
        return False
    recent = messages[-4:]
    recent_texts = [_extract_text_from_openai_message(item) for item in recent]
    greeting_like_count = sum(1 for text in recent_texts if _is_greeting_like(text))
    if greeting_like_count < 2:
        return False
    # When most recent turns are greeting-like, force a pivot.
    return greeting_like_count >= max(2, len([text for text in recent_texts if text]) - 1)


def _message_input_to_content(item: MessageInputSchema) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in item.content:
        block_type = str(part.type).strip().lower()
        if block_type in _TEXT_CONTENT_TYPES:
            text = (part.text or "").strip()
            if not text:
                continue
            blocks.append({"type": "input_text" if block_type == "text" else block_type, "text": text})
            continue
        if block_type in _IMAGE_CONTENT_TYPES:
            image_url = (part.image_url or "").strip()
            if not image_url:
                continue
            blocks.append({"type": "input_image" if block_type == "image_url" else block_type, "image_url": image_url})
    return blocks


def _resolve_user_input(payload: MessageCreateSchema) -> tuple[str, list[dict[str, Any]]]:
    if payload.messages:
        for item in reversed(payload.messages):
            if item.role != "user":
                continue
            blocks = _message_input_to_content(item)
            if blocks:
                return _content_to_preview_text(blocks), blocks

        for item in reversed(payload.messages):
            blocks = _message_input_to_content(item)
            if blocks:
                return _content_to_preview_text(blocks), blocks

    if payload.message:
        text = payload.message.strip()
        if text:
            return text, [{"type": "input_text", "text": text}]

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="At least one content block is required in `messages[].content[]` or `message`.",
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _log_background_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Detached assistant generation task failed.")


def _put_stream_event(queue: asyncio.Queue[tuple[str, dict[str, Any]]], event: str, data: dict[str, Any]) -> None:
    try:
        queue.put_nowait((event, data))
        return
    except asyncio.QueueFull:
        if event not in {"done", "error"}:
            return

    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        return

    try:
        queue.put_nowait((event, data))
    except asyncio.QueueFull:
        return


async def _append_assistant_message(
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    message: str,
    model_id: str,
    model_name: str,
    model_display_name: str,
    provider: str,
) -> dict[str, Any] | None:
    text = message.strip()
    if not text:
        return None
    content = [{"type": "output_text", "text": text}]
    return await run_in_threadpool(
        conversation_store.append_message,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        message=text,
        role="assistant",
        content=content,
        model_id=model_id,
        model_name=model_name,
        model_display_name=model_display_name,
        provider=provider,
    )


async def _generate_assistant_reply(
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    model_id: str,
    model_name: str,
    model_display_name: str,
    provider: str,
    chat_model: AsyncOpenAIChatModel,
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    assistant_reply = await chat_model.generate_messages(messages)
    return await _append_assistant_message(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        message=assistant_reply,
        model_id=model_id,
        model_name=model_name,
        model_display_name=model_display_name,
        provider=provider,
    )


async def _stream_assistant_reply_task(
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    model_id: str,
    model_name: str,
    model_display_name: str,
    provider: str,
    chat_model: AsyncOpenAIChatModel,
    messages: list[dict[str, Any]],
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
) -> None:
    chunks: list[str] = []
    try:
        async for delta in chat_model.stream_messages(messages):
            chunks.append(delta)
            _put_stream_event(event_queue, "delta", {"text": delta})
    except Exception as exc:
        _put_stream_event(event_queue, "error", {"detail": str(exc)})
        return

    full_text = "".join(chunks).strip()
    if full_text:
        await _append_assistant_message(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            message=full_text,
            model_id=model_id,
            model_name=model_name,
            model_display_name=model_display_name,
            provider=provider,
        )

    _put_stream_event(
        event_queue,
        "done",
        {
            "conversation_id": conversation_id,
            "model_id": model_id,
            "model_name": model_name,
            "model_display_name": model_display_name,
            "provider": provider,
        },
    )


async def _stream_assistant_reply(
    *,
    tenant_id: str,
    user_id: str,
    event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
) -> AsyncIterator[str]:
    try:
        while True:
            event, data = await event_queue.get()
            yield _sse(event, data)
            if event in {"done", "error"}:
                return
    except asyncio.CancelledError:
        # Client disconnected; detached generation task continues and persists response.
        return


@conversation_router.get("/list", response_model=List[ConversationSummarySchema])
async def conversation_list(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:list")
    return await run_in_threadpool(
        conversation_store.list_conversations,
        tenant_id=access.tenant,
        user_id=access.subject,
    )


@conversation_router.get("/model/list", response_model=List[ConversationModelOptionSchema])
async def conversation_model_list(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:model:list")
    all_models = await run_in_threadpool(list_chat_models)
    user_default_id = await run_in_threadpool(
        user_model_preference_store.get_default_model_id,
        tenant_id=access.tenant,
        user_id=access.subject,
    )

    items: List[ConversationModelOptionSchema] = []
    for item in all_models:
        if not _is_supported_conversation_model(item.provider, item.is_active):
            continue
        items.append(
            ConversationModelOptionSchema(
                model_id=item.model_id,
                provider=item.provider,
                openai_api=item.openai_api,
                model=item.model,
                display_name=item.display_name,
                description=item.description,
                is_global_default=item.is_default,
                is_user_default=(item.model_id == user_default_id),
            )
        )
    return items


@conversation_router.get("/model/default", response_model=UserDefaultModelSchema)
async def conversation_default_model(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:model:get_default")
    model_id, display_name, source = await run_in_threadpool(
        _effective_default_model,
        tenant_id=access.tenant,
        user_id=access.subject,
    )
    return UserDefaultModelSchema(model_id=model_id, display_name=display_name, source=source)


@conversation_router.put("/model/default", response_model=UserDefaultModelSchema)
async def set_conversation_default_model(
    payload: UserDefaultModelUpdateSchema,
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="conversation:model:set_default")
    requested_model_id = payload.model_id.strip()
    model = await run_in_threadpool(get_chat_model, requested_model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requested model is not registered.")
    if not model.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Requested model is inactive.")
    if model.provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested model provider is not supported for conversations yet.",
        )

    await run_in_threadpool(
        user_model_preference_store.set_default_model_id,
        tenant_id=access.tenant,
        user_id=access.subject,
        model_id=model.model_id,
    )
    return UserDefaultModelSchema(model_id=model.model_id, display_name=model.display_name, source="user")


@conversation_router.delete("/model/default", response_model=UserDefaultModelSchema)
async def clear_conversation_default_model(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:model:set_default")
    await run_in_threadpool(
        user_model_preference_store.clear_default_model_id,
        tenant_id=access.tenant,
        user_id=access.subject,
    )
    model_id, display_name, source = await run_in_threadpool(
        _effective_default_model,
        tenant_id=access.tenant,
        user_id=access.subject,
    )
    return UserDefaultModelSchema(model_id=model_id, display_name=display_name, source=source)


@conversation_router.get("/{conversation_id}/models", response_model=ConversationAssignedModelListSchema)
async def get_conversation_models(conversation_id: str, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:get", resource_id=conversation_id)
    model_ids = await run_in_threadpool(
        _conversation_model_ids_or_default,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    return await run_in_threadpool(_conversation_model_list_response, conversation_id, model_ids)


@conversation_router.post("/{conversation_id}/models", response_model=ConversationAssignedModelListSchema)
async def add_conversation_model(
    conversation_id: str,
    payload: ConversationAssignedModelUpdateSchema,
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="conversation:update", resource_id=conversation_id)
    requested_model_id = payload.model_id.strip()
    requested_model = await run_in_threadpool(get_chat_model, requested_model_id)
    if requested_model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requested model is not registered.")
    if not _is_supported_conversation_model(requested_model.provider, requested_model.is_active):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Requested model is not available for conversations.")

    model_ids = await run_in_threadpool(
        _conversation_model_ids_or_default,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    if requested_model.model_id not in model_ids:
        model_ids.append(requested_model.model_id)
    updated_ids = await run_in_threadpool(
        conversation_model_list_store.set_model_ids,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
        model_ids=model_ids,
    )
    return await run_in_threadpool(_conversation_model_list_response, conversation_id, updated_ids)


@conversation_router.delete("/{conversation_id}/models/{model_id}", response_model=ConversationAssignedModelListSchema)
async def remove_conversation_model(
    conversation_id: str,
    model_id: str,
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="conversation:update", resource_id=conversation_id)
    conversation = await run_in_threadpool(
        conversation_store.get_conversation,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    model_ids = await run_in_threadpool(
        _conversation_model_ids_or_default,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    normalized_model_id = model_id.strip()
    if normalized_model_id not in model_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model is not configured for this conversation.")
    if len(model_ids) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one model must remain configured for the conversation.",
        )

    updated_ids = [item for item in model_ids if item != normalized_model_id]
    stored_ids = await run_in_threadpool(
        conversation_model_list_store.set_model_ids,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
        model_ids=updated_ids,
    )
    return await run_in_threadpool(_conversation_model_list_response, conversation_id, stored_ids)


@conversation_router.get("/{conversation_id}", response_model=ConversationDetailSchema)
async def get_dialogue(conversation_id: str, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:get", resource_id=conversation_id)
    conversation = await run_in_threadpool(
        conversation_store.get_conversation,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
        mark_read=True,
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
    user_message, user_content = _resolve_user_input(payload)
    conversation = await run_in_threadpool(
        conversation_store.append_message,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
        message=user_message,
        role="user",
        content=user_content,
    )
    conversation_model_ids = await run_in_threadpool(
        _conversation_model_ids_or_default,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )

    selected_model_id = _select_model_id(
        available_model_ids=conversation_model_ids,
        requested_model_id=(payload.model_id.strip() if payload.model_id is not None else None),
        requested_model_ids=payload.model_ids,
    )

    selected_model = await run_in_threadpool(resolve_chat_model, selected_model_id)
    if selected_model.provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {selected_model.provider}",
        )
    model_client = _chat_model(selected_model)

    messages = _conversation_to_openai_messages(
        conversation,
        selected_model_id=selected_model.model_id,
    )
    messages = _prepend_developer_prompt(
        messages,
        selected_model_id=selected_model.model_id,
        selected_model_display_name=selected_model.display_name,
        user_id=access.subject,
    )
    if not messages:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to build chat messages.")

    if stream:
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=_STREAM_EVENT_QUEUE_MAXSIZE)
        stream_task = asyncio.create_task(
            _stream_assistant_reply_task(
                tenant_id=access.tenant,
                user_id=access.subject,
                conversation_id=conversation_id,
                model_id=selected_model.model_id,
                model_name=selected_model.model,
                model_display_name=selected_model.display_name,
                provider=selected_model.provider,
                chat_model=model_client,
                messages=messages,
                event_queue=event_queue,
            )
        )
        stream_task.add_done_callback(_log_background_task_result)
        return StreamingResponse(
            _stream_assistant_reply(
                tenant_id=access.tenant,
                user_id=access.subject,
                event_queue=event_queue,
            ),
            media_type="text/event-stream",
        )

    generation_task = asyncio.create_task(
        _generate_assistant_reply(
            tenant_id=access.tenant,
            user_id=access.subject,
            conversation_id=conversation_id,
            model_id=selected_model.model_id,
            model_name=selected_model.model,
            model_display_name=selected_model.display_name,
            provider=selected_model.provider,
            chat_model=model_client,
            messages=messages,
        )
    )
    generation_task.add_done_callback(_log_background_task_result)

    try:
        generated_conversation = await asyncio.shield(generation_task)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except asyncio.CancelledError:
        # Request ended early; detached generation continues and persists reply.
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate assistant response: {exc}",
        )

    if generated_conversation is not None:
        return generated_conversation
    return conversation


@conversation_router.post("/{conversation_id}/continue", response_model=ConversationDetailSchema)
async def continue_dialogue(
    conversation_id: str,
    payload: ConversationContinueSchema,
    stream: bool = Query(default=False),
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="conversation:create", resource_id=conversation_id)
    conversation = await run_in_threadpool(
        conversation_store.get_conversation,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    if not isinstance(conversation.get("messages"), list) or not conversation.get("messages"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Conversation has no messages to continue from.")
    if payload.max_turns is not None:
        current_turns = _count_assistant_turns(conversation, user_id=access.subject)
        if current_turns >= payload.max_turns:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"max_turns reached ({payload.max_turns}).",
            )

    conversation_model_ids = await run_in_threadpool(
        _conversation_model_ids_or_default,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    continue_candidates = _resolve_continue_model_candidates(
        available_model_ids=conversation_model_ids,
        requested_model_id=(payload.model_id.strip() if payload.model_id is not None else None),
        requested_model_ids=payload.model_ids,
    )
    has_explicit_model_selection = payload.model_id is not None or bool(payload.model_ids)
    if not has_explicit_model_selection:
        _ensure_continue_min_participants(continue_candidates)
    selected_model_id = random.choice(continue_candidates)

    selected_model = await run_in_threadpool(resolve_chat_model, selected_model_id)
    if selected_model.provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {selected_model.provider}",
        )
    model_client = _chat_model(selected_model)

    messages = _conversation_to_openai_messages(
        conversation,
        selected_model_id=selected_model.model_id,
    )
    messages = _prepend_developer_prompt(
        messages,
        selected_model_id=selected_model.model_id,
        selected_model_display_name=selected_model.display_name,
        user_id=access.subject,
    )
    if not messages:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to build chat messages.")

    delay_seconds = _resolve_continue_interval_seconds(
        min_interval_seconds=payload.min_interval_seconds,
        max_interval_seconds=payload.max_interval_seconds,
    )
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    if stream:
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=_STREAM_EVENT_QUEUE_MAXSIZE)
        stream_task = asyncio.create_task(
            _stream_assistant_reply_task(
                tenant_id=access.tenant,
                user_id=access.subject,
                conversation_id=conversation_id,
                model_id=selected_model.model_id,
                model_name=selected_model.model,
                model_display_name=selected_model.display_name,
                provider=selected_model.provider,
                chat_model=model_client,
                messages=messages,
                event_queue=event_queue,
            )
        )
        stream_task.add_done_callback(_log_background_task_result)
        return StreamingResponse(
            _stream_assistant_reply(
                tenant_id=access.tenant,
                user_id=access.subject,
                event_queue=event_queue,
            ),
            media_type="text/event-stream",
        )

    generation_task = asyncio.create_task(
        _generate_assistant_reply(
            tenant_id=access.tenant,
            user_id=access.subject,
            conversation_id=conversation_id,
            model_id=selected_model.model_id,
            model_name=selected_model.model,
            model_display_name=selected_model.display_name,
            provider=selected_model.provider,
            chat_model=model_client,
            messages=messages,
        )
    )
    generation_task.add_done_callback(_log_background_task_result)

    try:
        generated_conversation = await asyncio.shield(generation_task)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate assistant response: {exc}",
        )

    if generated_conversation is not None:
        return generated_conversation
    return conversation


@conversation_router.delete("/{conversation_id}", response_model=ConversationVisibilitySchema)
async def hide_dialogue(conversation_id: str, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="conversation:delete", resource_id=conversation_id)
    hidden = await run_in_threadpool(
        conversation_store.hide_conversation,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
    )
    if not hidden:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return {"conversation_id": conversation_id, "visible": False}


@conversation_router.patch("/{conversation_id}/title", response_model=ConversationTitleSchema)
async def update_dialogue_title(
    conversation_id: str,
    payload: ConversationTitleUpdateSchema,
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="conversation:update", resource_id=conversation_id)
    normalized = payload.title.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="title must be a non-empty string.")
    updated = await run_in_threadpool(
        conversation_store.update_title,
        tenant_id=access.tenant,
        user_id=access.subject,
        conversation_id=conversation_id,
        title=normalized,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return {"conversation_id": conversation_id, "title": updated}
