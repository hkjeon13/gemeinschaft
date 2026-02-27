"""Small FastAPI app factory used by all scaffold services."""

from fastapi import FastAPI


def build_service_app(service_name: str) -> FastAPI:
    app = FastAPI(title=f"{service_name} service", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready", "service": service_name}

    return app
