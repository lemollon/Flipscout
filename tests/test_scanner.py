"""Tests for the arbitrage scanner: the Browse-items parser, the scan engine
(with a fake source), and the /api/deals endpoint."""

import pytest

from flipscout.ebay_api import parse_browse_items
from flipscout.scanner import scan, scan_query
from flipscout.analyzer import Thresholds


def test_parse_browse_items():
    body = {"itemSummaries": [
        {"title": "DeWalt drill", "price": {"value": "40.00"}, "itemWebUrl": "http://x/1"},
        {"title": "no price"},
        {"title": "Makita", "price": {"value": "bad"}},
    ]}
    items = parse_browse_items(body)
    assert len(items) == 1
    assert items[0] == {"title": "DeWalt drill", "price": 40.0, "url": "http://x/1", "item_id": ""}


class _FakeSource:
    """A source with a fixed sold price and a set of active listings."""
    def __init__(self, sold, listings):
        self._sold = sold
        self._listings = listings

    class _Comp:
        def __init__(self, sold): self.sold_price = sold; self.source = "ebay_insights"
    def lookup(self, query, observed_price=None):
        return self._Comp(self._sold)
    def active_listings(self, query, limit=50):
        return self._listings


def test_scan_flags_underpriced_ranked_by_profit():
    src = _FakeSource(sold=200.0, listings=[
        {"title": "cheap DeWalt", "price": 60.0, "url": "u1"},   # big margin
        {"title": "fair DeWalt", "price": 150.0, "url": "u2"},   # thin -> below bar
        {"title": "mid DeWalt", "price": 100.0, "url": "u3"},    # decent
    ])
    hits = scan_query("dewalt", src, thresholds=Thresholds(min_profit=15, min_roi=0.5))
    # $200 sold nets ~200 - 26.9 fees = ~173. buy 60 -> ~113 profit; buy 100 -> ~73.
    # buy 150 -> ~23 profit but ROI 15% < 50% -> excluded.
    assert [h.buy_price for h in hits] == [60.0, 100.0]     # ranked by profit desc
    assert hits[0].profit > hits[1].profit
    assert hits[0].roi > 0.5


def test_scan_no_sold_data_returns_empty():
    src = _FakeSource(sold=0, listings=[{"title": "x", "price": 10.0, "url": ""}])
    assert scan_query("x", src) == []


def test_scan_merges_and_ranks_across_queries():
    src = _FakeSource(sold=200.0, listings=[{"title": "a", "price": 50.0, "url": ""}])
    hits = scan(["q1", "q2"], src, thresholds=Thresholds(min_profit=15, min_roi=0.5))
    assert len(hits) == 2  # one hit per query


# --- /api/deals endpoint ----------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
import flipscout.server as server  # noqa: E402


@pytest.fixture
def client():
    return TestClient(server.app)


def test_deals_endpoint(client, monkeypatch):
    monkeypatch.setattr(server, "_provider", _FakeSource(sold=200.0, listings=[
        {"title": "cheap DeWalt", "price": 60.0, "url": "u1"}]))
    r = client.get("/api/deals", params={"q": "dewalt drill"})
    assert r.status_code == 200
    deals = r.json()["deals"]
    assert len(deals) == 1
    assert deals[0]["buy_price"] == 60.0 and deals[0]["profit"] > 15


def test_deals_503_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(server, "_provider", None)
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    r = client.get("/api/deals", params={"q": "x"})
    assert r.status_code == 503
