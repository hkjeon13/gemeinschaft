import json
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4, uuid5

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from .database import database_url_from_settings, load_database_settings
from .security_audit import emit_security_event

try:
    import psycopg
except ImportError:  # pragma: no cover - installed in runtime image
    psycopg = None


_OPENAI_CLIENT_OPTION_KEYS = {
    "organization",
    "project",
    "base_url",
    "websocket_base_url",
    "timeout",
    "max_retries",
    "default_headers",
    "default_query",
    "_strict_response_validation",
    "strict_response_validation",
}

_OPENAI_CHAT_CREATE_ALLOWED_KEYS = {
    "audio",
    "frequency_penalty",
    "function_call",
    "functions",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "metadata",
    "modalities",
    "n",
    "parallel_tool_calls",
    "prediction",
    "presence_penalty",
    "prompt_cache_key",
    "reasoning_effort",
    "response_format",
    "safety_identifier",
    "seed",
    "service_tier",
    "stop",
    "store",
    "stream_options",
    "temperature",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "user",
    "verbosity",
    "web_search_options",
    "extra_headers",
    "extra_query",
    "extra_body",
    "timeout",
}

_OPENAI_CHAT_CREATE_RESERVED_KEYS = {"messages", "model", "stream"}

_OPENAI_RESPONSES_CREATE_ALLOWED_KEYS = {
    "background",
    "conversation",
    "include",
    "instructions",
    "max_output_tokens",
    "max_tool_calls",
    "metadata",
    "parallel_tool_calls",
    "previous_response_id",
    "prompt",
    "prompt_cache_key",
    "reasoning",
    "safety_identifier",
    "service_tier",
    "store",
    "stream_options",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "truncation",
    "user",
    "extra_headers",
    "extra_query",
    "extra_body",
    "timeout",
}

_OPENAI_RESPONSES_CREATE_RESERVED_KEYS = {"input", "model", "stream"}


def _normalize_id_or_raise(model_id: str) -> str:
    value = model_id.strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model_id is required.")
    return value


def _normalize_nonempty_or_raise(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{name} is required.")
    return normalized


def _normalize_provider_or_raise(provider: str) -> str:
    return _normalize_nonempty_or_raise("provider", provider).lower()


def _json_dict_or_raise(value: Any, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a JSON object.",
        )
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} contains non-serializable values.",
        )


def _normalize_string_dict_or_raise(value: Any, field_name: str) -> Dict[str, str]:
    parsed = _json_dict_or_raise(value, field_name)
    normalized: Dict[str, str] = {}
    for key, item in parsed.items():
        if not isinstance(key, str) or not key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} keys must be non-empty strings.",
            )
        if not isinstance(item, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} values must be strings.",
            )
        normalized[key] = item
    return normalized


def _normalize_openai_client_options_or_raise(value: Any) -> Dict[str, Any]:
    raw = _json_dict_or_raise(value, "client_options")
    normalized: Dict[str, Any] = {}

    for key, item in raw.items():
        if not isinstance(key, str) or not key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client_options keys must be non-empty strings.",
            )

        normalized_key = "_strict_response_validation" if key == "strict_response_validation" else key
        if normalized_key not in _OPENAI_CLIENT_OPTION_KEYS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported client_options key '{key}' for provider openai. "
                    "Allowed keys: organization, project, base_url, websocket_base_url, "
                    "timeout, max_retries, default_headers, default_query, strict_response_validation."
                ),
            )

        if normalized_key in {"organization", "project", "base_url", "websocket_base_url"}:
            if not isinstance(item, str) or not item.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"client_options.{key} must be a non-empty string.",
                )
            normalized[normalized_key] = item.strip()
            continue

        if normalized_key == "timeout":
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="client_options.timeout must be a number.",
                )
            if float(item) <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="client_options.timeout must be greater than 0.",
                )
            normalized[normalized_key] = float(item)
            continue

        if normalized_key == "max_retries":
            if isinstance(item, bool) or not isinstance(item, int):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="client_options.max_retries must be an integer.",
                )
            if item < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="client_options.max_retries must be >= 0.",
                )
            normalized[normalized_key] = item
            continue

        if normalized_key == "default_headers":
            normalized[normalized_key] = _normalize_string_dict_or_raise(item, "client_options.default_headers")
            continue

        if normalized_key == "default_query":
            normalized[normalized_key] = _json_dict_or_raise(item, "client_options.default_query")
            continue

        if normalized_key == "_strict_response_validation":
            if not isinstance(item, bool):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="client_options.strict_response_validation must be a boolean.",
                )
            normalized[normalized_key] = item
            continue

    return normalized


