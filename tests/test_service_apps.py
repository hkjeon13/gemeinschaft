"""Smoke tests for scaffold service applications."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.agent_runtime.app import app as agent_runtime_app
from services.api_gateway.app import app as api_gateway_app
from services.conversation_orchestrator.app import app as orchestrator_app
from services.data_ingestion.app import app as data_ingestion_app
from services.export_service.app import app as export_service_app
from services.topic_pipeline.app import app as topic_pipeline_app

SERVICE_APPS: list[tuple[str, FastAPI]] = [
    ("api_gateway", api_gateway_app),
    ("conversation_orchestrator", orchestrator_app),
    ("agent_runtime", agent_runtime_app),
    ("data_ingestion", data_ingestion_app),
    ("topic_pipeline", topic_pipeline_app),
    ("export_service", export_service_app),
]


@pytest.mark.parametrize(("service_name", "app"), SERVICE_APPS)
def test_health_endpoint(service_name: str, app: FastAPI) -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": service_name}
