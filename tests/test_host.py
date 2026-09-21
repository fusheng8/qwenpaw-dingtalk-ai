import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qpai.routes import router


def test_authenticated_host_route_masks_secret_and_downloads_template(monkeypatch):
    from qwenpaw.app import agent_context
    config = NS(channels=NS(dingtalk_ai={"client_id": "test", "client_secret": "must-not-leak", "enabled": False}, dingtalk=None))
    monkeypatch.setattr(agent_context, "get_agent_for_request", AsyncMock(return_value=NS(config=config)))
    app = FastAPI(); app.include_router(router, prefix="/api/dingtalk-ai")
    with TestClient(app) as client:
        response = client.get("/api/dingtalk-ai/config")
        assert response.status_code == 200
        assert response.json()["has_secret"] is True
        assert "must-not-leak" not in response.text and "client_secret" not in response.text
        assert client.get("/api/dingtalk-ai/template").json()["type"] == "im"


@pytest.mark.asyncio
async def test_actual_qwenpaw_plugin_loader():
    from qwenpaw.plugins import PluginLoader, PluginManifest
    path = Path(__file__).resolve().parents[1]
    loader = PluginLoader([])
    app = FastAPI(); loader.registry.set_plugin_http_app(app)
    record = await loader.load_plugin(PluginManifest.model_validate(json.loads((path / "plugin.json").read_text())), path)
    assert record.manifest.id == "dingtalk-ai"
    with TestClient(app) as client:
        assert client.get("/api/dingtalk-ai/template").status_code == 200
