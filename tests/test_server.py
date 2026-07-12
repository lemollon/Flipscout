"""Tests for the web backend (flipscout.server). No network: a fake comps provider
stands in for the eBay API, exactly like tests/test_flipscout.py does."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import flipscout.server as server
from flipscout.comps import Comp


class _FakeProvider:
    def lookup(self, q, observed_price=None):
        return Comp(query=q, sold_price=250.0, sold_count=800, active_count=400,
                    source="ebay_insights", low=210.0, high=290.0)


@pytest.fixture
def client():
    return TestClient(server.app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert set(r.json()) >= {"ok", "ebay_configured"}


def test_comps_503_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(server, "_provider", None)
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    r = client.get("/api/comps", params={"q": "anything"})
    assert r.status_code == 503
    assert "sold price" in r.json()["detail"].lower()


def test_comps_returns_provider_data(client, monkeypatch):
    monkeypatch.setattr(server, "_provider", _FakeProvider())
    r = client.get("/api/comps", params={"q": "Nintendo Switch OLED"})
    assert r.status_code == 200
    d = r.json()
    assert d["sold_price"] == 250.0
    assert d["sold_count"] == 800 and d["active_count"] == 400
    assert d["source"] == "ebay_insights"


def test_comps_requires_query(client):
    assert client.get("/api/comps").status_code == 422  # missing q


def test_app_serves_web_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Flip" in r.text  # the web app's wordmark
