import json
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from .api import router
from .services.auth import validate_auth_settings
from .services.authorization import validate_authorization_settings
from .services.chat_model_registry import initialize_chat_model_registry
from .services.conversation_store import (
    shutdown_conversation_store,
    start_conversation_store_background_tasks,
)
from .services.database import validate_database_settings
from .services.security_state import initialize_security_state

__version__ = os.environ.get("VERSION", "0.0.0")
app = FastAPI(
    title=os.getenv("ENV_APP_NAME", "AI Society"),
    version=__version__,
    docs_url=None,
    swagger_ui_parameters={"persistAuthorization": True},
)


def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("AUTH_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return []
    return [origin.strip().lower().rstrip("/") for origin in raw.split(",") if origin.strip()]


def _parse_allowed_origin_regex() -> str:
    return os.getenv("AUTH_ALLOWED_ORIGIN_REGEX", "").strip()


cors_allowed_origins = _parse_allowed_origins()
cors_allowed_origin_regex = _parse_allowed_origin_regex() or None
if "*" in cors_allowed_origins:
    cors_allowed_origins = [origin for origin in cors_allowed_origins if origin != "*"]
    cors_allowed_origin_regex = ".*"

if cors_allowed_origins or cors_allowed_origin_regex:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins,
        allow_origin_regex=cors_allowed_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(router)


@app.on_event("startup")
async def startup_validate_auth_settings() -> None:
    logging.getLogger("security.audit").setLevel(logging.INFO)
    validate_auth_settings()
    validate_authorization_settings()
    validate_database_settings()
    initialize_chat_model_registry()
    start_conversation_store_background_tasks()
    initialize_security_state()


@app.on_event("shutdown")
async def shutdown_conversation_background_tasks() -> None:
    shutdown_conversation_store()


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui() -> HTMLResponse:
    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_ui_parameters={"persistAuthorization": True},
    )

    dev_jwt = os.getenv("SWAGGER_DEV_JWT")
    if not dev_jwt:
        return html

    script = f"""
<script>
window.addEventListener("load", function() {{
  if (window.ui) {{
    window.ui.preauthorizeApiKey("HTTPBearer", {json.dumps(dev_jwt)});
  }}
}});
</script>
"""
    patched = html.body.decode("utf-8").replace("</body>", script + "</body>")
    return HTMLResponse(content=patched, status_code=html.status_code)
