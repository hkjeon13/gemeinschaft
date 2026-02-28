from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.admin import AdminUserCreateSchema, AdminUserSchema, AdminUserUpdateSchema
from app.services.authorization import AccessContext, authorize_action, require_access_context
from app.services.auth import (
    create_auth_user,
    delete_auth_user,
    get_auth_user,
    list_auth_users,
    update_auth_user,
)

admin_router = APIRouter()


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