def _normalize_openai_create_options_or_raise(
    field_name: str,
    value: Any,
    *,
    allowed_keys: set[str],
    reserved_keys: set[str],
) -> Dict[str, Any]:
    parsed = _json_dict_or_raise(value, field_name)
    normalized: Dict[str, Any] = {}
    reserved_used: List[str] = []
    unknown_keys: List[str] = []

    for key, item in parsed.items():
        if not isinstance(key, str) or not key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} keys must be non-empty strings.",
            )

        if key in reserved_keys:
            reserved_used.append(key)
            continue
        if key not in allowed_keys:
            unknown_keys.append(key)
            continue

        normalized[key] = item

    if reserved_used:
        names = ", ".join(sorted(reserved_used))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} includes reserved key(s): {names}",
        )

    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} includes unsupported key(s): {names}",
        )

    return normalized


def _normalize_client_options_or_raise(provider: str, value: Any) -> Dict[str, Any]:
    if provider == "openai":
        return _normalize_openai_client_options_or_raise(value)
    return _json_dict_or_raise(value, "client_options")


def _normalize_chat_create_options_or_raise(provider: str, value: Any) -> Dict[str, Any]:
    if provider == "openai":
        return _normalize_openai_create_options_or_raise(
            "chat_create_options",
            value,
            allowed_keys=_OPENAI_CHAT_CREATE_ALLOWED_KEYS,
            reserved_keys=_OPENAI_CHAT_CREATE_RESERVED_KEYS,
        )
    return _json_dict_or_raise(value, "chat_create_options")


def _normalize_responses_create_options_or_raise(provider: str, value: Any) -> Dict[str, Any]:
    if provider == "openai":
        return _normalize_openai_create_options_or_raise(
            "responses_create_options",
            value,
            allowed_keys=_OPENAI_RESPONSES_CREATE_ALLOWED_KEYS,
            reserved_keys=_OPENAI_RESPONSES_CREATE_RESERVED_KEYS,
        )
    return _json_dict_or_raise(value, "responses_create_options")


def _normalize_openai_api_or_raise(provider: str, value: Optional[str]) -> str:
    if provider != "openai":
        if value is None:
            return "chat.completions"
        candidate = value.strip()
        return candidate or "chat.completions"

    candidate = (value or "chat.completions").strip().lower()
    if candidate not in {"chat.completions", "responses"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="openai_api must be one of: chat.completions, responses.",
        )
    return candidate


def _model_registry_backend_name() -> str:
    configured = os.getenv("MODEL_REGISTRY_BACKEND", "").strip().lower()
    if configured:
        if configured not in ("postgres", "memory"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MODEL_REGISTRY_BACKEND must be 'postgres' or 'memory'.",
            )
        return configured

    settings = load_database_settings()
    return "postgres" if settings.enabled else "memory"


def _fernet_or_raise() -> Fernet:
    raw = os.getenv("MODEL_SECRET_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MODEL_SECRET_ENCRYPTION_KEY must be set to store model secrets.",
        )

    try:
        return Fernet(raw.encode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MODEL_SECRET_ENCRYPTION_KEY must be a valid Fernet key.",
        )


def _encrypt_secret_or_raise(field_name: str, value: str) -> str:
    text = value.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be non-empty.")
    return _fernet_or_raise().encrypt(text.encode("utf-8")).decode("utf-8")


def _decrypt_secret_or_raise(field_name: str, token: str) -> str:
    try:
        return _fernet_or_raise().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Stored {field_name} cannot be decrypted. "
                "Check MODEL_SECRET_ENCRYPTION_KEY."
            ),
        )


_LEGACY_API_KEY_NAMESPACE = UUID("f1eb1e2b-9127-4d7a-8dd7-2619b7ce7da3")


@dataclass
class StoredApiKey:
    key_id: str
    key_value: str


@dataclass
class ApiKeyRef:
    key_id: str
    masked_key: str


def _normalize_secret_list_or_raise(field_name: str, values: Any) -> List[str]:
    if not isinstance(values, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a list of non-empty strings.",
        )

    normalized: List[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} must be a list of non-empty strings.",
            )
        secret = item.strip()
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} must be a list of non-empty strings.",
            )
        if secret in seen:
            continue
        seen.add(secret)
        normalized.append(secret)
    return normalized


def _normalize_api_key_id_or_raise(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must contain non-empty UUID strings.",
        )
    try:
        return str(UUID(normalized))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must contain valid UUID strings.",
        )


