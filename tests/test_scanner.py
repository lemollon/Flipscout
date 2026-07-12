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
        self.last_kw = None

    class _Comp:
        def __init__(self, sold): self.sold_price = sold; self.source = "ebay_insights"
    def lookup(self, query, observed_price=None):
        return self._Comp(self._sold)
    def active_listings(self, query, limit=50, **kw):
        self.last_kw = kw
        return self._listings


def test_scan_flags_underpriced_ranked_by_per_hour():
    src = _FakeSource(sold=200.0, listings=[
        {"title": "cheap DeWalt", "price": 60.0, "url": "u1"},   # big margin
        {"title": "fair DeWalt", "price": 150.0, "url": "u2"},   # thin -> below bar
        {"title": "mid DeWalt", "price": 100.0, "url": "u3"},    # decent
    ])
    hits = scan_query("dewalt", src, thresholds=Thresholds(min_profit=15, min_roi=0.5))
    # $200 sold nets ~173. buy 60 -> ~113 profit; buy 100 -> ~73.
    # buy 150 -> ~23 profit but ROI 15% < 50% -> excluded.
    assert [h.buy_price for h in hits] == [60.0, 100.0]     # ranked by $/hr (== profit order here)
    assert hits[0].per_hour > hits[1].per_hour
    assert hits[0].per_hour == pytest.approx(hits[0].profit * 60 / 20)  # shipped default


def test_local_flag_passes_through_and_changes_effort():
    src = _FakeSource(sold=200.0, listings=[{"title": "x", "price": 60.0, "url": ""}])
    hits = scan_query("q", src, local=True, zip_code="98101",
                      thresholds=Thresholds(min_profit=15, min_roi=0.5))
    assert src.last_kw == {"local": True, "zip_code": "98101"}   # forwarded to source
    assert hits[0].per_hour == pytest.approx(hits[0].profit * 60 / 45)  # local effort


def test_active_listings_local_sets_filter_and_location_header():
    from flipscout.ebay_api import EbayApiComps, EbayConfig
    captured = {}
    class _R:
        status_code = 200
        def __init__(self, body): self._b = body
        def json(self): return self._b
        def raise_for_status(self): pass
    class _Sess:
        def post(self, url, **kw): return _R({"access_token": "T", "expires_in": 7200})
        def get(self, url, **kw):
            captured.update(kw)
            return _R({"itemSummaries": [{"title": "x", "price": {"value": "10"}, "itemWebUrl": "u"}]})
    prov = EbayApiComps(cfg=EbayConfig(client_id="i", client_secret="s"), session=_Sess())
    prov.active_listings("drill", local=True, zip_code="98101")
    assert captured["params"]["filter"] == "deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP}"
    assert "zip=98101" in captured["headers"]["X-EBAY-C-ENDUSERCTX"]


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
