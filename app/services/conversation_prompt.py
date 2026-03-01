import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - optional in local shells
    yaml = None

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "You are one participant in a multi-model conversation. "
    "Answer naturally, and if needed ask a related follow-up question."
)
_DEFAULT_PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "conversation_developer_prompt.yaml"
_PROMPT_PATH_ENV = "CONVERSATION_DEVELOPER_PROMPT_FILE"

_cache_lock = Lock()
_cached_path: str | None = None
_cached_mtime: float | None = None
_cached_prompt: str | None = None


def _resolve_prompt_file() -> Path:
    raw = os.getenv(_PROMPT_PATH_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_PROMPT_FILE


def _parse_prompt_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Prompt YAML root must be a mapping.")
    prompt = payload.get("developer_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt YAML must include non-empty `developer_prompt`.")
    return prompt.strip()


def _parse_prompt_yaml_without_pyyaml(raw: str) -> str:
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if not stripped.startswith("developer_prompt:"):
            continue

        base_indent = len(line) - len(stripped)
        tail = stripped[len("developer_prompt:") :].strip()
        if tail and not tail.startswith("|"):
            inline = tail.strip().strip("'\"")
            if inline:
                return inline

        block_lines: list[str] = []
        content_indent: int | None = None
        for next_line in lines[index + 1 :]:
            if not next_line.strip():
                block_lines.append("")
                continue
            next_indent = len(next_line) - len(next_line.lstrip(" "))
            if next_indent <= base_indent:
                break
            if content_indent is None:
                content_indent = next_indent
            cut = content_indent if content_indent is not None else 0
            block_lines.append(next_line[cut:])

        candidate = "\n".join(block_lines).strip()
        if candidate:
            return candidate

    raise ValueError("Prompt YAML must include non-empty `developer_prompt`.")


def _load_prompt_uncached(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if yaml is None:
        return _parse_prompt_yaml_without_pyyaml(raw)
    parsed = yaml.safe_load(raw)
    return _parse_prompt_payload(parsed)


def get_conversation_developer_prompt_template() -> str:
    global _cached_path, _cached_mtime, _cached_prompt

    path = _resolve_prompt_file()
    try:
        stat = path.stat()
    except FileNotFoundError:
        logger.warning("Conversation developer prompt file not found: %s; using fallback prompt.", path)
        return _DEFAULT_PROMPT
    except Exception:
        logger.exception("Failed to stat conversation developer prompt file: %s", path)
        return _DEFAULT_PROMPT

    path_key = str(path)
    mtime = float(stat.st_mtime)

    with _cache_lock:
        if _cached_path == path_key and _cached_mtime == mtime and _cached_prompt:
            return _cached_prompt

    try:
        loaded = _load_prompt_uncached(path)
    except Exception:
        logger.exception("Failed to load conversation developer prompt from YAML: %s", path)
        return _DEFAULT_PROMPT

    with _cache_lock:
        _cached_path = path_key
        _cached_mtime = mtime
        _cached_prompt = loaded
    return loaded


def render_conversation_developer_prompt(*, selected_model_id: str, user_id: str) -> str:
    template = get_conversation_developer_prompt_template()
    try:
        rendered = template.format(
            selected_model_id=selected_model_id,
            user_id=user_id,
        )
    except Exception:
        logger.exception("Failed to render conversation developer prompt template; using raw template.")
        rendered = template
    return rendered.strip()
