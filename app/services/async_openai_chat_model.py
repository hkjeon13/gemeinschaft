import os
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

    async def generate_messages(self, messages: list[dict[str, str]]) -> str:
        normalized = self._normalize_messages(messages)
        if self.openai_api == "responses":
            return await self._generate_messages_responses(normalized)
        return await self._generate_messages_chat(normalized)

    async def stream_messages(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
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

    async def _generate_messages_chat(self, messages: list[dict[str, str]]) -> str:
        payload = self._chat_payload(messages=messages, stream=False)
        completion = await self.client.chat.completions.create(**payload)
        if not completion.choices:
            return ""
        return self._to_text(completion.choices[0].message.content).strip()

    async def _stream_messages_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        payload = self._chat_payload(messages=messages, stream=True)
        stream = await self.client.chat.completions.create(**payload)
        async for chunk in stream:
            if not chunk.choices:
                continue
            text = self._to_text(chunk.choices[0].delta.content)
            if text:
                yield text

    async def _generate_messages_responses(self, messages: list[dict[str, str]]) -> str:
        payload = self._responses_payload(messages=messages, stream=False)
        response = await self.client.responses.create(**payload)
        return self._response_to_text(response).strip()

    async def _stream_messages_responses(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
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

    def _build_messages(self, *, user_input: str, system_prompt: Optional[str]) -> list[dict[str, str]]:
        text = user_input.strip()
        if not text:
            raise ValueError("user_input must be a non-empty string.")

        resolved_system_prompt = self.system_prompt if system_prompt is None else system_prompt.strip()

        messages: list[dict[str, str]] = []
        if resolved_system_prompt:
            messages.append({"role": "system", "content": resolved_system_prompt})
        messages.append({"role": "user", "content": text})
        return messages

    def _normalize_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        has_system = False
        has_user = False

        for item in messages:
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", "")).strip()

            if role not in {"system", "user", "assistant"}:
                continue
            if not content:
                continue

            if role == "system":
                has_system = True
            if role == "user":
                has_user = True

            normalized.append({"role": role, "content": content})

        if self.system_prompt and not has_system:
            normalized.insert(0, {"role": "system", "content": self.system_prompt})

        if not normalized:
            raise ValueError("messages must include at least one non-empty message.")
        if not has_user:
            raise ValueError("messages must include at least one user message.")
        return normalized

    def _chat_payload(self, *, messages: list[dict[str, str]], stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
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

    def _responses_payload(self, *, messages: list[dict[str, str]], stream: bool) -> dict[str, Any]:
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

    def _messages_to_responses_input(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        for item in messages:
            payload.append({"role": item["role"], "content": item["content"]})
        return payload

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
