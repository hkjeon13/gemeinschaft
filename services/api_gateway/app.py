"""API gateway scaffold service."""

from services.shared.app_factory import build_service_app

app = build_service_app("api_gateway")
