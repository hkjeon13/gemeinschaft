import os
import json
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from .api import router
__version__ = os.environ.get("VERSION", "0.0.0")
app = FastAPI(
    title=os.getenv("ENV_APP_NAME", "AI Society"),
    version=__version__,
    docs_url=None,
    swagger_ui_parameters={"persistAuthorization": True},
)
app.include_router(router)


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



