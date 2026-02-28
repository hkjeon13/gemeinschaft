import os
from fastapi import APIRouter
from .conversation import conversation_router


router = APIRouter()

router.include_router(
    conversation_router,
    prefix=os.getenv("CONVERSATION_ROUTE", "/conversation"),
    tags=["conversation"]
)