def _normalize_api_key_id_list_or_raise(field_name: str, values: Any) -> List[str]:
    if not isinstance(values, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a list of UUID strings.",
        )
    normalized: List[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} must contain UUID strings.",
            )
        api_key_id = _normalize_api_key_id_or_raise(field_name, item)
        if api_key_id in seen:
            continue
        seen.add(api_key_id)
        normalized.append(api_key_id)
    return normalized


def _legacy_api_key_id(key_value: str) -> str:
    return str(uuid5(_LEGACY_API_KEY_NAMESPACE, key_value))


def _new_api_key_items(values: List[str]) -> List[StoredApiKey]:
    return [StoredApiKey(key_id=str(uuid4()), key_value=value) for value in values]


def _api_key_values(items: List[StoredApiKey]) -> List[str]:
    return [item.key_value for item in items]


def _mask_api_key(value: str) -> str:
    text = value.strip()
    if not text:
        return "***"
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}***{text[-2:]}"


def _api_key_refs(items: List[StoredApiKey]) -> List[ApiKeyRef]:
    return [ApiKeyRef(key_id=item.key_id, masked_key=_mask_api_key(item.key_value)) for item in items]


def _resolve_api_key_input_or_raise(*, api_key: Optional[str], api_keys: Optional[List[str]]) -> Optional[List[str]]:
    if api_key is not None and api_keys is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use either api_key or api_keys, not both.",
        )
    if api_keys is not None:
        return _normalize_secret_list_or_raise("api_keys", api_keys)
    if api_key is not None:
        return [_normalize_nonempty_or_raise("api_key", api_key)]
    return None


def _encrypt_api_keys_or_raise(api_keys: List[StoredApiKey]) -> str:
    serialized = json.dumps(
        [{"id": item.key_id, "value": item.key_value} for item in api_keys],
        ensure_ascii=False,
    )
    return _encrypt_secret_or_raise("api_key", serialized)


def _decrypt_api_keys_or_raise(token: str) -> List[StoredApiKey]:
    decrypted = _decrypt_secret_or_raise("api_key", token).strip()
    if not decrypted:
        return []

    try:
        parsed = json.loads(decrypted)
    except ValueError:
        return [StoredApiKey(key_id=_legacy_api_key_id(decrypted), key_value=decrypted)]

    if isinstance(parsed, list):
        normalized: List[StoredApiKey] = []
        seen_values: set[str] = set()
        seen_ids: set[str] = set()
        for item in parsed:
            if isinstance(item, str):
                key_value = item.strip()
                if not key_value or key_value in seen_values:
                    continue
                key_id = _legacy_api_key_id(key_value)
                seen_values.add(key_value)
                seen_ids.add(key_id)
                normalized.append(StoredApiKey(key_id=key_id, key_value=key_value))
                continue

            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Stored api_key has invalid format.",
                )

            key_value_raw = item.get("value")
            if not isinstance(key_value_raw, str):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Stored api_key has invalid format.",
                )
            key_value = key_value_raw.strip()
            if not key_value or key_value in seen_values:
                continue

            key_id_raw = item.get("id")
            if isinstance(key_id_raw, str) and key_id_raw.strip():
                try:
                    key_id = _normalize_api_key_id_or_raise("api_key.id", key_id_raw)
                except HTTPException:
                    key_id = str(uuid4())
            else:
                key_id = _legacy_api_key_id(key_value)

            if key_id in seen_ids:
                key_id = str(uuid4())

            seen_values.add(key_value)
            seen_ids.add(key_id)
            normalized.append(StoredApiKey(key_id=key_id, key_value=key_value))
        return normalized

    if isinstance(parsed, str):
        value = parsed.strip()
        if not value:
            return []
        return [StoredApiKey(key_id=_legacy_api_key_id(value), key_value=value)]
    return [StoredApiKey(key_id=_legacy_api_key_id(decrypted), key_value=decrypted)]


def _env_openai_api_keys() -> List[str]:
    env_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not env_key:
        return []
    if "\n" in env_key:
        return _normalize_secret_list_or_raise("OPENAI_API_KEY", [line for line in env_key.splitlines() if line.strip()])
    return [env_key]


@dataclass
class StoredChatModel:
    model_id: str
    provider: str
    openai_api: str
    model: str
    display_name: str
    description: str
    parameters: Dict[str, Any]
    client_options: Dict[str, Any]
    chat_create_options: Dict[str, Any]
    responses_create_options: Dict[str, Any]
    encrypted_api_key: Optional[str]
    encrypted_webhook_secret: Optional[str]
    is_active: bool
    is_default: bool
    created_at: str
    updated_at: str


