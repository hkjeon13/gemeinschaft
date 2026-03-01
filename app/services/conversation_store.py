import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from .database import database_url_from_settings, load_database_settings

try:
    import psycopg
except ImportError:  # pragma: no cover - installed in runtime image
    psycopg = None

try:
    import redis
except ImportError:  # pragma: no cover - installed in runtime image
    redis = None

logger = logging.getLogger(__name__)

_ALLOWED_ROLES = {"user", "assistant", "system"}
_TITLE_MAX_LENGTH = 120
_TEXT_CONTENT_TYPES = {"text", "input_text", "output_text"}
_IMAGE_CONTENT_TYPES = {"image_url", "input_image", "output_image"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat().replace("+00:00", "Z")


def _to_iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _parse_iso_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return _now_utc()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return _now_utc()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_epoch(value: Any) -> float:
    return _parse_iso_datetime(value).timestamp()


def _normalize_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized in _ALLOWED_ROLES:
        return normalized
    return "user"


def _normalized_message(entry: Dict[str, Any]) -> Dict[str, Any]:
    message_id = entry.get("message_id")
    created_at = entry.get("created_at")
    message = entry.get("message")
    role = entry.get("role", "user")
    model_id = entry.get("model_id")
    model_name = entry.get("model_name")
    model_display_name = entry.get("model_display_name")
    provider = entry.get("provider")
    content = entry.get("content")

    normalized_content = _normalize_content_blocks(content)
    if not normalized_content:
        normalized_content = _content_from_legacy_message(role=str(role), message=message)
    message_text = _text_from_content(normalized_content)
    if not message_text and isinstance(message, str) and message.strip():
        message_text = message.strip()

    normalized = {
        "message_id": str(message_id or ""),
        "role": _normalize_role(str(role)),
        "message": message_text,
        "content": normalized_content,
        "created_at": str(created_at or _now_iso()),
    }
    normalized["model_id"] = str(model_id).strip() if model_id is not None else None
    normalized["model_name"] = str(model_name).strip() if model_name is not None else None
    normalized["model_display_name"] = str(model_display_name).strip() if model_display_name is not None else None
    normalized["provider"] = str(provider).strip().lower() if provider is not None else None
    return normalized


def _normalize_content_blocks(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, list):
        return []

    normalized: List[Dict[str, Any]] = []
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
            continue

    return normalized


def _content_from_legacy_message(*, role: str, message: Any) -> List[Dict[str, Any]]:
    text = str(message or "").strip()
    if not text:
        return []
    normalized_role = _normalize_role(role)
    if normalized_role == "assistant":
        return [{"type": "output_text", "text": text}]
    return [{"type": "input_text", "text": text}]


def _text_from_content(content: List[Dict[str, Any]]) -> str:
    text_parts: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "")).strip().lower()
        if block_type not in _TEXT_CONTENT_TYPES:
            continue
        text = str(block.get("text", "")).strip()
        if text:
            text_parts.append(text)
    return "\n".join(text_parts).strip()


def _normalize_title(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    return text[:_TITLE_MAX_LENGTH]


def _title_from_messages(messages: List[Dict[str, Any]]) -> Optional[str]:
    for item in messages:
        role = str(item.get("role", "")).strip().lower()
        if role != "user":
            continue
        candidate = _normalize_title(item.get("message"))
        if candidate:
            return candidate

    for item in messages:
        candidate = _normalize_title(item.get("message"))
        if candidate:
            return candidate
    return None


def _resolve_conversation_title(conversation: Dict[str, Any]) -> str:
    explicit = _normalize_title(conversation.get("title"))
    if explicit:
        return explicit
    messages = conversation.get("messages", [])
    normalized_messages = [_normalized_message(item) for item in messages if isinstance(item, dict)]
    inferred = _title_from_messages(normalized_messages)
    if inferred:
        return inferred
    return "New conversation"


def _has_unread_assistant_messages(messages: List[Dict[str, Any]], last_read_at: Optional[Any]) -> bool:
    if not messages:
        return False
    cutoff = _to_epoch(last_read_at) if last_read_at else float("-inf")
    for item in messages:
        if str(item.get("role", "")).strip().lower() != "assistant":
            continue
        created_at = item.get("created_at")
        if _to_epoch(created_at) > cutoff:
            return True
    return False


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    raw = os.getenv(name, fallback)
    return _truthy(raw)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be an integer.",
        )

    if value < minimum:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{name} must be >= {minimum}.",
        )
    return value


def _conversation_store_backend_name() -> str:
    configured = os.getenv("CONVERSATION_STORE_BACKEND", "").strip().lower()
    if configured:
        if configured == "redis":
            configured = "hybrid"
        if configured not in ("postgres", "memory", "hybrid"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="CONVERSATION_STORE_BACKEND must be 'postgres', 'memory', or 'hybrid'.",
            )
        return configured

    settings = load_database_settings()
    return "postgres" if settings.enabled else "memory"


