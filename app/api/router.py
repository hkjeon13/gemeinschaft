import os
from fastapi import APIRouter
from .admin import admin_router
from .auth import auth_router
from .conversation import conversation_router


router = APIRouter()

router.include_router(
    auth_router,
    prefix=os.getenv("AUTH_ROUTE", "/auth"),
    tags=["auth"],
)

router.include_router(
    conversation_router,
    prefix=os.getenv("CONVERSATION_ROUTE", "/conversation"),
    tags=["conversation"],
)

router.include_router(
    admin_router,
    prefix=os.getenv("ADMIN_ROUTE", "/admin"),
    tags=["admin"],
)
