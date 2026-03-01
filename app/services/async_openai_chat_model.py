import os
import base64
import mimetypes
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from openai import AsyncOpenAI

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
_TEXT_CONTENT_TYPES = {"text", "input_text", "output_text"}
_IMAGE_CONTENT_TYPES = {"image_url", "input_image", "output_image"}


class AsyncOpenAIChatModel:
    def __init__(
        self,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        request_options: Optional[dict[str, Any]] = None,
        client_options: Optional[dict[str, Any]] = None,
        openai_api: str = "chat.completions",
        chat_create_options: Optional[dict[str, Any]] = None,
        responses_create_options: Optional[dict[str, Any]] = None,
        client: Optional[AsyncOpenAI] = None,
    ) -> None:
        resolved_api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        normalized_client_options = dict(client_options or {})
        if "api_key" in normalized_client_options:
            raise ValueError("Do not set api_key in client_options; use api_key field.")

        if client is None and not resolved_api_key:
            raise ValueError("OPENAI_API_KEY is required.")

        if client is None:
            openai_kwargs = dict(normalized_client_options)
            openai_kwargs["api_key"] = resolved_api_key
            try:
                self.client = AsyncOpenAI(**openai_kwargs)
            except TypeError as exc:
                raise ValueError(f"Invalid OpenAI client options: {exc}") from exc
        else:
            self.client = client

        normalized_api = openai_api.strip().lower()
        if normalized_api not in {"chat.completions", "responses"}:
            raise ValueError("openai_api must be one of: chat.completions, responses.")

        self.model = (model or os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")).strip()
        self.system_prompt = system_prompt.strip()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.openai_api = normalized_api
        self.request_options = dict(request_options or {})
        self.chat_create_options = self._validate_create_options(
            "chat_create_options",
            dict(chat_create_options or {}),
            allowed_keys=_OPENAI_CHAT_CREATE_ALLOWED_KEYS,
            reserved_keys=_OPENAI_CHAT_CREATE_RESERVED_KEYS,
        )
        self.responses_create_options = self._validate_create_options(
            "responses_create_options",
            dict(responses_create_options or {}),
            allowed_keys=_OPENAI_RESPONSES_CREATE_ALLOWED_KEYS,
            reserved_keys=_OPENAI_RESPONSES_CREATE_RESERVED_KEYS,
        )

    async def generate_messages(self, messages: list[dict[str, Any]]) -> str:
        normalized = self._normalize_messages(messages)
        if self.openai_api == "responses":
            return await self._generate_messages_responses(normalized)
        return await self._generate_messages_chat(normalized)

    async def stream_messages(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        normalized = self._normalize_messages(messages)
        if self.openai_api == "responses":
            async for chunk in self._stream_messages_responses(normalized):
                yield chunk
            return

        async for chunk in self._stream_messages_chat(normalized):
            yield chunk

    async def generate(self, user_input: str, *, system_prompt: Optional[str] = None) -> str:
        messages = self._build_messages(user_input=user_input, system_prompt=system_prompt)
        return await self.generate_messages(messages)

    async def stream(self, user_input: str, *, system_prompt: Optional[str] = None) -> AsyncIterator[str]:
        messages = self._build_messages(user_input=user_input, system_prompt=system_prompt)
        async for chunk in self.stream_messages(messages):
            yield chunk

    async def _generate_messages_chat(self, messages: list[dict[str, Any]]) -> str:
        payload = self._chat_payload(messages=messages, stream=False)
        completion = await self.client.chat.completions.create(**payload)
        if not completion.choices:
            return ""
        return self._to_text(completion.choices[0].message.content).strip()

    async def _stream_messages_chat(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        payload = self._chat_payload(messages=messages, stream=True)
        stream = await self.client.chat.completions.create(**payload)
        async for chunk in stream:
            if not chunk.choices:
                continue
            text = self._to_text(chunk.choices[0].delta.content)
            if text:
                yield text

    async def _generate_messages_responses(self, messages: list[dict[str, Any]]) -> str:
        payload = self._responses_payload(messages=messages, stream=False)
        response = await self.client.responses.create(**payload)
        return self._response_to_text(response).strip()

    async def _stream_messages_responses(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        payload = self._responses_payload(messages=messages, stream=True)
        stream = await self.client.responses.create(**payload)

        saw_delta = False
        completed_response: Any = None

        async for event in stream:
            event_type = self._event_field(event, "type")
            if event_type == "response.output_text.delta":
                delta = self._event_field(event, "delta")
                text = self._to_text(delta)
                if text:
                    saw_delta = True
                    yield text
                continue

            if event_type == "response.completed":
                completed_response = self._event_field(event, "response")

        if not saw_delta and completed_response is not None:
            fallback = self._response_to_text(completed_response)
            if fallback:
                yield fallback

    def _build_messages(self, *, user_input: str, system_prompt: Optional[str]) -> list[dict[str, Any]]:
        text = user_input.strip()
        if not text:
            raise ValueError("user_input must be a non-empty string.")

        resolved_system_prompt = self.system_prompt if system_prompt is None else system_prompt.strip()

        messages: list[dict[str, Any]] = []
        if resolved_system_prompt:
            messages.append({"role": "system", "content": [{"type": "input_text", "text": resolved_system_prompt}]})
        messages.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
        return messages

    def _normalize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        has_system = False
        has_developer = False
        has_user = False

        for item in messages:
            role = str(item.get("role", "")).strip().lower()
            if role not in {"system", "developer", "user", "assistant"}:
                continue

            content_blocks = self._normalize_content_blocks(item.get("content"), role=role)
            if not content_blocks:
                continue

            if role == "system":
                has_system = True
            if role == "developer":
                has_developer = True
            if role == "user":
                has_user = True

            normalized.append({"role": role, "content": content_blocks})

        if self.system_prompt and not (has_system or has_developer):
            normalized.insert(
                0,
                {"role": "system", "content": [{"type": "input_text", "text": self.system_prompt}]},
            )

        if not normalized:
            raise ValueError("messages must include at least one non-empty message.")
        if not has_user:
            raise ValueError("messages must include at least one user message.")
        return normalized

    def _chat_payload(self, *, messages: list[dict[str, Any]], stream: bool) -> dict[str, Any]:
        chat_messages = [self._message_to_chat_payload(item) for item in messages]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "stream": stream,
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens

        merged_options = self._merge_create_options(
            reserved_keys=_OPENAI_CHAT_CREATE_RESERVED_KEYS,
            method_options=self.chat_create_options,
        )
        payload.update(merged_options)
        return payload

    def _responses_payload(self, *, messages: list[dict[str, Any]], stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self._messages_to_responses_input(messages),
            "stream": stream,
        }

        merged_options = self._merge_create_options(
            reserved_keys=_OPENAI_RESPONSES_CREATE_RESERVED_KEYS,
            method_options=self.responses_create_options,
        )

        if "temperature" not in merged_options:
            merged_options["temperature"] = self.temperature
        if self.max_tokens is not None and "max_output_tokens" not in merged_options:
            merged_options["max_output_tokens"] = self.max_tokens

        payload.update(merged_options)
        return payload

    def _merge_create_options(self, *, reserved_keys: set[str], method_options: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for source in (self.request_options, method_options):
            for key, value in source.items():
                if key in reserved_keys:
                    continue
                merged[key] = value
        return merged

    def _messages_to_responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for item in messages:
            role = str(item["role"]).strip().lower()
            if role not in {"user", "assistant", "system", "developer"}:
                continue
            content = item["content"]
            if not isinstance(content, list):
                continue

            text_blocks: list[str] = []
            media_blocks: list[dict[str, Any]] = []
            for block in content:
                block_type = str(block.get("type", "")).strip().lower()
                if block_type in _TEXT_CONTENT_TYPES:
                    text = str(block.get("text", "")).strip()
                    if not text:
                        continue
                    text_blocks.append(text)
                    continue

                if block_type in _IMAGE_CONTENT_TYPES:
                    image_url = str(block.get("image_url", "")).strip()
                    if not image_url:
                        continue
                    media_blocks.append({"type": "input_image", "image_url": self._resolve_image_reference(image_url)})

            if not text_blocks and not media_blocks:
                continue

            if media_blocks:
                mixed_blocks: list[dict[str, Any]] = []
                for text in text_blocks:
                    mixed_blocks.append({"type": "input_text", "text": text})
                mixed_blocks.extend(media_blocks)
                payload.append({"type": "message", "role": role, "content": mixed_blocks})
                continue

            payload.append({"type": "message", "role": role, "content": "\n".join(text_blocks).strip()})
        return payload

    def _message_to_chat_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        role = str(item["role"]).strip().lower()
        chat_role = "system" if role == "developer" else role
        content = item["content"]
        if not isinstance(content, list):
            return {"role": chat_role, "content": ""}

        blocks: list[dict[str, Any]] = []
        for block in content:
            block_type = str(block.get("type", "")).strip().lower()
            if block_type in _TEXT_CONTENT_TYPES:
                text = str(block.get("text", "")).strip()
                if not text:
                    continue
                blocks.append({"type": "text", "text": text})
                continue

            if block_type in _IMAGE_CONTENT_TYPES:
                image_url = str(block.get("image_url", "")).strip()
                if not image_url:
                    continue
                blocks.append({"type": "image_url", "image_url": {"url": self._resolve_image_reference(image_url)}})

        if not blocks:
            return {"role": chat_role, "content": ""}
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            return {"role": chat_role, "content": str(blocks[0].get("text", ""))}
        return {"role": chat_role, "content": blocks}

    def _normalize_content_blocks(self, content: Any, *, role: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if isinstance(content, str):
            text = content.strip()
            if text:
                normalized.append({"type": "output_text" if role == "assistant" else "input_text", "text": text})
            return normalized

        if not isinstance(content, list):
            return normalized

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

    def _resolve_image_reference(self, image_url: str) -> str:
        candidate = image_url.strip()
        if not candidate:
            raise ValueError("image_url must be a non-empty string.")
        lowered = candidate.lower()
        if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("data:"):
            return candidate

        media_root = (os.getenv("CONVERSATION_MEDIA_ROOT", "") or "").strip()
        source = Path(candidate)
        if not source.is_absolute() and media_root:
            source = Path(media_root) / source
        source = source.expanduser().resolve()

        if not source.exists() or not source.is_file():
            raise ValueError(f"Image path does not exist: {candidate}")

        mime_type, _ = mimetypes.guess_type(str(source))
        mime = mime_type or "application/octet-stream"
        raw = source.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _response_to_text(self, response: Any) -> str:
        output_text = self._event_field(response, "output_text")
        text = self._to_text(output_text)
        if text:
            return text

        output_items = self._event_field(response, "output")
        if isinstance(output_items, list):
            parts: list[str] = []
            for item in output_items:
                content_blocks = self._event_field(item, "content")
                if isinstance(content_blocks, list):
                    for block in content_blocks:
                        block_text = self._event_field(block, "text")
                        text_piece = self._to_text(block_text)
                        if text_piece:
                            parts.append(text_piece)
                else:
                    item_text = self._event_field(item, "text")
                    text_piece = self._to_text(item_text)
                    if text_piece:
                        parts.append(text_piece)
            return "".join(parts)

        return ""

    def _validate_create_options(
        self,
        field_name: str,
        options: dict[str, Any],
        *,
        allowed_keys: set[str],
        reserved_keys: set[str],
    ) -> dict[str, Any]:
        reserved_used: list[str] = []
        unknown_keys: list[str] = []

        for key in options.keys():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{field_name} keys must be non-empty strings.")
            if key in reserved_keys:
                reserved_used.append(key)
                continue
            if key not in allowed_keys:
                unknown_keys.append(key)

        if reserved_used:
            names = ", ".join(sorted(reserved_used))
            raise ValueError(f"{field_name} includes reserved key(s): {names}")
        if unknown_keys:
            names = ", ".join(sorted(unknown_keys))
            raise ValueError(f"{field_name} includes unsupported key(s): {names}")

        return dict(options)

    def _event_field(self, event: Any, key: str) -> Any:
        if isinstance(event, dict):
            return event.get(key)
        return getattr(event, key, None)

    def _to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts)
        return str(content)