class ConversationStoreBackend:
    def init_schema(self) -> None:
        raise NotImplementedError

    def list_conversations(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        mark_read: bool = False,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def append_message(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        message: str,
        role: str,
        content: Optional[List[Dict[str, Any]]] = None,
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_display_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def hide_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        raise NotImplementedError

    def update_title(self, tenant_id: str, user_id: str, conversation_id: str, title: str) -> Optional[str]:
        raise NotImplementedError

    def mark_conversation_read(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        raise NotImplementedError

    def start_background_tasks(self) -> None:
        return

    def stop_background_tasks(self) -> None:
        return


class InMemoryConversationStore(ConversationStoreBackend):
    def __init__(self) -> None:
        self._lock = Lock()
        self._conversations_by_tenant: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}

    def init_schema(self) -> None:
        return

    def list_conversations(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            tenant_conversations = self._conversations_by_tenant.get(tenant_id, {})
            user_conversations = tenant_conversations.get(user_id, {})
            summaries = []
            for conversation_id, conversation in user_conversations.items():
                if not bool(conversation.get("visible", True)):
                    continue
                messages = [item for item in conversation.get("messages", []) if isinstance(item, dict)]
                summaries.append(
                    {
                        "conversation_id": conversation_id,
                        "title": _resolve_conversation_title(conversation),
                        "message_count": len(messages),
                        "updated_at": conversation["updated_at"],
                        "has_unread": _has_unread_assistant_messages(
                            [_normalized_message(item) for item in messages],
                            conversation.get("last_read_at"),
                        ),
                    }
                )

        summaries.sort(key=lambda item: item["updated_at"], reverse=True)
        return summaries

    def get_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        mark_read: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            tenant_conversations = self._conversations_by_tenant.get(tenant_id, {})
            user_conversations = tenant_conversations.get(user_id, {})
            conversation = user_conversations.get(conversation_id)
            if conversation is None or not bool(conversation.get("visible", True)):
                return None
            if mark_read:
                conversation["last_read_at"] = _now_iso()
            return {
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "title": _resolve_conversation_title(conversation),
                "messages": [_normalized_message(item) for item in conversation["messages"]],
                "updated_at": conversation["updated_at"],
            }

    def append_message(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        message: str,
        role: str,
        content: Optional[List[Dict[str, Any]]] = None,
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_display_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = _now_iso()
        normalized_content = _normalize_content_blocks(content)
        if not normalized_content:
            normalized_content = _content_from_legacy_message(role=role, message=message)
        normalized_message = _text_from_content(normalized_content) or str(message or "").strip()
        entry = _normalized_message(
            {
                "message_id": uuid.uuid4().hex,
                "role": role,
                "message": normalized_message,
                "content": normalized_content,
                "created_at": now,
                "model_id": model_id,
                "model_name": model_name,
                "model_display_name": model_display_name,
                "provider": provider,
            }
        )

        with self._lock:
            tenant_conversations = self._conversations_by_tenant.setdefault(tenant_id, {})
            user_conversations = tenant_conversations.setdefault(user_id, {})
            conversation = user_conversations.setdefault(
                conversation_id,
                {
                    "messages": [],
                    "updated_at": now,
                    "title": None,
                    "visible": True,
                    "hidden_at": None,
                    "last_read_at": now,
                },
            )
            conversation["visible"] = True
            conversation["hidden_at"] = None
            conversation["messages"].append(entry)
            if _normalize_role(role) == "user":
                conversation["last_read_at"] = now
            if _normalize_title(conversation.get("title")) is None:
                conversation["title"] = _title_from_messages(conversation["messages"]) or "New conversation"
            conversation["updated_at"] = now

            return {
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "title": _resolve_conversation_title(conversation),
                "messages": [_normalized_message(item) for item in conversation["messages"]],
                "updated_at": conversation["updated_at"],
            }

    def hide_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            tenant_conversations = self._conversations_by_tenant.get(tenant_id, {})
            user_conversations = tenant_conversations.get(user_id, {})
            conversation = user_conversations.get(conversation_id)
            if conversation is None:
                return False
            conversation["visible"] = False
            conversation["hidden_at"] = _now_iso()
            conversation["updated_at"] = _now_iso()
            return True

    def update_title(self, tenant_id: str, user_id: str, conversation_id: str, title: str) -> Optional[str]:
        normalized_title = _normalize_title(title)
        if normalized_title is None:
            return None
        with self._lock:
            tenant_conversations = self._conversations_by_tenant.get(tenant_id, {})
            user_conversations = tenant_conversations.get(user_id, {})
            conversation = user_conversations.get(conversation_id)
            if conversation is None or not bool(conversation.get("visible", True)):
                return None
            conversation["title"] = normalized_title
            conversation["updated_at"] = _now_iso()
            return normalized_title

    def mark_conversation_read(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            tenant_conversations = self._conversations_by_tenant.get(tenant_id, {})
            user_conversations = tenant_conversations.get(user_id, {})
            conversation = user_conversations.get(conversation_id)
            if conversation is None or not bool(conversation.get("visible", True)):
                return False
            conversation["last_read_at"] = _now_iso()
            return True


class PostgresConversationStore(ConversationStoreBackend):
    def _dsn(self) -> str:
        settings = load_database_settings()
        if not settings.enabled:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DATABASE_ENABLED must be true when using Postgres conversation storage.",
            )
        return database_url_from_settings(settings)

    def _connect(self):
        if psycopg is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="psycopg is required for postgres conversation store.",
            )
        try:
            return psycopg.connect(self._dsn())
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to Postgres for conversation store.",
            )

    def init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS conversations (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            title TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_read_at TIMESTAMPTZ,
            visible BOOLEAN NOT NULL DEFAULT TRUE,
            hidden_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, user_id, conversation_id)
        );

        ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS title TEXT;
        ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMPTZ;
        ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS visible BOOLEAN NOT NULL DEFAULT TRUE;
        ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ;

        CREATE INDEX IF NOT EXISTS idx_conversations_tenant_user_updated
            ON conversations(tenant_id, user_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS conversation_messages (
            message_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            content_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            model_id TEXT,
            model_name TEXT,
            model_display_name TEXT,
            provider TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES conversations(tenant_id, user_id, conversation_id)
                ON DELETE CASCADE
        );

        ALTER TABLE conversation_messages
            ADD COLUMN IF NOT EXISTS model_id TEXT;
        ALTER TABLE conversation_messages
            ADD COLUMN IF NOT EXISTS content_json JSONB NOT NULL DEFAULT '[]'::jsonb;
        ALTER TABLE conversation_messages
            ADD COLUMN IF NOT EXISTS model_name TEXT;
        ALTER TABLE conversation_messages
            ADD COLUMN IF NOT EXISTS model_display_name TEXT;
        ALTER TABLE conversation_messages
            ADD COLUMN IF NOT EXISTS provider TEXT;

        UPDATE conversations c
        SET title = LEFT(
            COALESCE(
                NULLIF(TRIM(c.title), ''),
                (
                    SELECT m.message
                    FROM conversation_messages m
                    WHERE m.tenant_id = c.tenant_id
                      AND m.user_id = c.user_id
                      AND m.conversation_id = c.conversation_id
                      AND m.role = 'user'
                    ORDER BY m.created_at ASC, m.message_id ASC
                    LIMIT 1
                ),
                (
                    SELECT m.message
                    FROM conversation_messages m
                    WHERE m.tenant_id = c.tenant_id
                      AND m.user_id = c.user_id
                      AND m.conversation_id = c.conversation_id
                    ORDER BY m.created_at ASC, m.message_id ASC
                    LIMIT 1
                ),
                'New conversation'
            ),
            120
        )
        WHERE c.title IS NULL OR TRIM(c.title) = '';

        UPDATE conversations
        SET last_read_at = COALESCE(last_read_at, updated_at, NOW())
        WHERE last_read_at IS NULL;

        CREATE INDEX IF NOT EXISTS idx_conversation_messages_lookup
            ON conversation_messages(tenant_id, user_id, conversation_id, created_at ASC, message_id ASC);
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def list_conversations(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        c.conversation_id,
                        c.title,
                        (
                            SELECT COUNT(*)
                            FROM conversation_messages m
                            WHERE m.tenant_id = c.tenant_id
                              AND m.user_id = c.user_id
                              AND m.conversation_id = c.conversation_id
                        ) AS message_count,
                        c.updated_at,
                        EXISTS (
                            SELECT 1
                            FROM conversation_messages mx
                            WHERE mx.tenant_id = c.tenant_id
                              AND mx.user_id = c.user_id
                              AND mx.conversation_id = c.conversation_id
                              AND mx.role = 'assistant'
                              AND mx.created_at > COALESCE(c.last_read_at, c.created_at)
                        ) AS has_unread
                    FROM conversations c
                    WHERE c.tenant_id = %s AND c.user_id = %s AND c.visible = TRUE
                    ORDER BY c.updated_at DESC
                    """,
                    (tenant_id, user_id),
                )
                rows = cur.fetchall()
            conn.commit()

        return [
            {
                "conversation_id": row[0],
                "title": _normalize_title(row[1]) or "New conversation",
                "message_count": int(row[2] or 0),
                "updated_at": _to_iso(row[3]),
                "has_unread": bool(row[4]),
            }
            for row in rows
        ]

    def get_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        mark_read: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            if mark_read:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE conversations
                        SET last_read_at = NOW()
                        WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s AND visible = TRUE
                        """,
                        (tenant_id, user_id, conversation_id),
                    )
            payload = self._fetch_conversation(conn, tenant_id, user_id, conversation_id, include_hidden=False)
            conn.commit()
        return payload

    def append_message(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        message: str,
        role: str,
        content: Optional[List[Dict[str, Any]]] = None,
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_display_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_role = _normalize_role(role)
        normalized_content = _normalize_content_blocks(content)
        if not normalized_content:
            normalized_content = _content_from_legacy_message(role=normalized_role, message=message)
        message_text = _text_from_content(normalized_content) or str(message or "").strip()
        message_id = uuid.uuid4().hex
        normalized_model_id = str(model_id).strip() if model_id is not None else None
        normalized_model_name = str(model_name).strip() if model_name is not None else None
        normalized_model_display_name = str(model_display_name).strip() if model_display_name is not None else None
        normalized_provider = str(provider).strip().lower() if provider is not None else None
        title_candidate = _normalize_title(message_text) if normalized_role == "user" else None
        is_user_message = normalized_role == "user"

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversations (
                        tenant_id, user_id, conversation_id, title, updated_at, last_read_at, visible, hidden_at
                    )
                    VALUES (%s, %s, %s, %s, NOW(), CASE WHEN %s THEN NOW() ELSE NULL END, TRUE, NULL)
                    ON CONFLICT (tenant_id, user_id, conversation_id)
                    DO UPDATE SET
                        updated_at = EXCLUDED.updated_at,
                        visible = TRUE,
                        hidden_at = NULL,
                        last_read_at = CASE
                            WHEN %s THEN NOW()
                            ELSE conversations.last_read_at
                        END,
                        title = CASE
                            WHEN conversations.title IS NULL OR TRIM(conversations.title) = ''
                                THEN COALESCE(EXCLUDED.title, conversations.title)
                            ELSE conversations.title
                        END
                    """,
                    (tenant_id, user_id, conversation_id, title_candidate, is_user_message, is_user_message),
                )
                cur.execute(
                    """
                    INSERT INTO conversation_messages (
                        message_id, tenant_id, user_id, conversation_id, role, message,
                        content_json, model_id, model_name, model_display_name, provider, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, NOW())
                    """,
                    (
                        message_id,
                        tenant_id,
                        user_id,
                        conversation_id,
                        normalized_role,
                        message_text,
                        json.dumps(normalized_content, ensure_ascii=False),
                        normalized_model_id,
                        normalized_model_name,
                        normalized_model_display_name,
                        normalized_provider,
                    ),
                )

            payload = self._fetch_conversation(conn, tenant_id, user_id, conversation_id)
            conn.commit()

        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to load conversation after append.",
            )
        return payload

    def replace_conversation(self, conversation: Dict[str, Any]) -> None:
        tenant_id = str(conversation.get("tenant_id") or "").strip()
        user_id = str(conversation.get("user_id") or "").strip()
        conversation_id = str(conversation.get("conversation_id") or "").strip()
        if not tenant_id or not user_id or not conversation_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid conversation payload for postgres replace.",
            )

        updated_at = _parse_iso_datetime(conversation.get("updated_at"))
        raw_last_read_at = conversation.get("last_read_at")
        if raw_last_read_at is None:
            last_read_at = updated_at
        else:
            last_read_at = _parse_iso_datetime(raw_last_read_at)
        raw_messages = conversation.get("messages", [])
        messages = [item for item in raw_messages if isinstance(item, dict)]
        normalized_title = _normalize_title(conversation.get("title"))
        if normalized_title is None:
            normalized_title = _title_from_messages([_normalized_message(item) for item in messages]) or "New conversation"

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversations (
                        tenant_id, user_id, conversation_id, title, updated_at, last_read_at, visible, hidden_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, NULL)
                    ON CONFLICT (tenant_id, user_id, conversation_id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        updated_at = EXCLUDED.updated_at,
                        last_read_at = EXCLUDED.last_read_at,
                        visible = TRUE,
                        hidden_at = NULL
                    """,
                    (tenant_id, user_id, conversation_id, normalized_title, updated_at, last_read_at),
                )

                cur.execute(
                    """
                    DELETE FROM conversation_messages
                    WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                    """,
                    (tenant_id, user_id, conversation_id),
                )

                for item in messages:
                    normalized = _normalized_message(item)
                    message_id = normalized["message_id"].strip() or uuid.uuid4().hex
                    role = _normalize_role(normalized["role"])
                    message_text = normalized["message"]
                    content_value = normalized.get("content")
                    normalized_content = _normalize_content_blocks(content_value)
                    if not normalized_content:
                        normalized_content = _content_from_legacy_message(role=role, message=message_text)
                    normalized_message_text = _text_from_content(normalized_content) or message_text
                    created_at = _parse_iso_datetime(normalized["created_at"])
                    model_id = (
                        str(normalized.get("model_id")).strip()
                        if normalized.get("model_id") is not None
                        else None
                    )
                    model_name = (
                        str(normalized.get("model_name")).strip()
                        if normalized.get("model_name") is not None
                        else None
                    )
                    model_display_name = (
                        str(normalized.get("model_display_name")).strip()
                        if normalized.get("model_display_name") is not None
                        else None
                    )
                    provider = (
                        str(normalized.get("provider")).strip().lower()
                        if normalized.get("provider") is not None
                        else None
                    )

                    cur.execute(
                        """
                        INSERT INTO conversation_messages (
                            message_id, tenant_id, user_id, conversation_id, role, message,
                            content_json, model_id, model_name, model_display_name, provider, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                        """,
                        (
                            message_id,
                            tenant_id,
                            user_id,
                            conversation_id,
                            role,
                            normalized_message_text,
                            json.dumps(normalized_content, ensure_ascii=False),
                            model_id,
                            model_name,
                            model_display_name,
                            provider,
                            created_at,
                        ),
                    )

            conn.commit()

    def hide_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE conversations
                    SET visible = FALSE, hidden_at = NOW(), updated_at = NOW()
                    WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                    """,
                    (tenant_id, user_id, conversation_id),
                )
                hidden = bool(cur.rowcount and cur.rowcount > 0)
            conn.commit()
        return hidden

    def update_title(self, tenant_id: str, user_id: str, conversation_id: str, title: str) -> Optional[str]:
        normalized_title = _normalize_title(title)
        if normalized_title is None:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE conversations
                    SET title = %s, updated_at = NOW()
                    WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s AND visible = TRUE
                    RETURNING title
                    """,
                    (normalized_title, tenant_id, user_id, conversation_id),
                )
                row = cur.fetchone()
            conn.commit()
        if row is None:
            return None
        return _normalize_title(row[0]) or normalized_title

    def mark_conversation_read(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE conversations
                    SET last_read_at = NOW()
                    WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s AND visible = TRUE
                    """,
                    (tenant_id, user_id, conversation_id),
                )
                updated = bool(cur.rowcount and cur.rowcount > 0)
            conn.commit()
        return updated

    def _fetch_conversation(
        self,
        conn: Any,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        include_hidden: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with conn.cursor() as cur:
            if include_hidden:
                cur.execute(
                    """
                    SELECT conversation_id, title, updated_at, last_read_at
                    FROM conversations
                    WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                    """,
                    (tenant_id, user_id, conversation_id),
                )
            else:
                cur.execute(
                    """
                    SELECT conversation_id, title, updated_at, last_read_at
                    FROM conversations
                    WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s AND visible = TRUE
                    """,
                    (tenant_id, user_id, conversation_id),
                )
            header = cur.fetchone()
            if header is None:
                return None

            cur.execute(
                """
                SELECT message_id, role, message, content_json, model_id, model_name, model_display_name, provider, created_at
                FROM conversation_messages
                WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                ORDER BY created_at ASC, message_id ASC
                """,
                (tenant_id, user_id, conversation_id),
            )
            rows = cur.fetchall()

        messages = [
            _normalized_message(
                {
                    "message_id": row[0],
                    "role": row[1],
                    "message": row[2],
                    "content": row[3],
                    "model_id": row[4],
                    "model_name": row[5],
                    "model_display_name": row[6],
                    "provider": row[7],
                    "created_at": _to_iso(row[8]),
                }
            )
            for row in rows
        ]

        return {
            "conversation_id": header[0],
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": _normalize_title(header[1]) or (_title_from_messages(messages) or "New conversation"),
            "messages": messages,
            "updated_at": _to_iso(header[2]),
            "last_read_at": _to_iso(header[3]) if header[3] is not None else None,
            "has_unread": _has_unread_assistant_messages(messages, _to_iso(header[3]) if header[3] is not None else None),
        }


class RedisHotConversationStore:
    def __init__(self) -> None:
        self._client = None

    def _redis_url(self) -> str:
        return os.getenv("CONVERSATION_HYBRID_REDIS_URL", "redis://valkey:6379/0").strip()

    def _prefix(self) -> str:
        value = os.getenv("CONVERSATION_HYBRID_REDIS_PREFIX", "conversation_hot").strip()
        return value or "conversation_hot"

    def _client_or_raise(self):
        if redis is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="redis package is required when CONVERSATION_STORE_BACKEND=hybrid.",
            )

        if self._client is None:
            try:
                self._client = redis.Redis.from_url(self._redis_url(), decode_responses=True)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to initialize Redis/Valkey client.",
                )
        return self._client

    def init(self) -> None:
        client = self._client_or_raise()
        try:
            client.ping()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to Redis/Valkey for hybrid conversation store.",
            )

    def _conversation_key(self, tenant_id: str, user_id: str, conversation_id: str) -> str:
        return f"{self._prefix()}:conversation:{tenant_id}:{user_id}:{conversation_id}"

    def _user_index_key(self, tenant_id: str, user_id: str) -> str:
        return f"{self._prefix()}:user:{tenant_id}:{user_id}:conversations"

    def _active_index_key(self) -> str:
        return f"{self._prefix()}:active"

    def _serialize(
        self,
        conversation: Dict[str, Any],
        *,
        dirty: bool,
        last_activity_epoch: float,
    ) -> str:
        normalized_messages = [
            _normalized_message(item) for item in conversation.get("messages", []) if isinstance(item, dict)
        ]
        payload = {
            "conversation_id": str(conversation.get("conversation_id") or ""),
            "tenant_id": str(conversation.get("tenant_id") or ""),
            "user_id": str(conversation.get("user_id") or ""),
            "title": _resolve_conversation_title(conversation),
            "messages": normalized_messages,
            "updated_at": str(conversation.get("updated_at") or _now_iso()),
            "last_read_at": conversation.get("last_read_at"),
            "_dirty": bool(dirty),
            "_last_activity_epoch": float(last_activity_epoch),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _deserialize(self, raw: str) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(raw)
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        messages = payload.get("messages")
        if not isinstance(messages, list):
            messages = []

        raw_last_activity_epoch = payload.get("_last_activity_epoch")
        try:
            last_activity_epoch = float(raw_last_activity_epoch)
        except (TypeError, ValueError):
            last_activity_epoch = time.time()

        normalized = {
            "conversation_id": str(payload.get("conversation_id") or "").strip(),
            "tenant_id": str(payload.get("tenant_id") or "").strip(),
            "user_id": str(payload.get("user_id") or "").strip(),
            "title": _normalize_title(payload.get("title")),
            "messages": [_normalized_message(item) for item in messages if isinstance(item, dict)],
            "updated_at": str(payload.get("updated_at") or _now_iso()),
            "last_read_at": payload.get("last_read_at"),
            "_dirty": bool(payload.get("_dirty", False)),
            "_last_activity_epoch": last_activity_epoch,
        }
        if not normalized["conversation_id"] or not normalized["tenant_id"] or not normalized["user_id"]:
            return None
        return normalized

    def _public_payload(self, cached: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "conversation_id": cached["conversation_id"],
            "tenant_id": cached["tenant_id"],
            "user_id": cached["user_id"],
            "title": _resolve_conversation_title(cached),
            "messages": [_normalized_message(item) for item in cached.get("messages", [])],
            "updated_at": cached["updated_at"],
            "last_read_at": cached.get("last_read_at"),
        }

    def cache_conversation(
        self,
        conversation: Dict[str, Any],
        *,
        dirty: bool,
        last_activity_epoch: Optional[float] = None,
    ) -> None:
        tenant_id = str(conversation.get("tenant_id") or "").strip()
        user_id = str(conversation.get("user_id") or "").strip()
        conversation_id = str(conversation.get("conversation_id") or "").strip()
        if not tenant_id or not user_id or not conversation_id:
            return

        updated_at = str(conversation.get("updated_at") or _now_iso())
        updated_at_epoch = _to_epoch(updated_at)
        activity_epoch = time.time() if last_activity_epoch is None else float(last_activity_epoch)

        record = {
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": _resolve_conversation_title(conversation),
            "messages": [_normalized_message(item) for item in conversation.get("messages", []) if isinstance(item, dict)],
            "updated_at": updated_at,
            "last_read_at": conversation.get("last_read_at"),
        }

        serialized = self._serialize(record, dirty=dirty, last_activity_epoch=activity_epoch)
        conversation_key = self._conversation_key(tenant_id, user_id, conversation_id)
        user_index = self._user_index_key(tenant_id, user_id)
        active_index = self._active_index_key()

        client = self._client_or_raise()
        with client.pipeline() as pipe:
            pipe.set(conversation_key, serialized)
            pipe.zadd(user_index, {conversation_id: updated_at_epoch})
            pipe.zadd(active_index, {conversation_key: activity_epoch})
            pipe.execute()

    def get_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        mark_read: bool = False,
    ) -> Optional[Dict[str, Any]]:
        conversation_key = self._conversation_key(tenant_id, user_id, conversation_id)
        client = self._client_or_raise()
        raw = client.get(conversation_key)
        if raw is None:
            return None

        cached = self._deserialize(raw)
        if cached is None:
            self.delete_cached_conversation(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
            return None

        if mark_read:
            cached["last_read_at"] = _now_iso()
            self.cache_conversation(cached, dirty=True, last_activity_epoch=time.time())

        return self._public_payload(cached)

    def list_conversations(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        client = self._client_or_raise()
        user_index = self._user_index_key(tenant_id, user_id)
        conversation_ids = [str(item) for item in client.zrevrange(user_index, 0, -1)]
        if not conversation_ids:
            return []

        conversation_keys = [
            self._conversation_key(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
            for conversation_id in conversation_ids
        ]
        raws = client.mget(conversation_keys)

        stale_ids: List[str] = []
        summaries: List[Dict[str, Any]] = []
        for conversation_id, raw in zip(conversation_ids, raws):
            if raw is None:
                stale_ids.append(conversation_id)
                continue

            cached = self._deserialize(raw)
            if cached is None:
                stale_ids.append(conversation_id)
                continue

            summaries.append(
                {
                    "conversation_id": cached["conversation_id"],
                    "title": _resolve_conversation_title(cached),
                    "message_count": len(cached.get("messages", [])),
                    "updated_at": cached["updated_at"],
                    "has_unread": _has_unread_assistant_messages(
                        [_normalized_message(item) for item in cached.get("messages", []) if isinstance(item, dict)],
                        cached.get("last_read_at"),
                    ),
                }
            )

        if stale_ids:
            client.zrem(user_index, *stale_ids)

        summaries.sort(key=lambda item: _to_epoch(item["updated_at"]), reverse=True)
        return summaries

    def append_message(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        message: str,
        role: str,
        content: Optional[List[Dict[str, Any]]] = None,
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_display_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_iso = _now_iso()
        now_epoch = time.time()
        normalized_role = _normalize_role(role)

        existing = self.get_conversation(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
        if existing is None:
            existing = {
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "messages": [],
                "updated_at": now_iso,
            }

        entry = _normalized_message(
            {
                "message_id": uuid.uuid4().hex,
                "role": normalized_role,
                "message": str(message),
                "content": content,
                "created_at": now_iso,
                "model_id": model_id,
                "model_name": model_name,
                "model_display_name": model_display_name,
                "provider": provider,
            }
        )

        existing_messages = [item for item in existing.get("messages", []) if isinstance(item, dict)]
        existing_messages.append(entry)
        updated = {
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": _resolve_conversation_title(
                {
                    "title": existing.get("title"),
                    "messages": existing_messages,
                }
            ),
            "messages": existing_messages,
            "updated_at": now_iso,
            "last_read_at": now_iso if normalized_role == "user" else existing.get("last_read_at"),
        }

        self.cache_conversation(updated, dirty=True, last_activity_epoch=now_epoch)
        return updated

    def update_title(self, tenant_id: str, user_id: str, conversation_id: str, title: str) -> Optional[str]:
        normalized_title = _normalize_title(title)
        if normalized_title is None:
            return None
        existing = self.get_conversation(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
        if existing is None:
            return None
        existing["title"] = normalized_title
        existing["updated_at"] = _now_iso()
        self.cache_conversation(existing, dirty=True, last_activity_epoch=time.time())
        return normalized_title

    def mark_conversation_read(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        existing = self.get_conversation(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
        if existing is None:
            return False
        existing["last_read_at"] = _now_iso()
        self.cache_conversation(existing, dirty=True, last_activity_epoch=time.time())
        return True

    def delete_cached_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> None:
        client = self._client_or_raise()
        conversation_key = self._conversation_key(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
        user_index = self._user_index_key(tenant_id=tenant_id, user_id=user_id)
        active_index = self._active_index_key()

        with client.pipeline() as pipe:
            pipe.delete(conversation_key)
            pipe.zrem(user_index, conversation_id)
            pipe.zrem(active_index, conversation_key)
            pipe.execute()

    def flush_idle_conversations(
        self,
        *,
        postgres_store: PostgresConversationStore,
        idle_seconds: int,
        batch_size: int,
    ) -> int:
        if batch_size <= 0:
            return 0

        client = self._client_or_raise()
        active_index = self._active_index_key()
        cutoff = time.time() - max(0, idle_seconds)

        conversation_keys = client.zrangebyscore(active_index, min="-inf", max=cutoff, start=0, num=batch_size)
        if not conversation_keys:
            return 0

        processed = 0
        for conversation_key in conversation_keys:
            score = client.zscore(active_index, conversation_key)
            if score is None:
                continue
            if float(score) > cutoff:
                continue

            raw = client.get(conversation_key)
            if raw is None:
                client.zrem(active_index, conversation_key)
                continue

            cached = self._deserialize(raw)
            if cached is None:
                client.delete(conversation_key)
                client.zrem(active_index, conversation_key)
                continue

            if cached.get("_dirty"):
                postgres_store.replace_conversation(self._public_payload(cached))

            self.delete_cached_conversation(
                tenant_id=cached["tenant_id"],
                user_id=cached["user_id"],
                conversation_id=cached["conversation_id"],
            )
            processed += 1

        return processed


class HybridConversationStore(ConversationStoreBackend):
    def __init__(self) -> None:
        self._cold_store = PostgresConversationStore()
        self._hot_store = RedisHotConversationStore()
        self._flush_thread: Optional[Thread] = None
        self._flush_stop = Event()
        self._flush_lock = Lock()

    def _idle_seconds(self) -> int:
        return _env_int("CONVERSATION_HYBRID_IDLE_SECONDS", default=1800, minimum=0)

    def _flush_batch_size(self) -> int:
        return _env_int("CONVERSATION_HYBRID_FLUSH_BATCH_SIZE", default=100, minimum=1)

    def _flush_interval_seconds(self) -> int:
        return _env_int("CONVERSATION_HYBRID_FLUSH_INTERVAL_SECONDS", default=30, minimum=0)

    def _write_through(self) -> bool:
        return _env_bool("CONVERSATION_HYBRID_WRITE_THROUGH", default=False)

    def init_schema(self) -> None:
        self._cold_store.init_schema()
        self._hot_store.init()

    def _flush_idle_best_effort(self) -> None:
        try:
            self.flush_idle_once()
        except Exception:
            logger.exception("Hybrid conversation idle flush failed.")

    def flush_idle_once(self) -> int:
        return self._hot_store.flush_idle_conversations(
            postgres_store=self._cold_store,
            idle_seconds=self._idle_seconds(),
            batch_size=self._flush_batch_size(),
        )

    def flush_all_hot(self) -> int:
        flushed = 0
        while True:
            count = self._hot_store.flush_idle_conversations(
                postgres_store=self._cold_store,
                idle_seconds=0,
                batch_size=self._flush_batch_size(),
            )
            if count <= 0:
                break
            flushed += count
        return flushed

    def _run_flush_loop(self, interval_seconds: int) -> None:
        while not self._flush_stop.wait(interval_seconds):
            self._flush_idle_best_effort()

    def start_background_tasks(self) -> None:
        interval_seconds = self._flush_interval_seconds()
        if interval_seconds <= 0:
            return

        with self._flush_lock:
            if self._flush_thread is not None and self._flush_thread.is_alive():
                return

            self._flush_stop.clear()
            self._flush_thread = Thread(
                target=self._run_flush_loop,
                args=(interval_seconds,),
                name="conversation-hybrid-flusher",
                daemon=True,
            )
            self._flush_thread.start()

    def stop_background_tasks(self) -> None:
        thread: Optional[Thread]
        with self._flush_lock:
            thread = self._flush_thread
            self._flush_thread = None
            self._flush_stop.set()

        if thread is not None and thread.is_alive():
            thread.join(timeout=3)

        try:
            self.flush_all_hot()
        except Exception:
            logger.exception("Failed to flush hot conversations during shutdown.")

    def _merge_summaries(
        self,
        cold_list: List[Dict[str, Any]],
        hot_list: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}

        for item in cold_list:
            conversation_id = str(item.get("conversation_id") or "").strip()
            if not conversation_id:
                continue
            merged[conversation_id] = {
                "conversation_id": conversation_id,
                "title": _normalize_title(item.get("title")) or "New conversation",
                "message_count": int(item.get("message_count", 0) or 0),
                "updated_at": str(item.get("updated_at") or _now_iso()),
                "has_unread": bool(item.get("has_unread", False)),
            }

        for item in hot_list:
            conversation_id = str(item.get("conversation_id") or "").strip()
            if not conversation_id:
                continue
            next_summary = {
                "conversation_id": conversation_id,
                "title": _normalize_title(item.get("title")) or "New conversation",
                "message_count": int(item.get("message_count", 0) or 0),
                "updated_at": str(item.get("updated_at") or _now_iso()),
                "has_unread": bool(item.get("has_unread", False)),
            }
            existing = merged.get(conversation_id)
            if existing is None:
                merged[conversation_id] = next_summary
                continue

            if _to_epoch(next_summary["updated_at"]) > _to_epoch(existing["updated_at"]):
                merged[conversation_id] = next_summary
            elif _to_epoch(next_summary["updated_at"]) == _to_epoch(existing["updated_at"]):
                existing["has_unread"] = bool(existing.get("has_unread", False) or next_summary.get("has_unread", False))

        summaries = list(merged.values())
        summaries.sort(key=lambda item: _to_epoch(item["updated_at"]), reverse=True)
        return summaries

    def list_conversations(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        self._flush_idle_best_effort()

        cold_list = self._cold_store.list_conversations(tenant_id=tenant_id, user_id=user_id)

        hot_list: List[Dict[str, Any]] = []
        try:
            hot_list = self._hot_store.list_conversations(tenant_id=tenant_id, user_id=user_id)
        except Exception:
            logger.exception("Failed to read hot conversation summaries; falling back to cold store only.")

        return self._merge_summaries(cold_list=cold_list, hot_list=hot_list)

    def get_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        mark_read: bool = False,
    ) -> Optional[Dict[str, Any]]:
        self._flush_idle_best_effort()

        try:
            hot = self._hot_store.get_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                mark_read=mark_read,
            )
            if hot is not None:
                if mark_read:
                    try:
                        self._cold_store.replace_conversation(hot)
                        self._hot_store.cache_conversation(hot, dirty=False, last_activity_epoch=time.time())
                    except Exception:
                        logger.exception("Failed to persist hot read marker to cold store.")
                return hot
        except Exception:
            logger.exception("Failed to read hot conversation; checking cold store.")

        cold = self._cold_store.get_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            mark_read=mark_read,
        )
        if cold is None:
            return None

        try:
            self._hot_store.cache_conversation(cold, dirty=False, last_activity_epoch=time.time())
        except Exception:
            logger.exception("Failed to warm conversation into hot store.")

        return cold

    def append_message(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        message: str,
        role: str,
        content: Optional[List[Dict[str, Any]]] = None,
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_display_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._flush_idle_best_effort()

        if self._write_through():
            conversation = self._cold_store.append_message(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                message=message,
                role=role,
                content=content,
                model_id=model_id,
                model_name=model_name,
                model_display_name=model_display_name,
                provider=provider,
            )
            try:
                self._hot_store.cache_conversation(conversation, dirty=False, last_activity_epoch=time.time())
            except Exception:
                logger.exception("Failed to cache write-through conversation to hot store.")
            return conversation

        try:
            hot_existing = self._hot_store.get_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if hot_existing is None:
                cold_existing = self._cold_store.get_conversation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                if cold_existing is not None:
                    self._hot_store.cache_conversation(cold_existing, dirty=False, last_activity_epoch=time.time())

            return self._hot_store.append_message(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                message=message,
                role=role,
                content=content,
                model_id=model_id,
                model_name=model_name,
                model_display_name=model_display_name,
                provider=provider,
            )
        except Exception:
            logger.exception("Hybrid hot append failed; falling back to cold append.")
            return self._cold_store.append_message(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                message=message,
                role=role,
                content=content,
                model_id=model_id,
                model_name=model_name,
                model_display_name=model_display_name,
                provider=provider,
            )

    def hide_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        self._flush_idle_best_effort()

        if not self._write_through():
            try:
                hot = self._hot_store.get_conversation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                if hot is not None:
                    self._cold_store.replace_conversation(hot)
            except Exception:
                logger.exception("Failed to persist hot conversation before hide.")

        hidden = self._cold_store.hide_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        try:
            self._hot_store.delete_cached_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.exception("Failed to remove hidden conversation from hot cache.")

        return hidden

    def update_title(self, tenant_id: str, user_id: str, conversation_id: str, title: str) -> Optional[str]:
        normalized_title = _normalize_title(title)
        if normalized_title is None:
            return None
        self._flush_idle_best_effort()

        if not self._write_through():
            try:
                hot = self._hot_store.get_conversation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                if hot is not None:
                    hot["title"] = normalized_title
                    hot["updated_at"] = _now_iso()
                    self._cold_store.replace_conversation(hot)
                    self._hot_store.cache_conversation(hot, dirty=False, last_activity_epoch=time.time())
                    return normalized_title
            except Exception:
                logger.exception("Failed to update title through hot store; falling back to cold store.")

        updated = self._cold_store.update_title(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            title=normalized_title,
        )
        if updated is None:
            return None
        try:
            cold = self._cold_store.get_conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if cold is not None:
                self._hot_store.cache_conversation(cold, dirty=False, last_activity_epoch=time.time())
        except Exception:
            logger.exception("Failed to warm updated conversation title into hot cache.")
        return updated

    def mark_conversation_read(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        marked = self.get_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            mark_read=True,
        )
        return marked is not None


_memory_store = InMemoryConversationStore()
_postgres_store = PostgresConversationStore()
_hybrid_store = HybridConversationStore()

_store_initialized = False
_store_init_lock = Lock()


def _get_store() -> ConversationStoreBackend:
    backend = _conversation_store_backend_name()
    if backend == "memory":
        return _memory_store
    if backend == "hybrid":
        return _hybrid_store
    return _postgres_store


def initialize_conversation_store() -> None:
    global _store_initialized
    if _store_initialized:
        return

    with _store_init_lock:
        if _store_initialized:
            return

        store = _get_store()
        store.init_schema()
        _store_initialized = True


def start_conversation_store_background_tasks() -> None:
    initialize_conversation_store()
    _get_store().start_background_tasks()


def shutdown_conversation_store() -> None:
    if not _store_initialized:
        return
    _get_store().stop_background_tasks()


class ConversationStore:
    def list_conversations(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        initialize_conversation_store()
        return _get_store().list_conversations(tenant_id=tenant_id, user_id=user_id)

    def get_conversation(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        mark_read: bool = False,
    ) -> Optional[Dict[str, Any]]:
        initialize_conversation_store()
        return _get_store().get_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            mark_read=mark_read,
        )

    def append_message(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        message: str,
        role: str = "user",
        content: Optional[List[Dict[str, Any]]] = None,
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_display_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        initialize_conversation_store()
        return _get_store().append_message(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            message=message,
            role=role,
            content=content,
            model_id=model_id,
            model_name=model_name,
            model_display_name=model_display_name,
            provider=provider,
        )

    def hide_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        initialize_conversation_store()
        return _get_store().hide_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    def update_title(self, tenant_id: str, user_id: str, conversation_id: str, title: str) -> Optional[str]:
        initialize_conversation_store()
        return _get_store().update_title(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            title=title,
        )

    def mark_conversation_read(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        initialize_conversation_store()
        return _get_store().mark_conversation_read(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )


conversation_store = ConversationStore()
