from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional
import uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConversationStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._conversations_by_tenant: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}

    def list_conversations(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            tenant_conversations = self._conversations_by_tenant.get(tenant_id, {})
            user_conversations = tenant_conversations.get(user_id, {})
            summaries = []
            for conversation_id, conversation in user_conversations.items():
                summaries.append(
                    {
                        "conversation_id": conversation_id,
                        "message_count": len(conversation["messages"]),
                        "updated_at": conversation["updated_at"],
                    }
                )

        summaries.sort(key=lambda item: item["updated_at"], reverse=True)
        return summaries

    def get_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            tenant_conversations = self._conversations_by_tenant.get(tenant_id, {})
            user_conversations = tenant_conversations.get(user_id, {})
            conversation = user_conversations.get(conversation_id)
            if conversation is None:
                return None
            return {
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "messages": list(conversation["messages"]),
                "updated_at": conversation["updated_at"],
            }

    def append_message(self, tenant_id: str, user_id: str, conversation_id: str, message: str) -> Dict[str, Any]:
        now = _now_iso()
        entry = {
            "message_id": uuid.uuid4().hex,
            "message": message,
            "created_at": now,
        }

        with self._lock:
            tenant_conversations = self._conversations_by_tenant.setdefault(tenant_id, {})
            user_conversations = tenant_conversations.setdefault(user_id, {})
            conversation = user_conversations.setdefault(
                conversation_id,
                {
                    "messages": [],
                    "updated_at": now,
                },
            )
            conversation["messages"].append(entry)
            conversation["updated_at"] = now

            return {
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "messages": list(conversation["messages"]),
                "updated_at": conversation["updated_at"],
            }


conversation_store = ConversationStore()