@dataclass
class ChatModelRecord:
    model_id: str
    provider: str
    openai_api: str
    model: str
    display_name: str
    description: str
    parameters: Dict[str, Any]
    client_options: Dict[str, Any]
    chat_create_options: Dict[str, Any]
    responses_create_options: Dict[str, Any]
    api_key_refs: List[ApiKeyRef]
    has_api_key: bool
    has_webhook_secret: bool
    is_active: bool
    is_default: bool
    created_at: str
    updated_at: str


@dataclass
class ResolvedChatModel:
    model_id: str
    provider: str
    openai_api: str
    model: str
    display_name: str
    description: str
    parameters: Dict[str, Any]
    client_options: Dict[str, Any]
    chat_create_options: Dict[str, Any]
    responses_create_options: Dict[str, Any]
    api_key: Optional[str]
    api_keys: List[str]


class ChatModelStore:
    def init_schema(self) -> None:
        raise NotImplementedError

    def count_models(self) -> int:
        raise NotImplementedError

    def list_models(self) -> List[StoredChatModel]:
        raise NotImplementedError

    def get_model(self, model_id: str) -> Optional[StoredChatModel]:
        raise NotImplementedError

    def upsert_model(self, model: StoredChatModel) -> None:
        raise NotImplementedError

    def delete_model(self, model_id: str) -> bool:
        raise NotImplementedError


