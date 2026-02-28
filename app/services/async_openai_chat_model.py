import os
from typing import Any, AsyncIterator, Optional

from openai import AsyncOpenAI


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
        client: Optional[AsyncOpenAI] = None,
    ) -> None:
        resolved_api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        if client is None and not resolved_api_key:
            raise ValueError("OPENAI_API_KEY is required.")

        self.client = client or AsyncOpenAI(api_key=resolved_api_key)
        self.model = (model or os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")).strip()
        self.system_prompt = system_prompt.strip()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.request_options = dict(request_options or {})

    async def generate_messages(self, messages: list[dict[str, str]]) -> str:
        normalized = self._normalize_messages(messages)
        payload = self._chat_payload(messages=normalized, stream=False)
        completion = await self.client.chat.completions.create(**payload)
        if not completion.choices:
            return ""
        return self._to_text(completion.choices[0].message.content).strip()

    async def stream_messages(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        normalized = self._normalize_messages(messages)
        payload = self._chat_payload(messages=normalized, stream=True)
        stream = await self.client.chat.completions.create(**payload)
        async for chunk in stream:
            if not chunk.choices:
                continue
            text = self._to_text(chunk.choices[0].delta.content)
            if text:
                yield text

    async def generate(self, user_input: str, *, system_prompt: Optional[str] = None) -> str:
        messages = self._build_messages(user_input=user_input, system_prompt=system_prompt)
        return await self.generate_messages(messages)

    async def stream(self, user_input: str, *, system_prompt: Optional[str] = None) -> AsyncIterator[str]:
        messages = self._build_messages(user_input=user_input, system_prompt=system_prompt)
        async for chunk in self.stream_messages(messages):
            yield chunk

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
        for key, value in self.request_options.items():
            if key in {"model", "messages", "stream"}:
                continue
            payload[key] = value
        return payload

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
