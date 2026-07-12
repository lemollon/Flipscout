"""Tests for screenshot scanning: the OCR/paste text parser (pure Python, no
network) and the /api/scan endpoint (with a fake extractor injected)."""

import pytest

from flipscout.scan import parse_listing_text


# --- parser (mirrors the web app's parseListing) ----------------------------

@pytest.mark.parametrize("text,name,price", [
    ("DeWalt 20V Drill\n$60\nUsed · Seattle, WA", "DeWalt 20V Drill", 60.0),
    ("$1,250\nPeloton Bike\nGood condition", "Peloton Bike", 1250.0),
    ("Marketplace - Vintage Pyrex Set | Facebook\n$45", "Vintage Pyrex Set", 45.0),
    ("Used · Milwaukee M18 combo kit\n$220.50\n3 mi", "Milwaukee M18 combo kit", 220.5),
    ("Free · Old couch\nPickup only", "Old couch", None),
    ("just a note no price here", "just a note no price here", None),
])
def test_parse_listing_text(text, name, price):
    got = parse_listing_text(text)
    assert got["name"] == name
    assert got["price"] == price


def test_parse_empty():
    assert parse_listing_text("") == {"name": "", "price": None}


# --- /api/scan endpoint -----------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
import flipscout.server as server  # noqa: E402


class _FakeScanner:
    def extract(self, image_bytes, mime="image/png"):
        assert image_bytes  # got the bytes
        return {"name": "DeWalt DCD771 drill", "price": 55.0,
                "condition": "Used", "source": "claude_vision"}


@pytest.fixture
def client():
    return TestClient(server.app)


def test_scan_returns_fields(client, monkeypatch):
    monkeypatch.setattr(server, "_scanner", _FakeScanner())
    r = client.post("/api/scan", files={"image": ("shot.png", b"\x89PNG...", "image/png")})
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "DeWalt DCD771 drill" and d["price"] == 55.0
    assert d["condition"] == "Used" and d["source"] == "claude_vision"


def test_scan_empty_image_400(client, monkeypatch):
    monkeypatch.setattr(server, "_scanner", _FakeScanner())
    r = client.post("/api/scan", files={"image": ("shot.png", b"", "image/png")})
    assert r.status_code == 400


def test_scan_503_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(server, "_scanner", None)
    import flipscout.scan as scan
    def _raise():
        raise RuntimeError("Screenshot scanning isn't set up.")
    monkeypatch.setattr(scan, "get_extractor", _raise)
    r = client.post("/api/scan", files={"image": ("shot.png", b"data", "image/png")})
    assert r.status_code == 503
    assert "isn't set up" in r.json()["detail"]