class InMemoryChatModelStore(ChatModelStore):
    def __init__(self) -> None:
        self._lock = Lock()
        self._models: Dict[str, StoredChatModel] = {}

    def init_schema(self) -> None:
        return

    def count_models(self) -> int:
        with self._lock:
            return len(self._models)

    def list_models(self) -> List[StoredChatModel]:
        with self._lock:
            models = list(self._models.values())
        models.sort(key=lambda item: item.model_id)
        return [self._copy(item) for item in models]

    def get_model(self, model_id: str) -> Optional[StoredChatModel]:
        with self._lock:
            existing = self._models.get(model_id)
            if existing is None:
                return None
            return self._copy(existing)

    def upsert_model(self, model: StoredChatModel) -> None:
        with self._lock:
            self._models[model.model_id] = self._copy(model)

    def delete_model(self, model_id: str) -> bool:
        with self._lock:
            return self._models.pop(model_id, None) is not None

    def _copy(self, item: StoredChatModel) -> StoredChatModel:
        return StoredChatModel(
            model_id=item.model_id,
            provider=item.provider,
            openai_api=item.openai_api,
            model=item.model,
            display_name=item.display_name,
            description=item.description,
            parameters=dict(item.parameters),
            client_options=dict(item.client_options),
            chat_create_options=dict(item.chat_create_options),
            responses_create_options=dict(item.responses_create_options),
            encrypted_api_key=item.encrypted_api_key,
            encrypted_webhook_secret=item.encrypted_webhook_secret,
            is_active=item.is_active,
            is_default=item.is_default,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class PostgresChatModelStore(ChatModelStore):
    def _dsn(self) -> str:
        settings = load_database_settings()
        if not settings.enabled:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DATABASE_ENABLED must be true when MODEL_REGISTRY_BACKEND=postgres.",
            )
        return database_url_from_settings(settings)

    def _connect(self):
        if psycopg is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="psycopg is required for postgres model registry.",
            )
        try:
            return psycopg.connect(self._dsn())
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to Postgres for model registry.",
            )

    def init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS chat_models (
            model_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            client_type TEXT NOT NULL DEFAULT 'openai',
            openai_api TEXT NOT NULL DEFAULT 'chat.completions',
            model_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
            client_options JSONB NOT NULL DEFAULT '{}'::jsonb,
            chat_create_options JSONB NOT NULL DEFAULT '{}'::jsonb,
            responses_create_options JSONB NOT NULL DEFAULT '{}'::jsonb,
            encrypted_api_key TEXT,
            encrypted_webhook_secret TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        ALTER TABLE chat_models
            ADD COLUMN IF NOT EXISTS client_type TEXT NOT NULL DEFAULT 'openai';

        ALTER TABLE chat_models
            ADD COLUMN IF NOT EXISTS openai_api TEXT NOT NULL DEFAULT 'chat.completions';

        ALTER TABLE chat_models
            ADD COLUMN IF NOT EXISTS client_options JSONB NOT NULL DEFAULT '{}'::jsonb;

        ALTER TABLE chat_models
            ADD COLUMN IF NOT EXISTS chat_create_options JSONB NOT NULL DEFAULT '{}'::jsonb;

        ALTER TABLE chat_models
            ADD COLUMN IF NOT EXISTS responses_create_options JSONB NOT NULL DEFAULT '{}'::jsonb;

        ALTER TABLE chat_models
            ADD COLUMN IF NOT EXISTS encrypted_webhook_secret TEXT;
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def count_models(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chat_models")
                count = int(cur.fetchone()[0])
            conn.commit()
        return count

    def list_models(self) -> List[StoredChatModel]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_id, provider, openai_api, model_name, display_name, description,
                           parameters, client_options, chat_create_options, responses_create_options,
                           encrypted_api_key, encrypted_webhook_secret,
                           is_active, is_default, created_at, updated_at
                    FROM chat_models
                    ORDER BY model_id ASC
                    """
                )
                rows = cur.fetchall()
            conn.commit()

        return [self._from_row(row) for row in rows]

    def get_model(self, model_id: str) -> Optional[StoredChatModel]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_id, provider, openai_api, model_name, display_name, description,
                           parameters, client_options, chat_create_options, responses_create_options,
                           encrypted_api_key, encrypted_webhook_secret,
                           is_active, is_default, created_at, updated_at
                    FROM chat_models
                    WHERE model_id = %s
                    """,
                    (model_id,),
                )
                row = cur.fetchone()
            conn.commit()

        if row is None:
            return None
        return self._from_row(row)

    def upsert_model(self, model: StoredChatModel) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_models (
                        model_id, provider, client_type, openai_api, model_name, display_name, description,
                        parameters, client_options, chat_create_options, responses_create_options,
                        encrypted_api_key, encrypted_webhook_secret,
                        is_active, is_default, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s,
                        %s, %s, %s::timestamptz, %s::timestamptz
                    )
                    ON CONFLICT (model_id)
                    DO UPDATE SET
                        provider = EXCLUDED.provider,
                        client_type = EXCLUDED.client_type,
                        openai_api = EXCLUDED.openai_api,
                        model_name = EXCLUDED.model_name,
                        display_name = EXCLUDED.display_name,
                        description = EXCLUDED.description,
                        parameters = EXCLUDED.parameters,
                        client_options = EXCLUDED.client_options,
                        chat_create_options = EXCLUDED.chat_create_options,
                        responses_create_options = EXCLUDED.responses_create_options,
                        encrypted_api_key = EXCLUDED.encrypted_api_key,
                        encrypted_webhook_secret = EXCLUDED.encrypted_webhook_secret,
                        is_active = EXCLUDED.is_active,
                        is_default = EXCLUDED.is_default,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        model.model_id,
                        model.provider,
                        model.provider,
                        model.openai_api,
                        model.model,
                        model.display_name,
                        model.description,
                        json.dumps(model.parameters),
                        json.dumps(model.client_options),
                        json.dumps(model.chat_create_options),
                        json.dumps(model.responses_create_options),
                        model.encrypted_api_key,
                        model.encrypted_webhook_secret,
                        model.is_active,
                        model.is_default,
                        model.created_at,
                        model.updated_at,
                    ),
                )
            conn.commit()

    def delete_model(self, model_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_models WHERE model_id = %s", (model_id,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def _from_row(self, row: Any) -> StoredChatModel:
        parameters = row[6] if isinstance(row[6], dict) else {}
        client_options = row[7] if isinstance(row[7], dict) else {}
        chat_create_options = row[8] if isinstance(row[8], dict) else {}
        responses_create_options = row[9] if isinstance(row[9], dict) else {}
        return StoredChatModel(
            model_id=row[0],
            provider=row[1],
            openai_api=row[2] or "chat.completions",
            model=row[3],
            display_name=row[4],
            description=row[5] or "",
            parameters=_json_dict_or_raise(parameters, "parameters"),
            client_options=_json_dict_or_raise(client_options, "client_options"),
            chat_create_options=_json_dict_or_raise(chat_create_options, "chat_create_options"),
            responses_create_options=_json_dict_or_raise(responses_create_options, "responses_create_options"),
            encrypted_api_key=row[10],
            encrypted_webhook_secret=row[11],
            is_active=bool(row[12]),
            is_default=bool(row[13]),
            created_at=row[14].isoformat().replace("+00:00", "Z") if hasattr(row[14], "isoformat") else str(row[14]),
            updated_at=row[15].isoformat().replace("+00:00", "Z") if hasattr(row[15], "isoformat") else str(row[15]),
        )


_memory_store = InMemoryChatModelStore()
_postgres_store = PostgresChatModelStore()
_store_initialized = False
_store_init_lock = Lock()


def _get_store() -> ChatModelStore:
    backend = _model_registry_backend_name()
    if backend == "memory":
        return _memory_store
    return _postgres_store


def _seed_default_model_if_empty() -> None:
    store = _get_store()
    if store.count_models() > 0:
        return

    default_model_id = _normalize_id_or_raise(os.getenv("OPENAI_DEFAULT_MODEL_ID", "default"))
    default_model_name = _normalize_nonempty_or_raise("model", os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"))
    now = _current_ts()
    store.upsert_model(
        StoredChatModel(
            model_id=default_model_id,
            provider="openai",
            openai_api="chat.completions",
            model=default_model_name,
            display_name=default_model_name,
            description="Default chat model.",
            parameters={},
            client_options={},
            chat_create_options={},
            responses_create_options={},
            encrypted_api_key=None,
            encrypted_webhook_secret=None,
            is_active=True,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
    )


def initialize_chat_model_registry() -> None:
    global _store_initialized
    if _store_initialized:
        return

    with _store_init_lock:
        if _store_initialized:
            return

        store = _get_store()
        store.init_schema()
        _seed_default_model_if_empty()
        _store_initialized = True


def _current_ts() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_public(item: StoredChatModel) -> ChatModelRecord:
    api_key_items = _decrypt_api_keys_or_raise(item.encrypted_api_key) if item.encrypted_api_key else []
    return ChatModelRecord(
        model_id=item.model_id,
        provider=item.provider,
        openai_api=item.openai_api,
        model=item.model,
        display_name=item.display_name,
        description=item.description,
        parameters=dict(item.parameters),
        client_options=dict(item.client_options),
        chat_create_options=dict(item.chat_create_options),
        responses_create_options=dict(item.responses_create_options),
        api_key_refs=_api_key_refs(api_key_items),
        has_api_key=bool(api_key_items),
        has_webhook_secret=bool(item.encrypted_webhook_secret),
        is_active=item.is_active,
        is_default=item.is_default,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _all_models() -> List[StoredChatModel]:
    return _get_store().list_models()


def _set_default_model(model_id: str) -> None:
    store = _get_store()
    models = _all_models()
    now = _current_ts()
    for item in models:
        should_default = item.model_id == model_id
        if item.is_default == should_default:
            continue
        store.upsert_model(
            StoredChatModel(
                model_id=item.model_id,
                provider=item.provider,
                openai_api=item.openai_api,
                model=item.model,
                display_name=item.display_name,
                description=item.description,
                parameters=dict(item.parameters),
                client_options=dict(item.client_options),
                chat_create_options=dict(item.chat_create_options),
                responses_create_options=dict(item.responses_create_options),
                encrypted_api_key=item.encrypted_api_key,
                encrypted_webhook_secret=item.encrypted_webhook_secret,
                is_active=item.is_active,
                is_default=should_default,
                created_at=item.created_at,
                updated_at=now,
            )
        )


def list_chat_models() -> List[ChatModelRecord]:
    initialize_chat_model_registry()
    items = [_to_public(item) for item in _all_models()]
    items.sort(key=lambda item: (not item.is_default, item.model_id))
    return items


def get_chat_model(model_id: str) -> Optional[ChatModelRecord]:
    initialize_chat_model_registry()
    stored = _get_store().get_model(_normalize_id_or_raise(model_id))
    if stored is None:
        return None
    return _to_public(stored)


def create_chat_model(
    *,
    model_id: str,
    provider: str,
    openai_api: str,
    model: str,
    display_name: Optional[str],
    description: str,
    parameters: Dict[str, Any],
    client_options: Dict[str, Any],
    chat_create_options: Dict[str, Any],
    responses_create_options: Dict[str, Any],
    api_key: Optional[str],
    api_keys: Optional[List[str]],
    webhook_secret: Optional[str],
    is_active: bool,
    is_default: bool,
) -> ChatModelRecord:
    initialize_chat_model_registry()
    store = _get_store()
    normalized_id = _normalize_id_or_raise(model_id)
    if store.get_model(normalized_id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Model already exists.")

    normalized_provider = _normalize_provider_or_raise(provider)
    normalized_openai_api = _normalize_openai_api_or_raise(normalized_provider, openai_api)
    normalized_model = _normalize_nonempty_or_raise("model", model)
    normalized_display_name = (display_name or normalized_model).strip() or normalized_model
    normalized_description = description.strip()
    normalized_params = _json_dict_or_raise(parameters, "parameters")
    normalized_client_options = _normalize_client_options_or_raise(normalized_provider, client_options)
    normalized_chat_create_options = _normalize_chat_create_options_or_raise(normalized_provider, chat_create_options)
    normalized_responses_create_options = _normalize_responses_create_options_or_raise(
        normalized_provider,
        responses_create_options,
    )
    requested_api_keys = _resolve_api_key_input_or_raise(api_key=api_key, api_keys=api_keys)
    requested_api_key_items = _new_api_key_items(requested_api_keys) if requested_api_keys else []
    encrypted_api_key = _encrypt_api_keys_or_raise(requested_api_key_items) if requested_api_key_items else None
    encrypted_webhook_secret = (
        _encrypt_secret_or_raise("webhook_secret", webhook_secret) if webhook_secret is not None else None
    )
    now = _current_ts()

    stored = StoredChatModel(
        model_id=normalized_id,
        provider=normalized_provider,
        openai_api=normalized_openai_api,
        model=normalized_model,
        display_name=normalized_display_name,
        description=normalized_description,
        parameters=normalized_params,
        client_options=normalized_client_options,
        chat_create_options=normalized_chat_create_options,
        responses_create_options=normalized_responses_create_options,
        encrypted_api_key=encrypted_api_key,
        encrypted_webhook_secret=encrypted_webhook_secret,
        is_active=bool(is_active),
        is_default=bool(is_default),
        created_at=now,
        updated_at=now,
    )
    store.upsert_model(stored)

    if is_default:
        _set_default_model(normalized_id)
    else:
        has_default = any(item.is_default for item in _all_models())
        if not has_default:
            _set_default_model(normalized_id)

    emit_security_event(
        event_type="admin_model_created",
        outcome="allow",
        model_id=normalized_id,
        provider=normalized_provider,
        openai_api=normalized_openai_api,
        is_default=bool(is_default),
    )
    created = store.get_model(normalized_id)
    if created is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create model.")
    return _to_public(created)


def update_chat_model(
    *,
    model_id: str,
    provider: Optional[str] = None,
    openai_api: Optional[str] = None,
    model: Optional[str] = None,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    client_options: Optional[Dict[str, Any]] = None,
    chat_create_options: Optional[Dict[str, Any]] = None,
    responses_create_options: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    api_keys: Optional[List[str]] = None,
    append_api_keys: Optional[List[str]] = None,
    remove_api_key_ids: Optional[List[str]] = None,
    clear_api_key: Optional[bool] = None,
    webhook_secret: Optional[str] = None,
    clear_webhook_secret: Optional[bool] = None,
    is_active: Optional[bool] = None,
    is_default: Optional[bool] = None,
) -> ChatModelRecord:
    initialize_chat_model_registry()
    store = _get_store()
    normalized_id = _normalize_id_or_raise(model_id)
    existing = store.get_model(normalized_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")

    next_provider = existing.provider if provider is None else _normalize_provider_or_raise(provider)
    raw_openai_api = existing.openai_api if openai_api is None else openai_api
    next_openai_api = _normalize_openai_api_or_raise(next_provider, raw_openai_api)

    next_model = existing.model if model is None else _normalize_nonempty_or_raise("model", model)
    next_display_name = existing.display_name if display_name is None else display_name.strip()
    if not next_display_name:
        next_display_name = next_model
    next_description = existing.description if description is None else description.strip()
    next_parameters = existing.parameters if parameters is None else _json_dict_or_raise(parameters, "parameters")

    if client_options is None:
        next_client_options = _normalize_client_options_or_raise(next_provider, existing.client_options)
    else:
        next_client_options = _normalize_client_options_or_raise(next_provider, client_options)

    if chat_create_options is None:
        next_chat_create_options = _normalize_chat_create_options_or_raise(next_provider, existing.chat_create_options)
    else:
        next_chat_create_options = _normalize_chat_create_options_or_raise(next_provider, chat_create_options)

    if responses_create_options is None:
        next_responses_create_options = _normalize_responses_create_options_or_raise(
            next_provider,
            existing.responses_create_options,
        )
    else:
        next_responses_create_options = _normalize_responses_create_options_or_raise(
            next_provider,
            responses_create_options,
        )

    next_api_key_items = _decrypt_api_keys_or_raise(existing.encrypted_api_key) if existing.encrypted_api_key else []
    if clear_api_key is True:
        next_api_key_items = []
    requested_api_keys = _resolve_api_key_input_or_raise(api_key=api_key, api_keys=api_keys)
    if requested_api_keys is not None:
        next_api_key_items = _new_api_key_items(requested_api_keys)
    append_requested_api_keys = (
        _normalize_secret_list_or_raise("append_api_keys", append_api_keys)
        if append_api_keys is not None
        else None
    )
    if append_requested_api_keys:
        seen_api_keys = {item.key_value for item in next_api_key_items}
        for item in append_requested_api_keys:
            if item in seen_api_keys:
                continue
            seen_api_keys.add(item)
            next_api_key_items.append(StoredApiKey(key_id=str(uuid4()), key_value=item))

    remove_requested_api_key_ids = (
        _normalize_api_key_id_list_or_raise("remove_api_key_ids", remove_api_key_ids)
        if remove_api_key_ids is not None
        else []
    )
    if remove_requested_api_key_ids:
        remove_id_set = set(remove_requested_api_key_ids)
        known_ids = {item.key_id for item in next_api_key_items}
        unknown_ids = sorted(remove_id_set - known_ids)
        if unknown_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown api_key id(s): {', '.join(unknown_ids)}",
            )
        next_api_key_items = [item for item in next_api_key_items if item.key_id not in remove_id_set]

    next_encrypted_api_key = _encrypt_api_keys_or_raise(next_api_key_items) if next_api_key_items else None

    next_encrypted_webhook_secret = existing.encrypted_webhook_secret
    if clear_webhook_secret is True:
        next_encrypted_webhook_secret = None
    if webhook_secret is not None:
        next_encrypted_webhook_secret = _encrypt_secret_or_raise("webhook_secret", webhook_secret)

    next_active = existing.is_active if is_active is None else bool(is_active)
    next_default = existing.is_default if is_default is None else bool(is_default)
    now = _current_ts()

    store.upsert_model(
        StoredChatModel(
            model_id=existing.model_id,
            provider=next_provider,
            openai_api=next_openai_api,
            model=next_model,
            display_name=next_display_name,
            description=next_description,
            parameters=next_parameters,
            client_options=next_client_options,
            chat_create_options=next_chat_create_options,
            responses_create_options=next_responses_create_options,
            encrypted_api_key=next_encrypted_api_key,
            encrypted_webhook_secret=next_encrypted_webhook_secret,
            is_active=next_active,
            is_default=next_default,
            created_at=existing.created_at,
            updated_at=now,
        )
    )

    if next_default:
        _set_default_model(existing.model_id)
    else:
        has_default = any(item.is_default for item in _all_models())
        if not has_default:
            _set_default_model(existing.model_id)

    emit_security_event(
        event_type="admin_model_updated",
        outcome="allow",
        model_id=existing.model_id,
        provider=next_provider,
        openai_api=next_openai_api,
        is_default=next_default,
    )
    updated = store.get_model(existing.model_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update model.")
    return _to_public(updated)


def delete_chat_model(model_id: str) -> None:
    initialize_chat_model_registry()
    store = _get_store()
    normalized_id = _normalize_id_or_raise(model_id)
    existing = store.get_model(normalized_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")

    models = _all_models()
    if len(models) <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the last model.")

    store.delete_model(normalized_id)
    if existing.is_default:
        remaining = _all_models()
        if remaining:
            _set_default_model(remaining[0].model_id)

    emit_security_event(
        event_type="admin_model_deleted",
        outcome="allow",
        model_id=normalized_id,
    )


def resolve_chat_model(model_id: Optional[str] = None) -> ResolvedChatModel:
    initialize_chat_model_registry()
    models = _all_models()
    if not models:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No chat model is configured.",
        )

    selected: Optional[StoredChatModel] = None
    if model_id is not None:
        normalized_id = _normalize_id_or_raise(model_id)
        selected = _get_store().get_model(normalized_id)
        if selected is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requested model is not registered.")
    else:
        defaults = [item for item in models if item.is_default]
        selected = defaults[0] if defaults else models[0]

    if not selected.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Requested model is inactive.")

    decrypted_api_key_items = _decrypt_api_keys_or_raise(selected.encrypted_api_key) if selected.encrypted_api_key else []
    decrypted_api_keys = _api_key_values(decrypted_api_key_items)
    if not decrypted_api_keys and selected.provider == "openai":
        decrypted_api_keys = _env_openai_api_keys()
    decrypted_api_key = decrypted_api_keys[0] if decrypted_api_keys else None

    resolved_client_options = dict(selected.client_options)
    if selected.encrypted_webhook_secret:
        resolved_client_options["webhook_secret"] = _decrypt_secret_or_raise(
            "webhook_secret",
            selected.encrypted_webhook_secret,
        )

    return ResolvedChatModel(
        model_id=selected.model_id,
        provider=selected.provider,
        openai_api=selected.openai_api,
        model=selected.model,
        display_name=selected.display_name,
        description=selected.description,
        parameters=dict(selected.parameters),
        client_options=resolved_client_options,
        chat_create_options=dict(selected.chat_create_options),
        responses_create_options=dict(selected.responses_create_options),
        api_key=decrypted_api_key,
        api_keys=decrypted_api_keys,
    )
