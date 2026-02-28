import os
from fastapi import APIRouter
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
