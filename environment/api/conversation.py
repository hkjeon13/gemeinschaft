from fastapi import APIRouter

conversation_router = APIRouter()


@conversation_router.get("/list")
async def conversation_list():
    return []


@conversation_router.get("/conversation/{conversation_id}")
async def conversation_conversation(conversation_id: int):
    return []