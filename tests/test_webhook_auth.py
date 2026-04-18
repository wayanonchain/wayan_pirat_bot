"""Verify /webhook/helius honours HELIUS_WEBHOOK_AUTH."""

import pytest
from fastapi.testclient import TestClient


def _reload_server_module(monkeypatch, auth_value: str):
    """Re-import webhook.server so the ``HELIUS_WEBHOOK_AUTH`` snapshot it
    captured at import time picks up our override."""
    import importlib

    from config import settings
    monkeypatch.setattr(settings, "HELIUS_WEBHOOK_AUTH", auth_value)

    import webhook.server as server
    importlib.reload(server)
    return server


def test_webhook_rejects_missing_auth(monkeypatch):
    server = _reload_server_module(monkeypatch, "secret-token")
    client = TestClient(server.app)

    resp = client.post("/webhook/helius", json=[])
    assert resp.status_code == 401


def test_webhook_rejects_wrong_auth(monkeypatch):
    server = _reload_server_module(monkeypatch, "secret-token")
    client = TestClient(server.app)

    resp = client.post(
        "/webhook/helius",
        headers={"Authorization": "wrong-value"},
        json=[],
    )
    assert resp.status_code == 401


def test_webhook_accepts_correct_auth(monkeypatch):
    server = _reload_server_module(monkeypatch, "secret-token")
    # Skip the loader so we don't touch the DB.
    monkeypatch.setattr(server, "load_monitored_addresses", _noop)
    monkeypatch.setattr(server, "load_tracked_tokens", _sync_noop)

    client = TestClient(server.app)
    resp = client.post(
        "/webhook/helius",
        headers={"Authorization": "secret-token"},
        json=[],
    )
    assert resp.status_code == 200
    assert resp.json() == {"processed": 0}


def test_webhook_open_when_auth_empty(monkeypatch):
    server = _reload_server_module(monkeypatch, "")
    monkeypatch.setattr(server, "load_monitored_addresses", _noop)
    monkeypatch.setattr(server, "load_tracked_tokens", _sync_noop)

    client = TestClient(server.app)
    resp = client.post("/webhook/helius", json=[])
    assert resp.status_code == 200


async def _noop():  # pragma: no cover
    return None


def _sync_noop():
    return None
