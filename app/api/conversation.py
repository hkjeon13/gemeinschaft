from fastapi import APIRouter, Depends
from app.services.auth import require_jwt

conversation_router = APIRouter(dependencies=[Depends(require_jwt)])


@conversation_router.get("/")
async def conversation_list():
    return []


@conversation_router.get("/{conversation_id}")
async def get_dialogue(conversation_id: str):
    return []


@conversation_router.post("/{conversation_id}")
async def create_dialogue(conversation_id: str):
    return []
