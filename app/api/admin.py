import random
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.admin import (
    AdminChatModelCreateSchema,
    AdminChatModelSchema,
    AdminChatModelUpdateSchema,
    AdminUserCreateSchema,
    AdminUserSchema,
    AdminUserUpdateSchema,
)
from app.services.authorization import AccessContext, authorize_action, require_access_context
from app.services.auth import (
    create_auth_user,
    delete_auth_user,
    get_auth_user,
    list_auth_users,
    update_auth_user,
)
from app.services.chat_model_registry import (
    create_chat_model,
    delete_chat_model,
    get_chat_model,
    list_chat_models,
    update_chat_model,
)

admin_router = APIRouter()
_RNG = random.SystemRandom()
_MODEL_ID_WORDS_A = (
    "amber",
    "apex",
    "arctic",
    "bold",
    "brisk",
    "calm",
    "clear",
    "cobalt",
    "crisp",
    "delta",
    "ember",
    "frost",
    "gloss",
    "lunar",
    "mist",
    "nova",
    "opal",
    "prism",
    "rapid",
    "sage",
    "solar",
    "swift",
    "tidal",
    "vivid",
)
_MODEL_ID_WORDS_B = (
    "anchor",
    "bridge",
    "cipher",
    "comet",
    "drift",
    "engine",
    "falcon",
    "field",
    "forge",
    "garden",
    "harbor",
    "horizon",
    "matrix",
    "orbit",
    "path",
    "peak",
    "quest",
    "ridge",
    "signal",
    "spark",
    "stream",
    "vector",
    "voyage",
    "wave",
)


def _generate_model_id() -> str:
    for _ in range(96):
        candidate = f"{_RNG.choice(_MODEL_ID_WORDS_A)}-{_RNG.choice(_MODEL_ID_WORDS_B)}-{_RNG.randint(100, 999)}"
        if get_chat_model(candidate) is None:
            return candidate

    while True:
        fallback = f"model-{uuid.uuid4().hex[:10]}"
        if get_chat_model(fallback) is None:
            return fallback


def _model_schema(item) -> AdminChatModelSchema:
    return AdminChatModelSchema(
        model_id=item.model_id,
        provider=item.provider,
        openai_api=item.openai_api,
        model=item.model,
        display_name=item.display_name,
        description=item.description,
        parameters=item.parameters,
        client_options=item.client_options,
        chat_create_options=item.chat_create_options,
        responses_create_options=item.responses_create_options,
        has_api_key=item.has_api_key,
        has_webhook_secret=item.has_webhook_secret,
        is_active=item.is_active,
        is_default=item.is_default,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@admin_router.get("/users", response_model=List[AdminUserSchema])
async def admin_list_users(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="admin:user:list")
    users = list_auth_users()
    return [
        AdminUserSchema(
            username=user.username,
            role=user.role,
            tenant=user.tenant,
            scopes=user.scopes,
        )
        for user in users
    ]


@admin_router.post("/users", response_model=AdminUserSchema, status_code=status.HTTP_201_CREATED)
async def admin_create_user(payload: AdminUserCreateSchema, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="admin:user:create")
    user = create_auth_user(
        username=payload.username,
        password=payload.password,
        role=payload.role,
        tenant=payload.tenant,
        scopes=payload.scopes,
    )
    return AdminUserSchema(
        username=user.username,
        role=user.role,
        tenant=user.tenant,
        scopes=user.scopes,
    )


@admin_router.patch("/users/{username}", response_model=AdminUserSchema)
async def admin_update_user(
    username: str,
    payload: AdminUserUpdateSchema,
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="admin:user:update")
    if payload.password is None and payload.role is None and payload.tenant is None and payload.scopes is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    user = update_auth_user(
        username=username,
        password=payload.password,
        role=payload.role,
        tenant=payload.tenant,
        scopes=payload.scopes,
    )
    return AdminUserSchema(
        username=user.username,
        role=user.role,
        tenant=user.tenant,
        scopes=user.scopes,
    )


@admin_router.delete("/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_user(username: str, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="admin:user:delete")
    current = get_auth_user(username)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    delete_auth_user(username)


@admin_router.get("/models", response_model=List[AdminChatModelSchema])
async def admin_list_models(access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="admin:model:list")
    return [_model_schema(item) for item in list_chat_models()]


@admin_router.post("/models", response_model=AdminChatModelSchema, status_code=status.HTTP_201_CREATED)
async def admin_create_model(payload: AdminChatModelCreateSchema, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="admin:model:create")
    model_id = payload.model_id.strip() if payload.model_id else _generate_model_id()
    created = create_chat_model(
        model_id=model_id,
        provider=payload.provider,
        openai_api=payload.openai_api,
        model=payload.model,
        display_name=payload.display_name,
        description=payload.description,
        parameters=payload.parameters,
        client_options=payload.client_options,
        chat_create_options=payload.chat_create_options,
        responses_create_options=payload.responses_create_options,
        api_key=payload.api_key,
        webhook_secret=payload.webhook_secret,
        is_active=payload.is_active,
        is_default=payload.is_default,
    )
    return _model_schema(created)


@admin_router.patch("/models/{model_id}", response_model=AdminChatModelSchema)
async def admin_update_model(
    model_id: str,
    payload: AdminChatModelUpdateSchema,
    access: AccessContext = Depends(require_access_context),
):
    authorize_action(access, action="admin:model:update")
    if (
        payload.provider is None
        and payload.openai_api is None
        and payload.model is None
        and payload.display_name is None
        and payload.description is None
        and payload.parameters is None
        and payload.client_options is None
        and payload.chat_create_options is None
        and payload.responses_create_options is None
        and payload.api_key is None
        and payload.clear_api_key is None
        and payload.webhook_secret is None
        and payload.clear_webhook_secret is None
        and payload.is_active is None
        and payload.is_default is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    updated = update_chat_model(
        model_id=model_id,
        provider=payload.provider,
        openai_api=payload.openai_api,
        model=payload.model,
        display_name=payload.display_name,
        description=payload.description,
        parameters=payload.parameters,
        client_options=payload.client_options,
        chat_create_options=payload.chat_create_options,
        responses_create_options=payload.responses_create_options,
        api_key=payload.api_key,
        clear_api_key=payload.clear_api_key,
        webhook_secret=payload.webhook_secret,
        clear_webhook_secret=payload.clear_webhook_secret,
        is_active=payload.is_active,
        is_default=payload.is_default,
    )
    return _model_schema(updated)


@admin_router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_model(model_id: str, access: AccessContext = Depends(require_access_context)):
    authorize_action(access, action="admin:model:delete")
    delete_chat_model(model_id)
